+++
title = "Tamal: an eSPI exerciser"
date = 2026-07-13T09:00:00
description = "Introducing Tamal — mole's idea retargeted to Intel's eSPI. An experimental FPGA exerciser that plays eSPI controller, drives legal and illegal cycles with deterministic timing, and captures everything. Very early days, but the shape is promising."
[taxonomies]
tags = ["embedded", "rust", "fpga", "clash", "haskell", "espi", "tamal", "testing"]
+++

[mole] is an FPGA bit-cycle engine for exercising I²C and I³C — you
write a little program, it drives the bus edge by edge, legal or
illegal, and hands you back a structured trace of what happened.
*Tamal* is that same idea aimed at a different, meaner bus: Intel's
[Enhanced Serial Peripheral Interface][espi-spec] (eSPI, base spec
rev 1.0).

[mole]: https://github.com/felipebalbi/mole
[espi-spec]: https://www.intel.com/content/www/us/en/support/articles/000020952/software/chipset-software.html

The one-line pitch, which is the vision more than the current state:

> A programmable eSPI controller/target that turns compliance testing
> into a reproducible, fully-observable, byte-for-byte deterministic
> exercise.

Read that as the destination, not the odometer. Tamal is
**experimental** — I'm keeping that adjective on it until it has
withstood considerable real-world load, at which point I'll drop it.
Today it plays eSPI **controller** only (target role is designed but
not built), at a **single 20 MHz clock** (all eSPI speeds are on the
roadmap), and everything below the honesty line near the end of this
post runs in *simulation*, not on silicon.

This post is what Tamal is, why it's shaped the way it is, why the
gateware is written in Haskell of all things, what I've learned
building it — and, honestly, how early it still is. The repo is
[github.com/felipebalbi/tamal][repo], public and split-licensed. Read
on before you clone it expecting a finished product; it isn't one yet.

[repo]: https://github.com/felipebalbi/tamal

## The problem

eSPI is the bus that replaced LPC on modern platforms: a short,
fast-ish serial link between a chipset/SoC and an embedded controller,
flash device, or BMC. It's more capable than it looks. A single eSPI
link multiplexes four logical channels — **Peripheral**, **Virtual
Wire**, **OOB** (tunneled SMBus), and **Runtime Flash Access** — over
one set of pins, with command/response framing, turnaround phases,
CRC, alerts, and a status register that gates who's allowed to talk.

That density is exactly what makes it hard to test. "Does my eSPI
target behave correctly?" is not one question. It's: does it honour
turnaround timing, does it respect CRC when enabled, does it track the
status register, does it tolerate a WAIT STATE, does it drive alerts
correctly, does it decode each channel's packets, and — the part
almost nothing can produce on demand — *does it do the right thing
when the other side does the wrong thing?*

Producing a legal transaction is the easy 20%. Producing a specific
*illegal* one — a deliberately short turnaround, a corrupted CRC, a
truncated packet, a cycle on a channel that isn't enabled yet — at a
precise moment, reproducibly, over and over, is the 80% that a real
device or a canned test fixture can't give you. That's the gap Tamal
is built to fill: an exerciser that will drive *any* cycle, correct or
malformed, with deterministic timing, and capture everything the bus
did in response.

## The name

eSPI wraps four logical channels into one serial packet stream. A
*tamal* is a wrapped, layered dish — masa and filling folded inside a
husk. The mapping wrote itself: eSPI wraps Peripheral, Virtual Wire,
OOB, and Runtime Flash Access into one stream of bytes on the wire;
Tamal is the thing that unwraps every layer and checks it against the
spec. Naming projects is one of the few unambiguous joys of this line
of work, and the *mole* → *tamal* lineage (both wrapped, both
Mesoamerican, both about what's hidden inside) was too good to pass
up.

## The key insight

The temptation with an FPGA bus tool is to think of it as a throughput
problem — get the bytes on and off the wire fast enough. eSPI defeats
that framing immediately, and in a useful way:

> This is **not** a throughput problem. It is an **external timing
> alignment** problem.

eSPI tops out at 66 MHz. On an Artix-7 that's *slow*; the fabric runs
at 100 MHz and never breaks a sweat pushing bits. The danger isn't
speed. It's that in **target** role — which Tamal is designed for but
does not yet play — you are not the clock master: the eSPI clock is
driven by the device under test, and you must respond *relative to
it*. The parts that will actually hurt you are turnaround/tri-state
timing, setup and hold against an external clock, and getting IO
direction control exactly right on a shared, bidirectional data bus.
None of that is about going fast. All of it is about being aligned.

Internalizing that reframed the whole design. The engine doesn't need
to be clever or quick. It needs to be *precise* about when it drives,
when it samples, and when it lets go of the pins.

## The shape: three planes

Tamal has a clean split into three planes, and almost every design
decision falls out of which plane it belongs to:

- **Control plane** (host → FPGA): load a compiled test program,
  select role (controller today; target is designed but not yet built),
  IO mode (single/dual/quad), CRC on or off, and triggers. Error
  injection isn't a knob here — it lives *in the program*, where the
  test author writes the malformed cycle directly (more on that below).
- **Bus plane** (FPGA ↔ DUT): the eSPI link itself — `CS#`, `CLK`,
  `IO[3:0]`, `ALERT#`, `RESET#` — driven or sampled against the device
  under test.
- **Trace / result plane** (FPGA → host): observed transactions,
  channel decode, captured cycles, and verdicts.

There's one load-bearing rule that spans the planes: **never block the
bus on trace backpressure.** If the host can't keep up draining the
trace, the bus plane does not stall waiting for it — events get
dropped with an overflow marker instead. A test rig that perturbs the
timing of the thing it's measuring is worse than useless, so the trace
plane is always allowed to lose data before the bus plane is allowed
to hesitate.

On the FPGA, that shape is deliberately boring:

```
 host ──UART──► loader ──► instr BRAM ──► engine (mealy step) ──► eSPI pads
      ◄─UART──── drain  ◄──── ring BRAM ◄──────┘   (IO[3:0], CS#, SCK,
                                                     RESET#, ALERT#)
```

The host ships a compiled program over UART; the loader writes it into
an instruction block RAM; the engine executes it as a Mealy machine,
driving and sampling the eSPI pads; every transaction lands in a trace
ring (another block RAM); on `HALT` the drainer sweeps the ring back
out over the same UART.

The whole thing is **one clock domain** — `Dom100`, 100 MHz — with no
clock-domain crossings and no FIFOs. The trace ring BRAM *is* the
buffer, and UART is roughly 500× slower than the fabric, so there's
nothing to gain from a FIFO in front of it. (On the Cyclone V board a
50 MHz oscillator is multiplied up to 100 MHz by an Altera PLL, but
it's still a single design domain.) Fewer moving parts, less to get
wrong, and — this matters later — a design that simulates end to end
without any special-casing.

## The repo

Two toolchains, cleanly separated:

```
crates/                 Rust host tooling (Cargo workspace)
  tamal-abi/            bytecode/ISA encoding + COBS/CRC-8 wire format + typed trace decode
  tamal-asm/            assembler: RISC-V-flavored source -> tamal bytecode
  tamal-asm-cli/        `tamal-asm` binary (clap front-end)
  tamal-loader/         host-side loader: load -> trigger -> drain over a transport
  tamal-loader-cli/     `tamal-loader` binary
hdl/                    Clash gateware + Vivado/Quartus build (self-contained)
docs/                   design specs + implementation plans
```

`tamal-abi` is the crate that matters most: it's the ABI, the single
source of truth for the bytecode encoding, the COBS/CRC-8 wire format,
and the typed trace decode. Everything else — assembler, loader,
gateware — agrees with `tamal-abi` byte for byte or it's a bug. It is
deliberately **transport-agnostic**: v1 speaks UART because that's
what the Arty's FT2232 gives you (USB-UART plus JTAG, not a USB3
SuperSpeed FIFO), but the wire format doesn't know or care, so a
future EZ-USB FX3 (GPIF II slave-FIFO) shield can be bolted on as
another backend without touching the ABI.

The licensing follows the `crates/` ↔ `hdl/` seam. The Rust host
tooling is **MIT**. The Clash gateware is **CERN-OHL-W-2.0** (CERN
Open Hardware Licence v2, Weakly Reciprocal), because an open-hardware
licence fits a hardware description better than a software licence
does. Every `.hs` file under `hdl/` carries a REUSE-style SPDX header
to make that boundary machine-checkable.

## A RISC-V-flavored ISA, and a very dumb engine

Tamal's on-FPGA engine is programmable, and the instruction set is
**inspired by — but not compatible with — the RISC-V 32-bit (RV32I)
ISA.**

From RV32I it borrows the ergonomics: 32-bit fixed-width instructions;
a 32-name register space `x0`..`x31` with `x0` hardwired to zero (v1
implements 16 physical registers, with `x16`..`x31` aliasing their
low-4 twin); the R/I/S/B/U/J instruction formats; and the ABI register
names (`zero`, `ra`, `sp`, `t0`, `s0`, `a0`, …). The assembler follows
the [riscv-asm-manual][riscv-asm] conventions for directives, labels,
and pseudo-instructions — but *follows* is doing real work in that
sentence. What's implemented today is the subset the examples exercise
(`.text`, `.globl`, `.equ`, the `li` pseudo-instruction, symbolic
labels, the branch mnemonics). The rest of the RISC-V surface I'd like
to have — `.align`, `.macro`, numeric local labels (`1f`/`1b`),
`mv`/`j`/`call`/`ret`/`beqz` and friends — is intended but not written
yet. If you've used RISC-V assembly the current subset will read like
home; just don't assume the whole manual works.

[riscv-asm]: https://github.com/riscv-non-isa/riscv-asm-manual

Where it diverges is the opcode space: Tamal repurposes and extends it
with bus-domain instructions for eSPI work — driving and sampling
cycles, per-channel operations, deterministic timing, author-written
error injection, and capture/verdict. The upshot is a hard rule: Tamal
bytecode is **not** interchangeable with a stock RISC-V toolchain, and
the project never claims otherwise. It's RISC-V-*flavored*, not
RISC-V.

The design decision I'm happiest with is how little the engine knows.
**The engine is a nearly-dumb SPI shifter with almost no eSPI
knowledge.** There is no "PUT_IORD" opcode and no notion of a channel.
The one concession is a CRC-8 unit that runs over *incoming* bytes, so
a program can read the residue and decide whether a packet from the
link partner was valid — but even it has no idea what a "packet" *is*,
and every *outbound* CRC byte is still computed by the host. The host
builds every byte that goes on the wire — command, header, payload, TX
CRC — and the engine just shifts them out and shifts responses back.
All the eSPI semantics live in the program, in software, where they're
easy to read, easy to change, and easy to get deliberately wrong.

## The shape of a program

The smallest interesting program reads one byte from an eSPI
Peripheral-channel I/O port. Here's the real `peripheral_io_read.s`
from the repo, reading I/O port `0x64` — the classic 8042
keyboard-controller status port:

```asm
    .equ  PUT_IORD1,      0x44    # PUT_IORD_SHORT, length = 1 byte
    .equ  RSP_WAIT_STATE, 0x0F    # eSPI WAIT_STATE response code
    .equ  VERDICT_OK,     0x00    # host verdict codes (written by halt)
    .equ  VERDICT_CRC,    0x11

    .text
    .globl _start
_start:
    set_config CONTROLLER, X1, SCK20, ALERT_PIN   # controller, x1 IO, 20 MHz
    cs_assert                       # begin frame: CS# low

    # --- command phase: host-built eSPI packet ---
    put_byte PUT_IORD1              # CMD:  PUT_IORD_SHORT (1 byte)
    put_byte 0x00                   # addr [15:8]
    put_byte 0x64                   # addr [7:0]  -> I/O port 0x64
    put_byte 0x16                   # TX CRC-8 over the 3 bytes above (poly 0x07)
    tar 2                           # legal turnaround; tar 3 / tar 1 = deliberate violation

    # --- response phase: WAIT_STATE poll + RX CRC residue verdict ---
poll:
    crc_reset                       # drop any prior WAIT_STATE byte from the residue
    get_byte t0                     # response code (auto-updates RX CRC-8)
    li   t1, RSP_WAIT_STATE
    beq  t0, t1, poll               # WAIT_STATE -> keep polling
    get_byte t0                     # read data byte (port 0x64 value)
    get_byte t0                     # status [7:0]
    get_byte t0                     # status [15:8]
    get_byte t0                     # trailing CRC byte -> drives residue to 0
    rdsr t2, CRC                    # RX CRC-8 residue (0 == good packet)
    cs_deassert                     # end frame: CS# high
    bnez t2, bad_crc
    halt VERDICT_OK
bad_crc:
    halt VERDICT_CRC
```

Read it top to bottom and the whole model is visible. `set_config`
picks controller role, single (x1) IO, a 20 MHz clock (`SCK20` — the
only rate wired up today, though the ISA reserves the field for the
faster eSPI speeds still to come), and pin-based alerts. `cs_assert`
pulls `CS#` low. Then a run of `put_byte`s spells out the eSPI command
packet a byte at a time — opcode, address, and a TX CRC-8 the *program*
computed, not the engine. `tar 2` performs a legal turnaround;
swapping in `tar 3` or `tar 1` is how you inject a deliberate
turnaround violation — that's the whole error-injection story, a test
author spelling out the malformed cycle by hand, not a probabilistic
"corrupt 10% of packets" knob. The response phase polls for
WAIT_STATE, reads the data and status bytes, and checks that the RX
CRC residue drove to zero — the eSPI-idiomatic way to validate a
packet. Finally `halt` writes a verdict code the host reads back from
the trace.

The load-bearing observation: **only the command-phase `put_byte`s are
channel-specific.** The CS framing, the WAIT_STATE poll, and the CRC
residue check are byte-for-byte identical across the Peripheral I/O
read, the [Virtual Wire][vw-example] example that deasserts `PLTRST#`,
and every other per-channel program in `examples/`. The channel is
just a different run of bytes in the middle. That's the payoff of a
dumb engine: the interesting part of each test is a handful of lines,
and everything around it is boilerplate you can read once and stop
thinking about.

[vw-example]: https://github.com/felipebalbi/tamal/blob/main/examples/virtual_wire_pltrst.s

## Why Clash, why Haskell

The gateware is written in [Clash] — a Haskell-to-RTL compiler — and
that's the choice people ask about first. It looks exotic next to
Verilog or even the SpinalHDL that drives mole. The reasons are
entirely practical, and they all come back to one thing: **iteration
speed** — how fast the edit-test loop closes. (Not to be confused with
eSPI's turnaround; this is developer seconds, not bus cycles.)

[Clash]: https://clash-lang.org

The Tamal test suite runs in **under a second.** Not the synthesis —
the *tests*. Every pure leaf of the design (`stepM`, `ringWrite`,
`rigState`, `ledPattern`) is a plain Haskell function, and I test them
the way I'd test any Haskell function: [hedgehog] property tests and
HUnit unit tests, each pure leaf and the engine keystone checked
against a reference model. No simulator to spin up, no waveform to
eyeball, no four-minute Vivado round-trip to find out I got a mux
backwards. `cabal test`, a second later I know.

[hedgehog]: https://hedgehog.qa

It gets better at the top. Because the whole integration is wired over
plain Clash `Signal`s — no `BiSignal` in the core, the tri-state only
appears in the thin board shell — the *entire system* simulates in
Haskell. There's a whole-system cosim (`Test.Top`) that serializes a
real `LOAD_PROGRAM` + `TRIGGER` onto a modeled UART line, runs the
actual load → run → drain path, decodes the UART output, and asserts
the drained trace (revision header, records, `HALT` terminator) *and*
the eSPI pin activity — UART → loader → engine → eSPI → ring → drain,
end to end, in one test, in the same language as the design, in
milliseconds. I can refactor the engine and know within a second
whether the full pipeline still behaves. That feedback loop is the
single biggest force multiplier on the project.

The type system pulls its weight too. eSPI is a bidirectional,
tri-state, direction-controlled bus, which is precisely where hardware
bugs love to hide. Having the compiler track widths, states, and pad
directions catches a whole class of "which way is this pin pointing
this cycle" mistakes before simulation, let alone before silicon.

And the discipline of a single clock domain with no CDC and no FIFOs
means the design is small enough to hold in your head — which is what
makes the sub-second property testing tractable in the first place.
Fast tests and a simple design reinforce each other.

The trade is real and worth stating: Clash is a smaller ecosystem, the
error messages can be a wall of type, and the toolchain (GHC 9.10.3 +
`cabal` + Vivado/Quartus) is more to stand up than `verilator`. For
this project, on this bus, that sub-second feedback loop won the
argument easily.

## Lessons learned

A few things I didn't know going in, in case they save you some time:

- **The bus is not the hard part; the pins are.** I spent the early
  design energy worrying about eSPI's channel matrix and almost none
  on tri-state timing. That was backwards. Reframing the whole thing
  as an *external timing alignment* problem — especially for the
  not-yet-built target role — is what made the architecture click.
- **A dumb engine is a feature.** Every time I was tempted to teach the
  engine more eSPI ("just let it build the TX CRC too", "just add a
  channel register"), pushing it back up into the host program made the
  design smaller *and* made illegal-cycle injection trivial. Malformed
  packets are free when the host builds every byte. (The one place I
  gave in — an RX-side CRC-8 so a program can check an inbound residue —
  earns its keep precisely because it stops at *checking* and never
  learns what a packet is.)
- **Clash's tri-state lowering has sharp edges.** Four scalar `inout`
  lanes (`io0`..`io3`) exist instead of a tidy `Vec` because Clash
  fuses a per-lane `BiSignalIn`/`BiSignalOut` pair into one `inout`
  port — but a `Vec` of BiSignals does *not*; it silently lowers to a
  plain input. That one cost me an afternoon.
- **The no-reset power-up design is deliberate.** The top ties reset
  permanently de-asserted and relies on power-up `init`, so Clash emits
  no reset port at all — matching the sibling Clash examples. Fighting
  that to add a "proper" reset is a trap.
- **Some ghc-options are load-bearing.** The `common-options` in
  `tamal.cabal` aren't optional style — Clash needs them. Trimming them
  to tidy up breaks codegen in non-obvious ways.

## What's running today — and what isn't

Now the honest part, because this is the whole reason for the
**experimental** label I put on it up top.

**Gateware: v1 complete, in *simulation*.** The full pipeline exists
and is tested in Clash — the RISC-V-flavored cycle engine, the
instruction and trace-ring block RAMs, the COBS/CRC-8 wire format, the
UART load/drain loader, the tri-state eSPI pad boundary, and the
`topEntity` that wires it all to the Arty A7 pins. The whole-system
cosim streams a program in and checks the drained trace end to end.
`cabal run clash -- Tamal.Board.ArtyA7 --verilog` emits a
synthesizable top, and `cd hdl && make` builds a bitstream. But **v1
is controller role, single (x1) IO, one 20 MHz clock rate, UART
transport, and it has not yet been brought up on real hardware.**
Simulation-complete is a real milestone; it is not the same as "works
on the bench."

**Host tooling: v1 implemented and tested.** The Rust ABI, assembler,
and loader are built and mirror the gateware's wire and bytecode
contract byte for byte. The live serial path is exercised on hardware
rather than in CI, and the pass/fail *conformance verdict engine* — the
thing that would let Tamal actually render a compliance judgment —
does not exist yet. Today a program ends in a `HALT`/`TRAP` with a
host-defined verdict code; a real conformance catalog is a later
phase.

So, plainly: **Tamal is early.** What it most needs next is validation
against *real* eSPI scenarios — on real silicon, against real targets,
with a logic analyzer confirming the timing the simulator promises.
Until on-hardware bring-up lands, every claim in this post about what
the bus does is a claim the *model* makes, and models are exactly the
thing an exerciser exists to distrust.

The roadmap is four phases:

1. **Link + transaction bring-up.** SPI-style framing,
   command/response/turnaround, Get Status / Get Configuration, CRC;
   controller role over single IO. *(This is where v1 sits — done in
   sim, next on hardware.)*
2. **The four channels.** Peripheral, Virtual Wire, OOB (tunneled
   SMBus), Runtime Flash Access; the ISA, assembler, and loader path;
   result streaming.
3. **Target role, alerts, dual/quad IO, faster clocks.** The
   external-timing-alignment problem in full: responding to a clock you
   don't own, and driving the higher eSPI speeds beyond today's 20 MHz.
4. **Determinism and verdicts.** The error-injection model stays what
   it is today — the test author writes the malformed cycle by hand, so
   a program is byte-for-byte reproducible by construction — joined by
   the verdict engine and a conformance catalog that turn a captured
   trace into an actual pass/fail judgment.

## Where to look

The source is [on GitHub][repo]. The gateware's story is in
[`hdl/README.md`][hdl-readme] and `hdl/PLAN.md`; the design specs live
under `docs/`; `AGENTS.md` is the fastest orientation to how the
pieces fit. The `examples/` directory is the best way to get the feel
of the ISA — start with `smoke_halt.s`, then `peripheral_io_read.s`,
then the per-channel programs.

It's early, it's promising, and it's exactly the stage where design
critiques are most useful. If you know eSPI — especially its nastier
corners — I'd love to hear where this is wrong before it meets
silicon.

[hdl-readme]: https://github.com/felipebalbi/tamal/blob/main/hdl/README.md
