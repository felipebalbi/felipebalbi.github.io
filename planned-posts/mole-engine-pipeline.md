+++
title = "Inside mole's engine, part 1: pipeline and plumbing"
date = 2026-08-17T09:00:00
description = "Why mole's HDL is in SpinalHDL, what the top-level FSM gates, how a program gets from UART into SPRAM, the 5-stage pipeline (with honest accounting of F1/F2 making it 6), and why the loop idiom is hazard-free by construction. The bit-cycle engine itself is the next post."
[taxonomies]
tags = ["embedded", "fpga", "spinalhdl", "mole", "hdl"]
+++

Mole's HDL is in [SpinalHDL](https://spinalhdl.github.io/SpinalDoc-RTD/).
When I started this project I assumed I'd write Verilog: it's what
the toolchain — yosys, nextpnr, icepack — actually consumes, and
"the tools eat what you write" is a strong default. I changed my
mind on the second time I caught myself hand-tracking which width
a signal was supposed to be on either side of a wire. SpinalHDL
is a Scala DSL that elaborates to Verilog; the Verilog under
`gen/MoleTop.v` is what reaches the toolchain, and SpinalHDL is
what reaches me.

This post takes the engine apart from the UART loader at the
inflow to the result-ring writer at the outflow. The 3-state
top-level FSM that gates everything. How a program gets from UART
into SPRAM. The five (six) pipeline stages and what each one is
responsible for. Why the loop idiom that the ISA is designed
around is hazard-free without any special hardware. And the
result ring at the outflow, briefly. What this post does *not*
cover is what the engine actually does to SDA and SCL on the
wire — quarter-bit timing, the SCL waveform generator, the bus
observer, what `EMIT_BIT_IMM` physically does. That's post 5.

## Why SpinalHDL

Three concrete things SpinalHDL bought me.

**It's just Scala.** The build is sbt; the tests are scalatest plus
a Spinal sim harness. There is no bespoke HDL build system on top
of a bespoke HDL. Mole's `MoleTopVerilog` is a Scala entry point;
`make gen` runs it; out comes `gen/MoleTop.v`. The IDE, the
formatter, the test runner are all things I already had.

**Type-safe signal composition.** `BusMode` and `EngineRole` are
real Scala enums at elaboration time. Confusing a `UInt(4 bits)`
and a `Bits(4 bits)` is a compile error, not a silently-wrong
synthesis. The instruction word's bit layout —
`{group[31:30], sub[29:26]}` for the opcode field, flag triple at
`[2:0]` — is described in a single `Instruction.scala` whose
`encode`/`decode` pair doubles as the Scala-side oracle for the
host Rust encoder's golden cross-checks. A reserved bit pattern
is a `case ReservedV05` carrier in a sealed trait, not a comment
saying "don't use this."

**`Component` is real.** Mole's `EnginePipeline`,
`SpramController`, `BusObserver`, `MoleLoaderFsm`,
`MoleDrainerFsm` are independent SpinalHDL `Component`s with
explicit IO bundles. The top-level wires them together; the
simulator can swap in Verilator-friendly models of the SPRAM
tiles, the PLL, or the iCE40 `SB_IO` pads by flipping
`useBlackBox = false`; the synthesis flow doesn't care which
side it gets. There's no module-plus-testbench-plus-wrapper
triplicate to keep in sync. The same `Component` is the
synthesis input, the simulation DUT, and the documentation
anchor.

Frame this as a choice about reading the design as much as
writing it. The generated Verilog is what the toolchain consumes,
but the SpinalHDL source is what a human can hold in their head.

## The top-level FSM

`MoleTop` has a 3-state phase FSM:

```scala
val phase = new StateMachine {
  val acceptLoadState: State = new State with EntryPoint {
    whenIsActive {
      acceptRxComb := True
      when(loader.io.loaded) {
        engineStarted := False
        goto(runningState)
      }
    }
  }

  val runningState: State = new State {
    whenIsActive {
      engineStartDrv := True
      when(!engineStarted) { engineStarted := True }
      when(engineStarted && pipeline.io.halted) {
        drainTriggerComb := True
        goto(drainingState)
      }
    }
  }

  val drainingState: State = new State {
    whenIsActive {
      when(drainer.io.drainComplete) {
        goto(acceptLoadState)
      }
    }
  }
}
```

That's the whole top-level controller. One state per phase, one
cycle per phase transition, one signal per gated subsystem.
`acceptRxComb` opens the loader's UART RX path; `engineStartDrv`
holds the engine's fetch enable; `drainTriggerComb` pulses the
drainer once. The combinational defaults at the top of the file
mean any signal not asserted in a state is off — the FSM is
exhaustive and the gates are mutually exclusive.

`io_uCts := !acceptRxComb` — the wire that tells the host's
FT2232H to stop sending — falls out of the same flag. When the
engine is running or draining, CTS# is deasserted; the host with
`crtscts` enabled holds its TX off. The invariant *"while the
program isn't HALTED, don't accept data"* is a single inverted
wire on the gate signal, not a separate piece of policy.

Per-program reset is what makes this comfortable to live with.
On `loader.io.loaded` the FSM goes to `runningState` and clears
`engineStarted`; the rising edge of `engineStart` inside the
engine clears `haltedReg`, `revisionPending`, `ringWrPtr`,
`ringOverflow`, the sticky flag regs, and `pcReg`. Back-to-back
program loads run without an FPGA power cycle.

## How a program gets into SPRAM

The loader path is `MoleLoaderFsm`. The host sends a little-endian
byte stream: a 4-byte magic word (`0x0002_4D4C`), a 4-byte body
length in 32-bit words, the body itself (one to N 32-bit
instruction words), and a 2-byte CRC-16/XMODEM trailer computed
over magic + length + body. The full grammar lives in
[`WIRE_FORMAT.md`](https://github.com/felipebalbi/mole/blob/main/fpga/Mole/WIRE_FORMAT.md);
the loader FSM walks it byte by byte, latching one byte per
cycle, feeding each into the CRC accumulator, and emitting one
32-bit write to SPRAM per four body bytes.

The states are a flat list: `idle` → four `magic` states → four
`len` states → four `word` states → one `writeWord` state → two
`crc` states → back to `idle`, with a `resync` state hanging off
the side that any error falls into. Every byte-consuming state
has the same `when(abortNow)` arm that pulses `fault` and jumps
to `resync`, and `resync` doesn't return to `idle` until the raw
RX line has been continuously high for 20 UART bit times. That
gap is the host's recovery point after a malformed frame.

Hardware flow control is mandatory on this link. The first bench
session with v0.2 silicon turned up a bug here: the host's serial
port wasn't actually asserting `crtscts`, so when the engine
deasserted CTS# during a run, the host kept pushing bytes into
the FT2232H's USB pipe. The bytes vanished at the FT, not at the
FPGA, which made the failure look like the loader was dropping
frames. The fix lives on both sides — the FPGA loader gained an
`acceptRx`-drop catch-all that faults mid-frame if the gate
drops, and the host CLI now sets `crtscts` on the serial port
builder automatically. Bench bugs find architecturally
interesting things, sometimes.

## The 5-stage pipeline

`EnginePipeline.scala` defines the pipeline in one line:

```scala
val f1, f2, d, r, x, w = CtrlLink()
```

Six stage names. The README and the spec both call this a
"5-stage F1 / F2 / D / R / X / W" pipeline, which looks wrong until
you remember that F1 and F2 together are the two-cycle fetch — F1
issues the SPRAM read request, F2 catches the response one cycle
later, because that's the SPRAM tile's read latency. From the
ISA's point of view the fetch is one logical stage that happens to
take two clocks. From the layout's point of view it's two stages.
Both numbers are correct. I'll call it a 5-stage pipeline because
that's what the rest of the documentation calls it, but it's worth
knowing that F1/F2 is a fetch that you can't collapse, not a
two-instruction-deep window of any other shape.

The stage responsibilities, top to bottom:

- **F1** — Address calculation. `pcReg` is offered to the SPRAM
  read port, and `pcReg + 1` is latched on the cycle the read
  actually fires. The valid is gated on `f1.down.isFiring`, not
  a free-running fetch enable, so a flushed-and-refetched read
  never goes out with a stale PC.
- **F2** — SPRAM data registered. The SPRAM controller delivers
  the read response exactly one cycle after the read fires; F2
  catches it into `f2InstrReg` and gates downstream on the latch
  being valid. F1 + F2 is the two-cycle fetch.
- **D** — Decode. The 32-bit instruction is split into
  `group[31:30]` and `sub[29:26]`, the operand domains are
  extracted as separate payloads (`BRANCH_OFFSET`,
  `WAIT_TIMEOUT`, `MARK_LABEL`, `LOAD_TIMING_DIVIDER`, the DATA
  immediates), and the per-opcode boolean predicates
  (`IS_HALT`, `IS_BRANCH_ON`, `IS_DATA`, …) are computed for
  the X stage to dispatch on.
- **R** — Register file read. Two combinational ports on
  `RegFile`, one for the A operand and one for the B operand
  (R7, used by `EMIT_BYTE_REG`). The W→R bypass mux on each
  port is a single comparator plus a 2-input mux; when the W
  stage writes the same register R is reading, the mux forwards
  the W-stage value combinationally with zero stall. Immediate
  operands skip the register file entirely and ride through R
  as decoded payloads.
- **X** — Execute. The actual bus action lives here.
  Quarter-bit timing, SCL waveform generation, sticky-flag
  updates against the bus observer — all of that, plus how the
  26 opcodes dispatch inside this stage, is what post 5 is
  about. From this post's point of view, X is "where the
  instruction does its thing"; it exports `HALT_REQUEST`,
  `RING_WRITE_VALID`, `PC_REDIRECT_VALID`, and `WRITES_REG` as
  the four observable consequences of running an opcode.
- **W** — Writeback. The destination register commits to
  `RegFile` here, sticky flags settle into their architectural
  regs (`mismatchFlagReg`, `regZeroFlagReg`, the start/stop
  edge detectors), and the result-ring writer emits a HALT,
  CAPTURE, or MARK record into SPRAM if X asked for one. `pcReg`
  also writes back here on a taken `BRANCH_ON`.

The bypass network in the spec is "full W-to-R": the in-flight
window where a producer is in X or W and a consumer is in R is
covered combinationally, no stall. There is one stall case: the
load-use pair, where a `LOAD_IMM Rd` is followed in the very
next slot by an opcode that reads `Rd`. That pair gets a
hardware-inserted one-cycle bubble. Everything else flows.

## Hazard-free by construction

The loop idiom — `DEC Rx`, `BRANCH_ON NOT_REG_ZERO`, back to the
top — is the canonical bounded loop in mole's ISA, and it is
hazard-free by design. `DEC` writes `REG_ZERO_FLAG` at the W
stage. `BRANCH_ON` reads it at D. By the time the `BRANCH_ON`
that immediately follows the `DEC` reaches D, the flag is
already in the architectural register, because `DEC` is one
stage further down the pipeline; the spec calls this
**zero-cycle visibility** for `REG_ZERO_FLAG`, `MISMATCH_FLAG`,
`TIMEOUT_FLAG`, `START_FLAG`, and `STOP_FLAG`. No bypass mux is
needed for that pair, no stall is inserted, and the back-to-back
`DEC; BRANCH_ON` is a single instruction's latency through the
pipeline.

This is not the hardware being clever; it is the ISA being
designed so the hardware doesn't have to be. The flag-writing
opcodes commit at W; the flag-consuming opcodes read at D; the
stages are physically far enough apart that back-to-back
placement of any flag writer and any flag reader just works.
The assembler-level lint (`LINT-001`) warns when an *interleaved*
ALU op gets inserted between `DEC` and its paired branch,
because the flag would be overwritten by the interleaved op
before the branch read it. That warning is the structure of the
hardware made visible at the source level.

The cost saving is real but not the point. What this design
choice buys is reasoning: I do not need to think about hazard
resolution for the loop idiom, because there is nothing to
resolve. The pipeline implements an ISA that was specified not
to need hazard logic on its hot path; the hot path therefore has
none.

## The result ring, briefly

Engine outflow is a 32-bit-grain ring at the top of SPRAM. Records
are one of four shapes, all tagged in the top bits of the first
word:

- **REVISION** — emitted once at cold start. Top 8 bits are
  the major revision, next 8 are minor, low 16 are patch. The
  host sees this in the first 4 bytes of every drain, and uses
  it to confirm what bitstream it's actually talking to. The
  emission is gated by a `revisionPending` register that
  clears the cycle the REVISION word commits, so the engine
  cannot start fetching instructions before the host can see
  the revision at the head of the ring.
- **CAPTURE** — written when the bus observer captures
  something the program asked for. Post 5 explains what's in
  these.
- **MARK** — written when the program executes a `MARK`
  opcode. Three words: header, low timestamp, high timestamp.
  Programmer-inserted breadcrumbs.
- **HALT** — written when X commits a `HALT` or trap. Slot
  reserved at `resultLimit`, with the layout
  `tag[31:30]=11 | overflow[29] | mismatch[28] | status[27:23] |
  reserved[22:0]`. The drainer knows where to stop because the
  HALT slot is always at the same offset.

When the engine halts, `MoleDrainerFsm` takes over the SPRAM read
port (the top-level mux gives it to the drainer for the duration
of `drainingState`) and sweeps the ring back over UART TX, four
bytes per word, little-endian. On the last byte of the last word
it pulses `drainComplete`, the phase FSM returns to
`acceptLoadState`, and CTS# reasserts.

Bytecode in, structured records out. The bus-observer side of
this — what a `CAPTURE` record actually contains, how the engine
knows when to write one, what the bit observer is doing while
the X stage runs — is the next post.

## What this post didn't cover

The actual bit-cycle engine: quarter-bit timing, the SCL waveform
generator that turns "I want a falling edge of SCL three
quarter-bits from now" into an SB_IO toggle, the bus observer
that watches SDA and SCL for events the program is parked on,
what `EMIT_BIT_IMM` *physically does* to the bus on the wire,
and how the engine's controller and target roles share a single
bitstream.

That's post 5. This one stops at the boundary of the bit-cycle
execution itself — the part of the engine where the X stage hands
off to the wire — because crossing it without first being clear
on the pipeline that gets you there would lose the structure
that makes the wire-level behaviour interesting.

The source is at
[`fpga/Mole/src/hw/`](https://github.com/felipebalbi/mole/tree/main/fpga/Mole/src/hw)
if you want to read along.
