+++
title = "Inside mole's engine, part 2: quarter-bit timing and the bus"
date = 2026-08-24T09:00:00
description = "What the execute stage does: quarter-bit timing, the SCL waveform generator, the bus observer that runs in parallel, what EMIT_BIT_IMM physically does to SDA and SCL, controller and target in one bitstream, and why error injection happens at compile time rather than as runtime dice."
[taxonomies]
tags = ["embedded", "fpga", "spinalhdl", "mole", "hdl", "i2c", "i3c"]
+++

The [previous post](/posts/mole-engine-pipeline/) followed one
instruction through fetch, decode, and register read, and dropped
it at the door of the execute stage. This post is what happens on
the other side. The pipeline mechanics it shares with any 32-bit
RISC; what the execute stage *does* with the decoded payload is
where mole stops looking like a CPU and starts looking like a
thing that talks to a two-wire bus on a deterministic quarter-bit
grid.

If there is one phrase that explains mole's design more than any
other, it is **quarter-bit timing**. It is the unit the engine
schedules in, the unit the bus observer samples on, the unit
`LOAD_TIMING` programs the period of, and the unit the result-ring
timestamps count in. Every other piece of timing discipline in
the engine is a consequence of dividing the bit cell by four and
deciding what to put in each slot. By the end of this post the
reader should be able to say what `EMIT_BIT_IMM` does to SDA and
SCL, how long it takes, and what the parallel bus observer is
doing while it runs. The closer is the argument: a bit-cycle
engine that doesn't know what I²C is — and that lets the test
author commit a deliberate malformed sequence to version control
with a `(seed, ratio)` tuple on it — is the right shape for the
failure-path testing the [first post in this
series](/posts/i2c-target-restarted-zero/) sketched.

## Quarter-bit resolution, briefly

A bit on an open-drain serial bus has structure: a setup phase
while the clock is still low, a high phase while the receiver is
allowed to sample, a hold phase after the clock falls. A real
controller has to honor all three; a real target has to sample
inside the window the spec promised it. Quarter-bit timing is the
engine's choice of *resolution* for those phases.

Each bit cell is divided into four equal slots, indexed Q0..Q3.
By convention used throughout the engine: SDA is driven on Q0,
SCL goes low across Q0 and Q1, SCL goes high across Q2 and Q3,
and the canonical sample point is the Q2→Q3 boundary while SCL
is high. The bus observer samples on the same grid. A "bit" is
four ticks of one timer.

Real silicon does not honor this neatly. A driver under test
might assume a Q1.5 sample point, stretch SCL halfway through Q1,
or move its setup edge a quarter early. With quarter-bit
resolution the engine can put a transition at exactly the wrong
slot, on purpose, and watch what happens — and because every slot
length is programmable, "the wrong slot at the wrong period" is
also reachable.

## The four timing slots

`LOAD_TIMING` indexes eight 14-bit timing registers. The four
quarter-bit dividers — one per bus mode — live in even slots; the
odd slots hold setup-and-hold knobs the WIRE FSM will consume
once HDR-DDR lands. Per v0.2 spec §5.17:

| `reg` | BUS_MODE consulted | Role                                |
|-------|--------------------|-------------------------------------|
| 0     | `i2c`              | quarter-bit clock divider, OD       |
| 2     | `i3c-OD`           | quarter-bit clock divider, i3c-OD   |
| 4     | `i3c-PP`           | quarter-bit clock divider, PP SDR   |
| 6     | `hdr-ddr`          | quarter-bit clock divider, HDR-DDR  |

The active divider is selected by the `BUS_MODE` register, not
by the opcode. A typical line in moleasm:

```text
LOAD_TIMING reg=0, divider=480   ; i2c standard-mode, ~24 MHz fabric
```

On the engine side this is one cycle in the execute stage. The
written value lands in a vector, and from that point the
quarter-bit timer reloads from the slot the active mode points
at:

```scala
val loadTimingRegs = Vec(
  Reg(UInt(14 bits)) init (cfg.quarterPeriodCyclesReset - 1),
  8
)

val activeDivider = UInt(14 bits)
switch(busModeReg) {
  is(BusMode.i2c)    { activeDivider := loadTimingRegs(0) }
  is(BusMode.i3cOd)  { activeDivider := loadTimingRegs(2) }
  is(BusMode.i3cPp)  { activeDivider := loadTimingRegs(4) }
  is(BusMode.hdrDdr) { activeDivider := loadTimingRegs(6) }
}
timer.io.reload := activeDivider.resize(timer.counterWidth bits)
```

A program that never issues `LOAD_TIMING` runs at the reset
divider for whatever mode is active; subsequent `SET_BUS_MODE`
switches are sub-cycle re-pointings of `activeDivider`.

## The SCL waveform generator

The block that turns "I am in quarter Q" into "this is what SCL
should look like" is small enough to fit on one screen. It is not
even a `Component`; it is a pure function on a 2-bit quarter
index:

```scala
def apply(quarterIndex: UInt): SpinalEnumCraft[TxSymbol.type] = {
  val sym = TxSymbol()
  // Default to dominant (Q0/Q1). Q2/Q3 override below.
  sym := TxSymbol.dominant
  when(quarterIndex(1)) {
    sym := TxSymbol.recessive
  }
  sym
}
```

That is the entire SCL shape: dominant when the top bit of the
quarter index is zero (Q0 and Q1), recessive otherwise (Q2 and
Q3). The function returns a `tx_symbol`, not a pair of driver
enables — that decoding is done one layer up, by the shared
`SymbolDecoder`, the one place in the engine that knows how
`BUS_MODE` translates a symbol to drive lines. Under open-drain
`BUS_MODE`s the recessive half releases the pad and the external
pull-up does the work; under push-pull `BUS_MODE`s the recessive
half actively drives high. The waveform generator is oblivious to
either.

The execute stage calls `SclWaveformGen(nextQ)` once per tick.
The timer paces four quarters; the waveform generator names the
SCL symbol for each; the symbol decoder turns the named symbol
into pad enables. Three separable pieces, no per-bus-mode
special-casing inside the FSM.

## What EMIT_BIT_IMM actually does

Walk one instruction through. The 32-bit word is
`group=0b00 sub=0b0000`, with the SDA `tx_symbol` at bits `[4:3]`
and the flag triple `(expect, mask, capture)` at bits `[2:0]`. By
the time it arrives in execute, decode has classified it as a
WIRE opcode and the WIRE mini-FSM is sitting in `WS_IDLE` waiting
for work.

On the first execute cycle the FSM does its entry dispatch:

```scala
is(0) {
  val txRaw = xEmitBitImmTxRaw
  when(txRaw === B"11") {
    // Reserved tx_symbol → immediate trap.
    xWireHaltReq := True
    xWireHaltWord := makeTrapWord()
    xWireDone := True
  } otherwise {
    val sdaSym = TxSymbol()
    sdaSym.assignFromBits(txRaw)
    val sdaDrive = SymbolDecoder(sdaSym, busModeReg)
    sdaDriveLow  := sdaDrive.driveLow
    sdaDriveHigh := sdaDrive.driveHigh
    // SCL Q0: dominant per SclWaveformGen.
    when(!roleReg) {
      val sclQ0 = SymbolDecoder(SclWaveformGen(U(0, 2 bits)), busModeReg)
      sclDriveLow  := sclQ0.driveLow
      sclDriveHigh := sclQ0.driveHigh
    }
    xQIdx := 0
    timerLoadReg := True
    xWireState := WS_EMIT_BIT
  }
}
```

That entry cycle decodes the SDA `tx_symbol` against the current
`BUS_MODE` and commits it to the pad enables, drives SCL to its
Q0 shape (dominant under controller role, released under target
role), resets the quarter index `xQIdx` to 0, and asks the
quarter-bit timer to reload itself on the next cycle. The X
stage holds the instruction because `xWireState` is now
`WS_EMIT_BIT`.

From there the FSM runs four quarter-bit periods. On each timer
tick it advances `xQIdx`, recomputes SCL via
`SclWaveformGen(nextQ)` — flipping it from dominant to recessive
at Q1→Q2 — and lets SDA hold whatever was driven on entry. On
the Q2→Q3 tick, if `mask` is set the sampled SDA value is
compared against `expect` and `MISMATCH_FLAG` is updated; if
`capture` is set the sampled bit is staged for a write into R7.
On the Q3 tick the FSM returns to `WS_IDLE`, X is released, and
the next instruction moves into execute.

One bit on the wire, four quarter ticks long. Each tick's period
is set by whichever `LOAD_TIMING` slot the active `BUS_MODE`
points at; the SCL shape is set by the same quarter index that
paces the timer. The execute stage does not know it is "doing
I²C." It is putting symbols on two wires on a four-slot grid.
What protocol that adds up to is a property of the program, not
of the engine.

There is one bus-shape fold the executor knows about: an OD-class
slave stretching SCL low across the Q1→Q2 boundary. The FSM
pauses the timer, waits for the SCL rising edge or a configured
stretch timeout, and resumes Q2. Under PP-class `BUS_MODE`s a
stretching slave is a spec violation and the FSM traps.

## The bus observer

While the WIRE FSM is driving lines, a second block is *watching*
them. `BusObserver` is a tiny `Area` constructed inside the
engine pipeline from the synchronized SDA and SCL sample lines:

```scala
val sdaSyncShift = Reg(Bits(2 bits)) init (B"11")
val sclSyncShift = Reg(Bits(2 bits)) init (B"11")
sdaSyncShift := sdaSyncShift(0) ## sdaRaw
sclSyncShift := sclSyncShift(0) ## sclRaw

val sdaSampled     = sdaSyncShift(1)
val sclSampled     = sclSyncShift(1)
val sdaSampledPrev = RegNext(sdaSampled) init (True)
val sclSampledPrev = RegNext(sclSampled) init (True)

val sdaFalling = sdaSampledPrev && !sdaSampled
val sdaRising  = !sdaSampledPrev && sdaSampled
val sclFalling = sclSampledPrev && !sclSampled
val sclRising  = !sclSampledPrev && sclSampled

val startEdge  = sdaFalling && sclSampled
val stopEdge   = sdaRising  && sclSampled
```

That is the entire bus observer. Two-flip-flop synchronizer per
line, edge detectors derived from one-cycle history, and the two
combined edges (`startEdge`, `stopEdge`) a START / STOP detector
cares about. The observer fires on the fabric clock,
unconditionally, regardless of what the FSM upstream is doing.

The execute stage feeds those outputs into the sticky flag set.
There are five 1-bit flags, all write-once-until-overwritten:
`MISMATCH_FLAG`, set when a sampled bit differs from `expect`
under `mask=1`; `TIMEOUT_FLAG`, set when a `WAIT_ON` expires
before its condition fires; `START_FLAG` and `STOP_FLAG`, set by
the observer's edge detectors above; and `REG_ZERO_FLAG`, set by
ALU opcodes whose result is zero. A flag persists until another
opcode of the same writing class overwrites it or until
`FLAG_CLEAR` explicitly clears its bit.

The sticky flag set is how a mole program *reacts* to bus state.
`BRANCH_ON MISMATCH, retry` jumps if the last comparison failed.
`WAIT_ON STOP_SEEN, 0` blocks forever until `stopEdge` fires.
`WAIT_ON START_SEEN, 100` blocks for at most 100 quarter-bit
ticks and then falls through with `START_FLAG` or `TIMEOUT_FLAG`
set, depending on which happened first. `FLAG_CLEAR 0b00001`
resets `MISMATCH_FLAG`. None of those opcodes are I²C-specific;
they are enough to express every control-flow shape an I²C or I³C
test program needs.

The result ring is the other half. Capture-bearing WIRE opcodes
push a one-word `CAPTURE` record per sampled bit; `MARK` pushes
a three-word labeled timestamp record. The host reads the ring
out after the program halts and turns it into a transcript of
what actually happened on the wire. That closes the loop on
[part 1](/posts/mole-engine-pipeline/)'s "result ring, briefly"
beat — `CAPTURE` records are what the bus observer's samples
turn into when the executing opcode asks them to.

## Controller and target in one bitstream

There is one `SET_ROLE` opcode. It writes a single architectural
register. The execute stage reads that register on every WIRE
dispatch and dispatches differently. Under controller role,
`EMIT_BIT_IMM` drives SCL on its own grid; under target role it
releases SCL and samples the controller's clock instead. The
target-role opcodes — `SAMPLE_BIT_ON_SCL` (wait for SCL rising,
sample SDA) and `DRIVE_BIT_ON_SCL` (drive on falling, sample on
rising, release on falling) — execute only under target role;
calling them as a controller traps with `STATUS_TRAP`. The
reverse case is softer: SCL-driving inside an `EMIT_BIT` cell is
quietly suppressed under target role rather than trapping, so the
same emit opcode stays legal as an asynchronous SDA-glitch path
between transactions. Reserved opcode slots and the unused LOOP
group all trap with `STATUS_TRAP` (`0x1F`).

A single bitstream covers both halves of any bus conversation. A
target-driver test rig that needed a re-flash to switch from "I
am the target under test" to "I am the controller poking the
target" would be twice as much code and twice as much bring-up.
Mole's design avoids that with one architectural register and a
runtime opcode that writes it. The reader doing target-driver
verification — the same reader the [first
post](/posts/i2c-target-restarted-zero/) was written for — can
write a program that *plays* the controller side of a test
without leaving the loop, and inside the same program switch
roles to observe how the target responds.

This isn't the only way to build a programmable test engine; it
is just the shape mole takes, and the cost of that shape is one
register and a few `when(!roleReg)` clauses inside the WIRE FSM.

## Compile-time-only error injection

The engine has zero runtime randomness. No PRNG inside the FPGA,
no entropy register, no opcode that means "randomly flip the next
bit." Per v0.2 spec §15, error injection is a property of the
*bytecode*, not of the engine that runs it. The host-side
compiler accepts `(seed, ratio)`; a deterministic PRNG seeded by
`seed` and gated by `ratio` decides, *at assembly time*, which
bit positions get substituted for their wrong-value counterparts;
the bytecode that lands in SPRAM already encodes the malformed
sequence the test wants. The engine looks at the bytecode and
executes it.

Today the substitution happens by hand in the assembly source:
the test author writes the specific `EMIT_BIT_IMM tx=hiz` or
`EMIT_QUARTER_IMM scl=dominant` that *is* the injected fault. A
Layer-1 compiler — on the roadmap — will own `(seed, ratio)` and
hide the per-bit decisions. The engine contract is the same
either way.

Three properties fall out.

First, `ratio = 0` is *exactly* zero. Not "approximately zero,"
not "the PRNG happened not to fire on this run." The compiler
with `ratio = 0` and any seed produces bytecode byte-identical to
the no-injection build, by direct comparison. There is no
probability of an injection slipping through, because the path by
which one could does not exist on the engine.

Second, re-running the same `(seed, ratio)` reproduces the run
byte for byte. Same wire trace, same result-ring contents. A
failure captured under `(seed = 7, ratio = 0.001)` is reproducible
by committing the bytecode — or, equivalently, the
`(source, seed, ratio)` tuple — to the test repository and
re-running against the same engine revision. Every bug has a
seed; you can hand a flake to another engineer with a six-line
program and the guarantee they will see what you saw.

Third — the design call, rather than a property of it — mole is a
test instrument, not a fuzzer. A coverage-driven fuzzer is the
better tool for "what input shape did I forget to think about." A
deterministic test instrument is the better tool for "is this
specific boundary case reproducible," "did my last change break
this corner," and "can I bisect to the commit that introduced
this flake." Those are the questions the first post in this
series was stuck on, and the ones a verdict-by-soak campaign
actually needs answered. Compile-time-only error injection is the
choice to be useful for those questions, and to leave coverage
fuzzing to tools built for it.

## What this still doesn't cover

A few honest limits.

HDR-Ternary is out of scope. There is no analog I³C PHY in the
design and no path to add one without different hardware; SDR
and HDR-DDR are the I³C surface mole targets.

The Layer-1 compiler is still on the roadmap. Today the way you
write a mole program is to write `.moleasm` and assemble it with
`mole-asm`. That is enough for every test in this series,
including the failure-path reproductions from [post
1](/posts/i2c-target-restarted-zero/); the [next
post](/posts/mole-worked-examples/) walks one through end to end.
Protocol-aware primitives, CCC catalogs, peripheral emulators,
and the `(seed, ratio)` substitution are what comes after.

The engine described here ships on the iCEbreaker UP5K today.
The ECP5 and CertusPro-NX tiers extend the timing range and the
capture-ring depth; the ISA, the quarter-bit model, and the
compile-time error-injection contract carry forward unchanged.

Quarter-bit timing is the unit. `LOAD_TIMING` sets its period;
the SCL waveform generator names its shape; the bus observer
samples on its edges; `EMIT_BIT_IMM` runs for four of them. The
sticky flag set, the result ring, the role register, and
compile-time error injection are what the program reaches for to
turn that grid into a test. The engine is small enough to fit on
one screen of state and decide one cycle at a time. That is the
substrate the rest of mole is built on.
