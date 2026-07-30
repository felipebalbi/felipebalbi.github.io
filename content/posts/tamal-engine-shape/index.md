+++
title = "Tamal: Eight Phases and a Pure Step"
date = 2026-07-31T09:00:00
draft = true
description = "Reading Tamal's engine from the outside in: why the machine that runs a compiled program is read shape-first, not whole, the way a leaf is read whole; the widest export list in the series, eleven names and six of them types, because the top must name the engine's state to wire it; the eight imported modules that are eight still-closed boxes, the exact inversion of the leaves-first UART; the one-line keystone type step :: State -> BusIn -> (State, BusOut, Maybe Ring), a pure Mealy transition the top lifts and hedgehog tests to death; the eight-phase lifecycle enum that is the machine's spine; the seventeen-field State record read in six groups, its pin drives registered so the outputs are a projection and not a computation; the boundary types that carry the one-cycle fetch latency the memories established; and the four trivial phases --- Idle, Halted, Preamble, Fetch --- that hold, re-run, stamp a REVISION word, and spend one deliberate cycle waiting for a block RAM, leaving Exec and the bus for the posts that follow."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "fsm"]
+++

The [memories][mem] closed on a promise: *next, we walk through.* Every
block before them --- the [CRC][crc], the UART, the [loader][loader], the
two [block RAMs][mem] --- was read the same way, opened and emptied and
set back down, and each ended by pointing at the one door still shut.
This is that door. [Tamal]'s engine is the machine the whole series has
been circling: the thing the [loader][loader] fills, triggers, and
drains; the thing that turns the words in one memory into the records in
the other; the reason any of the plumbing exists.

But you do not read a machine the way you read a leaf.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[mem]: https://balbi.sh/posts/tamal-mem/
[loader]: https://balbi.sh/posts/tamal-loader/
[top]: https://balbi.sh/posts/tamal-uart-top/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## Read the shape first

The [CRC][crc] was twenty-eight lines and one idea; the [memories][mem]
were four lines and a contract. Each fit on a screen, and each was read
*whole* --- top to bottom, every line accounted for, nothing left shut
when the post ended. That is what a leaf is: small enough to hold
entire.

`Tamal.Engine` is not small. It is five hundred and thirty-four lines
(four hundred and ten of which are actual code), and --- this is the
part that changes how we have to read it --- it *composes eight other
modules.* It calls the [CRC][crc] we already read, and seven blocks we
have not: a decoder, an ALU, a branch comparator, a register file, a
serialiser, a configuration decoder, and a pair of width aliases. Open
all of that at once, in the order the lines happen to fall, and the
shape is gone before the second page --- you are three levels deep in
a bus-timing detail with no memory of the machine it serves.

So we read it the other way. This post reads the engine's **shape** ---
its skeleton, its lifecycle, the type of the one function that is the
whole machine --- and it leaves the rooms shut. When the engine reaches
for `decode` we will say *decode turns a word into an instruction* and
walk on; when it reaches for `dataResult` we will say *that is the ALU*
and walk on. The boxes get names here and get opened later, one post
each, in the order the engine actually touches them.

This is the exact inverse of how we read the [UART][top]. There we built
*up*: the baud generator, then the transmitter, then the receiver, and
only at the end the five-line `uart` umbrella --- which could simply
*name* `oversampleTick`, `uartRx`, and `uartTx`, because by then we knew
every one of them. Leaves first, umbrella last. The engine turns that
around. We start at the umbrella --- the biggest one in the project ---
and it will name blocks we have *not* read, on purpose, because with a
machine this size the shape is the thing you need before the pieces, not
after. Outside in, not inside out.

Here is the whole map in one sentence, and the rest of the post is its
legend: **the engine is a single pure function that, once a cycle, looks
at where it is and decides what to do next.** Everything below is what
"where it is" and "what to do next" are made of.

## The widest door yet

A [Tamal] module opens by naming itself and listing what leaves through
the wall. We have read that opening five times now, and each time the
door was narrow: the [CRC][crc] exported one name, the [memories][mem]
exported two. Here is the engine's:

```haskell
module Tamal.Engine
  ( Phase (..)
  , Pending (..)
  , State (..)
  , BusIn (..)
  , BusOut (..)
  , Ring (..)
  , initState
  , powerUpDefault
  , revisionWord
  , busOut
  , step
  ) where
```

Eleven names --- the widest export list in the series --- and the first
thing to notice is that **six of them are types.** `Phase`, `Pending`,
`State`, `BusIn`, `BusOut`, `Ring`: half the door is given over not to
functions but to the *shapes of data*, each exported with the `(..)`
that says "and all of its constructors too." The [CRC][crc] exported a
verb; the engine exports mostly nouns.

That is not clutter, and it is worth pausing on, because it tells you
what kind of module this is. A leaf like `crc8Update` can hide its
types --- it takes a `BitVector 8` and gives one back, and the outside
world never needs a name for anything *inside*. The engine cannot hide
its types, because the engine does not run itself. Look at the last two
names on the list: `step`, the transition function, and `busOut`, a
projection of it. Neither is `mealy`. Neither touches a `Signal`.
`Tamal.Engine` is a *pure* description of a machine, and something one
level up --- the board's `system`, a later post --- has to lift it onto
a clock and wire its inputs and outputs to real pins. To do that, the
top must be able to *say* `State`, and `BusIn`, and `BusOut`. The types
are exported because they are the engine's contract with whatever runs
it: the wiring diagram one level up is written in these nouns. A machine
that will be assembled elsewhere has to publish the shape of its own
plugs.

## Eight closed boxes

Under the header sit the imports, and this is where reading outside in
first costs us something:

```haskell
import Clash.Prelude
import Tamal.Alu (dataResult)
import qualified Tamal.Branch as Br
import Tamal.Bus.Serdes (Lanes, hiZ, serializeX1, tarBeat)
import Tamal.Config (AlertSource (..), Config (..), IoMode (..), Role (..), Sck (..), decodeConfig)
import Tamal.Crc (crc8Update)
import Tamal.Isa (Instr (..), Reg, decode)
import Tamal.Params (AW, RW)
import Tamal.RegFile (Regs, initRegs, readReg, writeReg)
```

The [prelude swap][crc] on the first line is the same one every block has
carried --- the line that trades ordinary Haskell's furniture for the
`Signal`, `Bit`, `Vec`, and `BitVector` that lower to gates --- and I
will not re-derive it a sixth time. It is the eight lines *under* it that
are new. When we read the [UART umbrella][top], its three imports pulled
in blocks we had already opened; naming `uartRx` meant something because
we had spent a post inside it. Here, eight modules come across the wall,
and we have opened exactly *one* of them.

That one is the [CRC][crc]: `crc8Update`, the polynomial-remainder block
we read whole, arrives here to keep a running residue over the bytes the
engine reads off the bus. And one more is not quite an import of code but
of vocabulary --- `Tamal.Params (AW, RW)`, the two width aliases we
[met alongside the memories][mem]: `AW = 10` for the program counter,
`RW = 12` for the ring pointer. Those two are known quantities.

The other six are the boxes this series will open next, and it is worth
naming what each one *is* now, precisely because we are about to spend
three posts using them without looking inside:

- **`Tamal.Isa` --- `decode`.** Turns a raw 32-bit word into a typed
  `Instr`, or fails. The instruction set itself: every opcode the engine
  knows, and the reserved patterns it refuses. The engine's first act on
  any word is to call this.
- **`Tamal.Alu` --- `dataResult`.** The combinational compute for the
  DATA instructions: add, subtract, the bitwise operations, the shift.
  Give it a decoded instruction and two register values, it hands back
  the result.
- **`Tamal.Branch` --- `branchTaken`** (imported qualified as `Br`).
  The unsigned comparator behind the conditional branches: given a
  comparison and two values, is the branch taken?
- **`Tamal.RegFile` --- `readReg`, `writeReg`, `initRegs`, `Regs`.**
  The sixteen general-purpose registers, `x0` hardwired to zero, that the
  datapath reads its operands from and writes its results to.
- **`Tamal.Bus.Serdes` --- `serializeX1`, `tarBeat`, `Lanes`, `hiZ`.**
  The bit-level serialiser: how a byte becomes MSB-first drive on the IO
  lanes, what a turnaround looks like, and what "all lanes released"
  means. The closest thing to the wire.
- **`Tamal.Config` --- `decodeConfig`** and the configuration types
  (`Role`, `IoMode`, `Sck`, `AlertSource`). How a `SET_CONFIG` immediate
  becomes the role, IO width, clock rate, and alert source the engine
  runs under.

Six modules, four posts, opened in that rough order once the shape is
drawn. For now they are names with one-line jobs, and that is *enough* ---
which is the whole bet of reading outside in. You can understand what the
engine *does* with a decoder long before you understand how the decoder
works, the same way you can read a paragraph knowing a word means
"remainder" without having derived long division. Hold the eight names.
We will spend them.

## One line that is the whole machine

Skip past the type declarations for a moment --- we will come back and
read every field --- to the function they all exist to serve. Stripped of
its doc-comment, this is `step`:

```haskell
step :: State -> BusIn -> (State, BusOut, Maybe Ring)
step s inp = case phase s of
  Idle -> stepIdle s inp
  Halted -> stepHalted s inp
  Preamble -> stepPreamble s inp
  Fetch -> stepFetch s inp
  Exec -> stepExec s inp
  BusBeat -> stepBusBeat s inp
  TraceEmit -> stepTraceEmit s inp
  WaitAlert -> stepWaitAlert s inp
```

The signature is the map, and under the [primer]'s reading --- a type is
half the documentation --- it very nearly tells the whole story before
the body does. Read it as three arrows' worth of intent:

> Given the machine's current `State` and this one cycle's inputs
> (`BusIn`), produce the next `State`, the pins to drive right now
> (`BusOut`), and *maybe* one word to write into the trace ring.

That is a **Mealy machine**, the exact shape the [primer] introduced and
the [transmitter][top], [receiver][top], and [loader][loader] each wore:
a pure function from current-state-and-input to next-state-and-output,
with all the memory pulled out into that first argument and threaded back
out as the first result. What makes the engine's version the keystone is
not its shape --- it is the same `s -> i -> (s, o)` as every mealy before
it --- but its *size*, and one deliberate choice hiding in the return
type.

The choice is that the output is split in three: `(State, BusOut, Maybe
Ring)`, not the two-tuple `(State, output)` the primer's `mealy` wants.
The middle element, `BusOut`, is the pins --- everything the engine is
driving onto the world this cycle. The third, `Maybe Ring`, is a
*write*: `Just` a record to store in the trace memory, or `Nothing`, at
most one per cycle. Keeping them separate is a readability decision the
top pays back for; when the engine is finally lifted, a four-line adapter
re-bundles the pins and the write into the single output `mealy` expects,
and `step` itself is never touched.[^stepm]

And note what the body of `step` actually *is*: a `case` on `phase s` ---
one field of the state --- dispatching to eight helpers, one per phase,
and doing nothing else. `step` does not compute; it *routes*. The real
work lives in the eight `step*` functions, and the machine's entire
behaviour is "look at which phase you are in, and hand the cycle to the
helper that owns it." That is the skeleton. The rest of this post reads
the state the `case` inspects, the phases it dispatches on, and the four
simplest helpers; the next post opens `stepExec`, and the one after that
opens the three bus-and-trace phases.

One more thing the signature says by saying nothing: there is no `mealy`
here, no `register`, no `Signal` anywhere in the type. `step` is a
*pure* function --- same state and input, same three outputs, every
time. That is not an aesthetic preference; it is the reason the engine is
testable at all. Because the whole transition, bus timing and trace
emission included, is one pure function, [hedgehog] can drive it by
folding it over a list of cycles and check the results against a
reference model --- no simulator, no waveform, no clock. The [intro]
promised a design where the *entire* machine runs in a sub-second test
suite. This signature, pure to its bones, is where that promise is kept.

## Eight phases

The field the `case` inspects is `phase`, and its type is the machine's
spine:

```haskell
data Phase
  = Idle
  | Preamble
  | Fetch
  | Exec
  | BusBeat
  | TraceEmit
  | WaitAlert
  | Halted
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

Eight constructors, no fields --- a plain enumeration, the hardware
equivalent of a state register wide enough to hold eight states. This is
the lifecycle of the whole engine, and every one of the `step*` helpers
is named for exactly one of these. Read them as the stages of a life:

- **`Idle`** --- powered up, pins safe, waiting for a start trigger. The
  resting state.
- **`Preamble`** --- the first cycle of a run: write the `REVISION` word
  into the top of the trace ring so the host can confirm what bitstream
  it is talking to.
- **`Fetch`** --- the one-cycle bubble while the instruction memory
  hands back the word at the program counter. We will see exactly why
  it costs a cycle.
- **`Exec`** --- the hub: decode the fetched word and dispatch on what it
  is. Every instruction passes through here.
- **`BusBeat`** --- sequencing an SCK-timed transfer on the eSPI bus:
  the multi-cycle drive-and-sample of a `PUT`, `GET`, or turnaround.
- **`TraceEmit`** --- the second cycle of a two-word trace record (a
  `MARK`), spilling its payload word after its header.
- **`WaitAlert`** --- blocked inside a `WAIT_ON`, watching the alert
  line and counting down a timeout.
- **`Halted`** --- stopped, after a `HALT` or a trap, holding the pins
  safe and asserting a flag that tells the top to drain the ring.

The `deriving` block is the [same four-class refrain][loader] every
stateful block in the series has carried, and I will be quick with it:
`Generic` lets Clash derive the rest structurally, `Show` and `Eq` are
for the tests, and `NFDataX` is the one that earns its place --- it is
Clash's promise that a value of this type can be the contents of a
register, with a defined-enough representation to sit in a
flip-flop.[^nfdatax] A `Phase` is going to *be* a register on the fabric;
`NFDataX` is what lets it.

## The state it carries

`Phase` says *where* the machine is. The `State` record says everything
else the machine remembers --- and at seventeen fields it is the largest
record in the project. Do not read it as a list; read it as six groups,
which is how it is written:

```haskell
data State = State
  { phase :: Phase
  , pc :: Unsigned AW
  , regs :: Regs
  , cfg :: Config
  , rxCrc :: BitVector 8
  , ringPtr :: Unsigned RW
  , ovf :: Bool
  , csN :: Bit
  , sck :: Bit
  , rstN :: Bit
  , lanes :: Lanes
  , busPhase :: Index 5
  , beatIx :: Unsigned 4
  , beatTot :: Unsigned 4
  , shifter :: BitVector 8
  , pending :: Pending
  , waitTimer :: BitVector 9
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

**The lifecycle** is one field: `phase`, the enum we just read, the thing
`step` dispatches on.

**The architectural state** --- four fields --- is what a program can
see and change, the machine's equivalent of a CPU's visible registers:
`pc`, the program counter, an `Unsigned AW` indexing the instruction
store; `regs`, the sixteen general-purpose registers (a `Regs`, from the
still-shut [register file][mem]); `cfg`, the current configuration (role,
IO width, clock, alert source); and `rxCrc`, the running CRC-8 residue
over bytes read from the bus --- the accumulator the [CRC block][crc] was
built to advance, living here as engine state.

**The trace state** --- two fields --- is the outflow bookkeeping:
`ringPtr`, the next free slot in the trace ring (an `Unsigned RW`), and
`ovf`, a sticky boolean that latches high the first time a record is
dropped because the ring filled. Together they are the "drop on overflow,
never stall the bus" rule the [intro] set out, reduced to a pointer and a
flag.

**The pin latches** --- four fields --- are the outputs, *stored*:
`csN`, `sck`, `rstN`, and `lanes`. This is a deliberate choice worth
naming now and cashing later. The engine does not compute its pins fresh
each cycle from its inputs; it *holds* them in the state and updates them
on transitions. That makes them registered outputs --- glitch-free, and
depending only on where the machine is, not on what arrived this
cycle.[^moore] `lanes` is a `Lanes`, the per-lane drive-and-enable bundle
from the still-shut [serialiser][intro].

**The bus scratch** --- four fields --- is the working memory of a
transfer, meaningless outside a `BusBeat`: `busPhase`, an `Index 5` that
counts the five fabric cycles of one SCK period; `beatIx` and `beatTot`,
which bit of how many; and `shifter`, the eight-bit register that bits
are driven out of or sampled into. We will read every one of these in the
bus post; here they are just named.

**The deferred work** --- two fields --- is how a single `Exec` cycle
arranges for something to finish *later*: `pending`, a small sum type we
will meet in a moment, and `waitTimer`, the countdown a `WAIT_ON` arms.
When decoding an instruction needs an after-effect --- a register to
write once a `GET` completes, a second word to spill, an alert to wait
for --- `Exec` records it here and a later phase carries it out.

Seventeen fields, six jobs. And the shape of `Pending`, exported beside
`State`, is the catalogue of those deferred after-effects:

```haskell
data Pending
  = PendNone
  | PendGet Reg (Unsigned 4) Bool
  | PendMark (BitVector 32)
  | PendTar
  | PendWait Reg
```

`PendNone` is the resting value: nothing owed. The other four each
name a piece of work an `Exec` decode sets up and a later cycle
discharges --- a register write-back waiting on a completed `GET`, a
payload word waiting to be spilled, an in-flight turnaround, a register
waiting on an alert. Reading `Pending` in full is reading the multi-cycle
instructions in full, so it belongs to the posts that open those; for now
it is enough to see that "deferred work" has exactly five shapes, and
that the machine is never owed more than one thing at a time.

## What goes in, what comes out

Three types remain, and together they are the engine's boundary --- the
plugs the exported nouns exist to let the top wire. First, what the top
feeds in every cycle:

```haskell
data BusIn = BusIn
  { instrWord :: BitVector 32
  , ioIn :: Vec 4 Bit
  , alertIn :: Bit
  , startIn :: Bool
  }
```

`instrWord` is the word the instruction memory hands back --- and here
the [memories post][mem] pays off exactly. That post spent its length
pinning down a one-cycle read latency: ask a block RAM for the word at an
address this cycle, get it *next* cycle. `instrWord` is that word,
arriving one cycle after the engine offered its `pc` --- which is the
whole reason `Fetch` exists as a phase, as we are about to see. `ioIn` is
the four IO lanes sampled off the pads; `alertIn` is the synchronised
alert pin; `startIn` is the control-plane trigger the top holds high once
a program is loaded.

Then what the engine drives out, every cycle:

```haskell
data BusOut = BusOut
  { pcOut :: Unsigned AW
  , csOut :: Bit
  , sckOut :: Bit
  , rstOut :: Bit
  , lanesOut :: Lanes
  , haltedOut :: Bool
  , ringPtrOut :: Unsigned RW
  }
```

Every one of these is something the top wires to a pin or a memory.
`pcOut` is the fetch address handed to the instruction RAM --- the other
half of the round trip `instrWord` completes. `csOut`, `sckOut`,
`rstOut`, `lanesOut` are the eSPI pins. `haltedOut` is the flag the
[loader][loader] watches to know when to start draining. And
`ringPtrOut` is how deep the trace ring filled, so the drain knows how
much to sweep. If you read the [loader][loader] and wondered where
`startIn`, `haltedOut`, and `ringPtrOut` reached to --- this is their
other end.

And last, the write itself:

```haskell
data Ring = Ring
  { rAddr :: Unsigned RW
  , rData :: BitVector 32
  }
```

A `Ring` is one word `rData` bound for one address `rAddr` in the trace
memory. It is the third element of `step`'s output wrapped in `Maybe`,
because a cycle writes at most one trace word --- and most cycles write
none.

## The pins are a projection, not a computation

We said the pins live *in* the state. `busOut` is the function that reads
them back out, and it is deliberately the dullest function in the file:

```haskell
busOut :: State -> BusOut
busOut s =
  BusOut
    { pcOut = pc s
    , csOut = csN s
    , sckOut = sck s
    , rstOut = rstN s
    , lanesOut = lanes s
    , haltedOut = phase s == Halted
    , ringPtrOut = ringPtr s
    }
```

Read it and there is nothing to compute: five of the seven outputs are a
field of `State` copied straight across, `ringPtrOut` is one more, and
the only field that is not a bare copy is `haltedOut`, which is the
single comparison `phase s == Halted` --- the flag is not stored, it is
*derived*, true exactly when the machine is in its stopped phase. That is
the whole function.

That dullness is the point, and it is the same point the [UART
umbrella][top] made about wiring: `busOut` adds no behaviour. It is a
**projection** --- a lens that shows the outside world the fields of the
state it is allowed to see, and nothing more. Because the pins were
already decided when the state was built, presenting them costs no logic;
the outputs are a *view* of the machine, computed once, the same whether
you look this cycle or leave it be. Every `step*` helper ends by calling
`busOut` on the state it produced, and so the pins are always exactly the
state's own pins. There is no second place where an output could
disagree with the state that owns it.

## Power-up

Three small definitions set the machine's power-up contents. The first
two are the constants a fresh run starts from:

```haskell
powerUpDefault :: Config
powerUpDefault = Config Controller X1 Sck20 AlertPin

revisionWord :: BitVector 32
revisionWord = 0x00_01_0000
```

`powerUpDefault` is the configuration the engine assumes before any
`SET_CONFIG` runs: **controller** role, **x1** IO width, **20 MHz**
clock, alerts on the **pin**. These four are the design's default posture
--- the [intro]'s "controller only, single IO, one clock rate" v1,
written as a value. `revisionWord` is the version stamp `0x0001_0000` ---
major 0, minor 1, patch 0 --- that `Preamble` lays down as the first word
of every trace, so a host reading the drain can confirm the gateware
matches the tool talking to it.

Then `initState`, the whole machine at rest:

```haskell
initState :: State
initState =
  State
    { phase = Idle
    , pc = 0
    , regs = initRegs
    , cfg = powerUpDefault
    , rxCrc = 0
    , ringPtr = 1
    , ovf = False
    , csN = 1
    , sck = 0
    , rstN = 1
    , lanes = hiZ
    , busPhase = 0
    , beatIx = 0
    , beatTot = 0
    , shifter = 0
    , pending = PendNone
    , waitTimer = 0
    }
```

Read it as the answer to "what is every field before anything happens."
`phase = Idle`, waiting. The pins **safe**: `csN = 1` and `rstN = 1`
(both active-low, so both deasserted), `sck = 0`, and `lanes = hiZ` ---
all IO lanes released, driving nothing, the [serialiser][intro]'s name
for "hands off the bus." The architectural state cleared: `pc` at zero,
`regs` at `initRegs`, `cfg` at the power-up default, `rxCrc` at zero. And
one field that is *not* zero: `ringPtr = 1`, not `0`, because slot zero
of the ring is reserved for the `REVISION` word `Preamble` writes ---
the pointer starts one past it, at the first slot a program's own records
may use.

There is a larger design decision folded into `initState` existing at
all, and it is one the [intro] flagged: [Tamal] has **no reset port.**
The top ties reset permanently deasserted and leans on power-up `init`,
the way the sibling [Clash] examples do. So `initState` is not what a
reset line loads --- there is no reset line --- it is what the registers
*power up* holding. Which raises the obvious question: if there is no
reset, how does a *second* program run after the first one halts? The
answer is the next section.

## The four trivial phases

Four of the eight `step*` helpers do so little that we can read them all
here and leave the substantial four --- `Exec`, and the three bus phases
--- for the posts that own them. Start with the two resting states,
`Idle` and `Halted`, which are almost the same function written twice:

```haskell
stepIdle :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepIdle s inp
  | startIn inp = (softInit, busOut s, Nothing)
  | otherwise = (s, busOut s, Nothing)

stepHalted :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepHalted s inp
  | startIn inp = (softInit, busOut s, Nothing)
  | otherwise = (s, busOut s, Nothing)
```

Both say the same thing: **hold until told to start.** With `startIn`
low, each returns the state it was handed, unchanged --- `(s, busOut s,
Nothing)`, the machine standing still, driving its stored pins, writing
nothing. When `startIn` goes high, both hand back `softInit`, and that is
where a run begins. The only difference between the two is *which* pins
they hold while waiting: `Idle` holds the safe power-up pins, `Halted`
holds the safe pins a halt left behind --- but the response to `start` is
identical, which is exactly why re-running works.

`softInit` is that response, and it is one line:

```haskell
softInit :: State
softInit = initState{phase = Preamble}
```

It is `initState` --- the entire power-up state, every field back to its
rest value --- with the one change that the phase is `Preamble` rather
than `Idle`. So `start`, from `Idle` *or* from `Halted`, throws the whole
machine back to power-up and points it at the preamble. This is the
answer to the no-reset question: a second run does not need a reset line
because `softInit` *is* the reset, performed in the datapath, synchronous
and total. Every architectural field, the ring pointer, the overflow
flag, the config --- all of it returns to the same values it powered up
with, which means a re-run is **byte-for-byte identical** to a first run.
Load a program, run it, halt, pulse `start`, and you get the same trace
you got the first time, with no power cycle and no reset pin. The
determinism the [intro] promised falls out of one record update.

Then `Preamble`, the first cycle of any run:

```haskell
stepPreamble :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepPreamble s _ = (s{phase = Fetch}, busOut s, Just (Ring 0 revisionWord))
```

It ignores its input (`_`), advances the phase to `Fetch`, and --- the
one thing it does --- emits `Just (Ring 0 revisionWord)`: write the
revision stamp to slot `0` of the trace ring. That is why `ringPtr`
started at `1`; slot `0` is spoken for, here, on the first cycle, so that
the very first word a host reads back is always the version of the
machine that produced the trace. One cycle, one write, and on to
`Fetch`.

And `Fetch` itself, the shortest of all:

```haskell
stepFetch :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepFetch s _ = (s{phase = Exec}, busOut s, Nothing)
```

It does *nothing* but advance to `Exec`. No write, no state change beyond
the phase. And this apparent do-nothing is the one-cycle bubble the
[memories][mem] warned us to expect. The program counter `pc` is offered
to the instruction RAM as `pcOut`; the RAM's read latency means the word
at that address --- `instrWord` --- does not arrive until the *next*
cycle. `Fetch` is the machine spending that cycle on purpose: it steps to
`Exec`, and by the time `Exec` runs, `instrWord` in its `BusIn` is valid
and ready to decode. The bubble is not waste; it is the phase that lets a
synchronous memory be the program store. One cycle of patience, bought
knowingly, exactly as the [memories post][mem] said it would be.

Put the four together and the outer loop of the machine is already
visible: `Idle` waits; `start` soft-inits to `Preamble`; `Preamble`
stamps the revision and goes to `Fetch`; `Fetch` waits a cycle for the
RAM and goes to `Exec`. What `Exec` does --- and how it loops back to
`Fetch` for the next instruction, or detours through the bus phases, or
lands in `Halted` --- is the rest of the engine, and the rest of this
arc. Here is the shape it all hangs on:

<figure class="engfsm-fig" style="margin:2rem 0">
<svg class="engfsm" viewBox="0 -52 800 342" role="img" aria-labelledby="engfsm-t engfsm-d" xmlns="http://www.w3.org/2000/svg">
<title id="engfsm-t">The tamal engine lifecycle: eight phases and their transitions</title>
<desc id="engfsm-d">Five phases sit on a horizontal spine: Idle, Preamble, Fetch, Exec, Halted, left to right. A start arrow leads from Idle to Preamble; Preamble writes the REVISION word and goes to Fetch; Fetch, the one-cycle memory bubble, goes to Exec; Exec goes right to Halted on HALT or TRAP. The main cycle is an accent-coloured arc from Exec back to Fetch, labelled op done, PC plus one or PC plus offset. Below Exec a dashed group holds three multi-cycle phases opened in a later post: BusBeat, TraceEmit, and WaitAlert; one arrow leads down from Exec into the group for PUT, GET, TAR, MARK, and WAIT_ON, and one arrow leads back up from the group to Fetch when the op is done. A long arc over the top returns from Halted to Preamble, labelled start, a soft-init re-run.</desc>
<style>
.engfsm{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.engfsm .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.engfsm .boxA{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.engfsm .sub{fill:var(--bg-main);stroke:var(--fg-main);stroke-width:1.5}
.engfsm .mbox{fill:none;stroke:var(--fg-dim);stroke-width:1.5;stroke-dasharray:6 5}
.engfsm .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.engfsm .accw{stroke:var(--accent);stroke-width:2.5;fill:none}
.engfsm .name{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.engfsm .sub-name{fill:var(--fg-main);font-family:var(--mono);font-size:12.5px}
.engfsm .lab{fill:var(--fg-dim);font-family:var(--sans);font-size:12px}
.engfsm .labA{fill:var(--accent);font-family:var(--sans);font-size:12.5px}
.engfsm .mlab{fill:var(--fg-dim);font-family:var(--mono);font-size:11.5px}
.engfsm .ah{fill:var(--fg-main)}
.engfsm .aha{fill:var(--accent)}
</style>
<defs>
<marker id="ef-a" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="ah"/></marker>
<marker id="ef-aa" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- main cycle arc: Exec -> Fetch -->
<path class="accw" d="M556,70 C556,30 364,30 364,70" marker-end="url(#ef-aa)"/>
<text class="labA" x="460" y="24" text-anchor="middle">op done: PC + 1 / PC + off</text>
<!-- spine boxes -->
<rect class="box" x="18" y="70" width="92" height="44" rx="6"/>
<text class="name" x="64" y="97" text-anchor="middle">Idle</text>
<rect class="box" x="158" y="70" width="92" height="44" rx="6"/>
<text class="name" x="204" y="97" text-anchor="middle">Preamble</text>
<rect class="boxA" x="318" y="70" width="92" height="44" rx="6"/>
<text class="name" x="364" y="97" text-anchor="middle">Fetch</text>
<rect class="boxA" x="510" y="70" width="92" height="44" rx="6"/>
<text class="name" x="556" y="97" text-anchor="middle">Exec</text>
<rect class="box" x="690" y="70" width="92" height="44" rx="6"/>
<text class="name" x="736" y="97" text-anchor="middle">Halted</text>
<!-- spine arrows -->
<line class="wire" x1="110" y1="92" x2="156" y2="92" marker-end="url(#ef-a)"/>
<text class="lab" x="133" y="84" text-anchor="middle">start</text>
<line class="wire" x1="250" y1="92" x2="316" y2="92" marker-end="url(#ef-a)"/>
<text class="lab" x="283" y="84" text-anchor="middle">REVISION</text>
<line class="accw" x1="410" y1="92" x2="508" y2="92" marker-end="url(#ef-aa)"/>
<text class="lab" x="459" y="84" text-anchor="middle">instr valid</text>
<line class="wire" x1="602" y1="92" x2="688" y2="92" marker-end="url(#ef-a)"/>
<text class="lab" x="645" y="84" text-anchor="middle">HALT / TRAP</text>
<!-- multi-cycle group -->
<rect class="mbox" x="292" y="190" width="352" height="82" rx="10"/>
<text class="mlab" x="302" y="206" text-anchor="start">multi-cycle phases — opened in part 3</text>
<rect class="sub" x="300" y="214" width="100" height="42" rx="5"/>
<text class="sub-name" x="350" y="240" text-anchor="middle">BusBeat</text>
<rect class="sub" x="418" y="214" width="104" height="42" rx="5"/>
<text class="sub-name" x="470" y="240" text-anchor="middle">TraceEmit</text>
<rect class="sub" x="538" y="214" width="100" height="42" rx="5"/>
<text class="sub-name" x="588" y="240" text-anchor="middle">WaitAlert</text>
<!-- Exec down into group -->
<line class="wire" x1="556" y1="114" x2="556" y2="188" marker-end="url(#ef-a)"/>
<text class="lab" x="548" y="150" text-anchor="end">PUT / GET / TAR,</text>
<text class="lab" x="548" y="166" text-anchor="end">MARK, WAIT_ON</text>
<!-- group back up to Fetch -->
<path class="wire" d="M360,190 C360,150 364,150 364,116" marker-end="url(#ef-a)"/>
<text class="lab" x="322" y="150" text-anchor="end">done</text>
<!-- Halted re-run back to Preamble, routed over the top -->
<path class="wire" d="M736,70 C736,-46 204,-46 204,70" marker-end="url(#ef-a)"/>
<text class="lab" x="470" y="-32" text-anchor="middle">start — soft-init re-run</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The whole engine as a lifecycle. The spine reads left to right: <code>Idle</code> waits for <code>start</code>; <code>Preamble</code> stamps the <code>REVISION</code> word and falls to <code>Fetch</code>; <code>Fetch</code> spends the one-cycle memory bubble and hands a valid instruction to <code>Exec</code>. The accent arc is the main loop --- <code>Exec</code> back to <code>Fetch</code>, one instruction retired, <code>PC</code> advanced. The multi-cycle instructions detour through the dashed group below (<code>BusBeat</code>, <code>TraceEmit</code>, <code>WaitAlert</code>) and return to <code>Fetch</code> when they finish; those three phases are the subject of a later post. <code>HALT</code> or a trap leaves the spine for <code>Halted</code>, and a fresh <code>start</code> soft-inits all the way back to <code>Preamble</code> for a byte-identical re-run. This post read the spine and the two resting states; the arc through <code>Exec</code> and the detour through the group are what remains.</figcaption>
</figure>

## What we read

The last door in the series, opened just wide enough to see its plan. Not
the way we opened a leaf --- whole, in a screenful --- but the way you
open a machine too big to hold at once: shape first, rooms shut. We read
the widest export list yet and found six of its eleven names were
*types*, because an engine assembled one level up has to publish the
shape of its own plugs. We read eight imports and opened only the one we
already knew, holding the other seven as named boxes --- decode, ALU,
branch, registers, serialiser, config --- to be spent now and opened
later, the exact inversion of the [UART][top]'s leaves-first climb.

We read the one line that is the whole machine: `step`, a pure Mealy
transition from state-and-input to state-and-pins-and-maybe-a-write,
routing each cycle to one of eight phase helpers and computing nothing
itself. We read the spine those helpers hang on --- eight phases, `Idle`
through `Halted` --- and the seventeen-field `State` they thread, in its
six groups, its pins deliberately *stored* so that `busOut` can be a dull
and honest projection rather than a computation. We read the boundary
types that plug into the [memories][mem] and the [loader][loader], and
saw the one-cycle fetch latency reappear as a phase spent on purpose. And
we read the four trivial helpers: two resting states that hold until
`start`, a preamble that stamps a revision, and a fetch that waits a beat
for a block RAM --- and, hiding in `softInit`, the reset that a machine
with no reset port performs in its own datapath, byte-for-byte repeatable.

What we did *not* read is the machine's actual work. `Exec` is still a
name in a `case`; the seven boxes are still shut; the bus scratch fields
have been named and not used. That is on purpose. The shape is drawn now
--- the loop from `Fetch` to `Exec` and back, the detours below it, the
exits to `Halted` --- and against that shape the work will make sense.

Next we stand in `Exec` and watch a single instruction go by: one word,
one cycle, decoded and dispatched --- the DATA compute, the branch and
its PC arithmetic, the pin and config pokes --- with the leaves it calls
still treated as the black boxes we promised to open after. The map is
drawn. Now we walk the first road on it.

[^stepm]: The adapter is the four lines the [PLAN][Tamal] calls `stepM`:
`stepM s i = let (s', bo, mr) = step s i in (s', (bo, mr))`, and then
`engine = mealy stepM initState`. All it does is glue the second and
third elements of `step`'s output into one pair, because `mealy` wants a
single output value, not two. Splitting them in `step` keeps the pure
function readable --- pins here, trace write there --- and paying a
four-line tax at the one place it is lifted is cheaper than blurring the
two everywhere they are produced. It is the same "keep the core pure, let
the shell adapt" seam the whole design is built on.

[^nfdatax]: `NFDataX` is [Clash]'s "can be stored in a register" class ---
roughly, a type whose values have a normal form Clash can reason about,
including how they behave when partly undefined at power-up. Every type
that lands in a flip-flop needs it: `Phase`, `Pending`, `State`, all of
them. It is derived `anyclass` --- the compiler writes the instance from
the type's structure --- while `Generic`, `Show`, and `Eq` are derived
`stock`, the built-in strategy. The split into two `deriving` clauses is
just Haskell keeping the two derivation mechanisms apart; the four
classes together are the standard kit every stateful [Tamal] block wears.

[^moore]: In the strict vocabulary this makes the *pins* a Moore output ---
a function of the state alone, `busOut s`, with no path from this cycle's
`BusIn` to this cycle's pins --- while the next state and the `Maybe Ring`
write remain Mealy, since they do depend on the input. Driving the pins
from stored state rather than combinationally from the input is what
keeps them glitch-free: they change only on a clock edge, when the state
updates, never in the middle of a cycle because an input wiggled.
