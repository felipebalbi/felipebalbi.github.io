+++
title = "Tamal: The receiver"
date = 2026-07-23T09:00:00
description = "Reading Tamal's UART receiver end to end: the transmitter's mirror, where the serial line is now an input arriving asynchronously from a pin and a 2-flop synchronizer must tame it before the logic dares look, where the start bit is found by its falling edge rather than driven on command, where each bit is decided not by one sample but by a 3-sample majority vote taken at the bit center on oversample counts 7, 8 and 9 — the actual point of oversampling — where a low stop bit raises a framing error and drops the byte, where the stop bit resolves early at count 9 so back-to-back frames still resync, where the byte-ready and framing-error outputs are genuinely Mealy strobes rather than the transmitter's steady Moore levels, and where a byte-exact loopback with the real fractional-3.125 tick finally closes the keystone the baud generator opened."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "uart", "state-machine", "receiver"]
[extra]
math = true
+++

The [transmitter][tx] we read yesterday *drove* a wire. It owned the
line: it held each level for sixteen ticks, and it made every output a
pure function of its own state, so the serial output sat rock-steady
between bit boundaries and never once twitched. That was a luxury, and
we spent most of the post admiring it. The receiver is the mirror of
that module --- and a mirror, it turns out, is not a reflection so much
as the harder half. **The transmitter drove a wire; the receiver must
read one it does not control.**

That single inversion --- *drive* becomes *read* --- is where every new
idea in this post comes from, and it is worth saying up front which
parts of the receiver are genuinely new and which are just the
transmitter run backwards. The skeleton is the same: the same
four-constructor `RxState`, the same record-of-fields carried tick to
tick, the same `Index 16` counting sixteen oversample ticks to a bit,
the same `mealy` at the top of the module, the same freeze-off-tick
cadence, the same LSB-first convention. If that were all, this post
would be short and I would send you back to re-read the [transmitter][tx]
with the names changed. It is not all. The interesting parts of the
receiver are precisely the parts that are *not* "TX in reverse," and
there are five of them:

- The line is an **input** now, arriving asynchronously from a pin, so
  the very first thing the module does is register it twice --- a
  **2-flop synchronizer** against metastability, a thing the transmitter
  never needed because it drove the wire itself.
- The start bit is not commanded but **hunted for**, and hunted for by
  its *falling edge* rather than its mere low level --- an edge, so a
  line simply held low can never fake a frame.
- The bit is not held but **sampled**, and sampled carefully: at the
  *center* of the bit-time, three times, and resolved by a **majority
  vote**. This is the whole point of oversampling sixteen times, and it
  is the richest section of the post.
- A stop bit that comes back low is a **framing error** --- the byte is
  dropped and a flag is raised. The transmitter had no notion of a frame
  going wrong; the receiver must.
- The outputs are **genuinely Mealy** --- one-cycle strobes that read the
  `tick` input --- where the transmitter's were Moore levels. This is the
  sharpest contrast between the two halves, and the one the
  [transmitter][tx] post set us up to notice.

Like the transmitter, the whole receiver fits in a screenful and change.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[tx]: https://balbi.sh/posts/tamal-uart-tx/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the license header and the doc-comments --- but keeping the short
inline comments, because on this module they carry real reasoning ---
here is `src/Tamal/Uart/Rx.hs` in full:

```haskell
module Tamal.Uart.Rx
  ( uartRx
  ) where

import Clash.Prelude

data RxState = RxIdle | RxStart | RxData (Index 8) | RxStop
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data RxS = RxS
  { rxState :: RxState
  , rxCnt :: Index 16
  , rxShift :: BitVector 8
  , rxS7 :: Bit
  , rxS8 :: Bit
  , rxS9 :: Bit
  , rxPrev :: Bit
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

uartRx ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bool ->
  Signal dom Bit ->
  (Signal dom (Maybe (BitVector 8)), Signal dom Bool)
uartRx tick rxLine = unbundle (mealy rxStep initRx (bundle (tick, synced)))
 where
  -- 2-flop synchronizer, clocked every cycle (not tick-gated); idle line is high.
  sync1 = register high rxLine
  synced = register high sync1
  initRx = RxS RxIdle 0 0 0 0 0 high

maj :: Bit -> Bit -> Bit -> Bit
maj a b c = (a .&. b) .|. (a .&. c) .|. (b .&. c)

captureSample :: RxS -> Bit -> RxS
captureSample s bit' = case rxCnt s of
  7 -> s{rxS7 = bit'}
  8 -> s{rxS8 = bit'}
  9 -> s{rxS9 = bit'}
  _ -> s -- actually unreachable

decideBit :: RxS -> (RxS, (Maybe (BitVector 8), Bool))
decideBit s = case rxState s of
  RxStart
    | bit' == low -> (s{rxState = RxData 0, rxCnt = 0}, (Nothing, False))
    | otherwise -> (s{rxState = RxIdle, rxCnt = 0}, (Nothing, False))
  RxData i ->
    let sh =
          if bit' == high
            then setBit (rxShift s) (fromEnum i)
            else clearBit (rxShift s) (fromEnum i)
        s' = s{rxShift = sh, rxCnt = 0}
     in if i == maxBound
          then (s'{rxState = RxStop}, (Nothing, False))
          else (s'{rxState = RxData (i + 1)}, (Nothing, False))
  RxStop
    | bit' == high -> (s{rxState = RxIdle, rxCnt = 0}, (Just (rxShift s), False))
    | otherwise -> (s{rxState = RxIdle, rxCnt = 0}, (Nothing, True))
  RxIdle -> (s, (Nothing, False)) -- actually unreachable
 where
  bit' = maj (rxS7 s) (rxS8 s) (rxS9 s)

rxStep :: RxS -> (Bool, Bit) -> (RxS, (Maybe (BitVector 8), Bool))
rxStep s (tick, line)
  | not tick = (s, (Nothing, False)) -- only move on oversample ticks
  | otherwise =
      let s0 = s{rxPrev = line} -- remember this tick's level for next tick
       in case rxState s of
            RxIdle
              | rxPrev s == high && line == low -> (s0{rxState = RxStart, rxCnt = 0}, (Nothing, False))
              | otherwise -> (s0, (Nothing, False))
            RxStop ->
              -- Resolve the stop bit at its center sample (count 9) rather than
              -- waiting out the whole window: returning to idle ~6 ticks early
              -- gives the falling-edge detector idle-high ticks to arm rxPrev,
              -- so a back-to-back next frame (stop immediately followed by start)
              -- still resyncs.
              let s1 = captureSample s0 line
               in if rxCnt s == 9
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
            _ ->
              -- RxStart / RxData
              let s1 = captureSample s0 line
               in if rxCnt s == maxBound
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
```

A dozen more lines than the transmitter, and every one of the extra
lines is one of the five new ideas. Top to bottom: the two `data`
declarations that are the machine's memory --- the transmitter's two
types, mirrored, with four new fields bolted on; the six-line `uartRx`,
which is the transmitter's `mealy` lift with a synchronizer soldered in
front of it; a tiny `maj` that is the majority vote; a `captureSample`
that stashes the three center samples; a `decideBit` that turns those
samples into a byte or an error; and `rxStep`, the transition, which is
where the falling-edge hunt and the early stop resolution live. We will
read them in roughly that order, spending almost all of our words on the
five things the transmitter never had to do.

The module header we can wave past. The [CRC][crc], [baud
generator][baudgen], and [transmitter][tx] posts each dwelt on the same
opening beat --- the single-name export list sealing everything private
behind it, and the `import Clash.Prelude` swap that trades ordinary
Haskell's furniture for `Signal`, `Bit`, `register`, and `mealy` --- and a
fourth reading would teach nothing. The one detail worth a glance is an
*absence*: there is no `{-# LANGUAGE NumericUnderscores #-}`, because the
receiver names no clock frequency and converts no baud rate the way the
[baud generator][baudgen] did. Its idea is not *timing* but *recovery* ---
given a wire it did not schedule, find the bits.

## The type: a line in, a byte out

The signature is the transmitter's, read in a mirror:

```haskell
uartRx ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bool ->
  Signal dom Bit ->
  (Signal dom (Maybe (BitVector 8)), Signal dom Bool)
```

Set it beside `uartTx` and the symmetry is almost too neat. The
transmitter took a tick and a `Maybe (BitVector 8)` and returned a `Bit`
and a `Bool`; the receiver takes a tick and a `Bit` and returns a `Maybe
(BitVector 8)` and a `Bool`. The byte and the wire have swapped ends ---
same lone `HiddenClockResetEnable` constraint, same numberless type. The
`Signal dom Bool` tick is the [baud generator's][baudgen] enable plugged
in exactly as it was for the transmitter, the receiver being the *other*
block that enable was built to gate. The rest of the signature is where
the mirror starts to bend.

`Signal dom Bit` is the RX line --- and this is the first genuinely new
thing in the type. For the transmitter, `Bit` was an *output*, a wire it
drove. Here `Bit` is an *input*, a wire it reads, and the comment on the
matching argument in the [design doc][baudgen] is the whole story in
four words: *asynchronous; synchronized internally*. This wire arrives
from a physical pin, from a host whose clock has no relationship to
Tamal's. Its edges fall wherever they fall. The receiver cannot assume
it is stable at the clock edge, cannot assume it will not change while a
flip-flop is trying to latch it, and so the very first thing `uartRx`
does --- before the state machine ever sees the line --- is register it
twice. We will get to that in a moment; the type is already warning us.

The result, `(Signal dom (Maybe (BitVector 8)), Signal dom Bool)`, is a
pair of output wires that are the mirror of the transmitter's:

- `Signal dom (Maybe (BitVector 8))` is the received byte, and the
  `Maybe` is doing the [primer]'s job again. `Just b` on a cycle means
  "a whole frame just landed and here is its byte"; `Nothing`, which is
  the value on the overwhelming majority of cycles, means "nothing
  completed this cycle." It is a one-cycle **strobe**, an event, not a
  held register --- and that word, *strobe*, is going to matter enormously
  when we ask whether these outputs are Moore or Mealy.
- `Signal dom Bool` is the framing-error flag, likewise a one-cycle
  strobe, raised on the single cycle a frame ends without a valid stop
  bit. Where the transmitter's second output was `ready` --- a *level*,
  high for as long as the machine idled --- the receiver's is an
  *error* --- a pulse, high for exactly one cycle when a frame goes bad.

So the interface reads: *here is a heartbeat and a wire I do not
control; take the bytes I recover from it and a flag for the frames that
came apart.* Two strobes out, no back-pressure, no `ready` --- because a
receiver cannot ask the host to slow down anyway. The bytes arrive when
they arrive; the strobe simply announces them.

## Two types, mirrored --- and four new fields

Everything the receiver remembers lives in two `data` declarations, and
the first of them is the transmitter's phase type with the tags renamed:

```haskell
data RxState = RxIdle | RxStart | RxData (Index 8) | RxStop
```

Read against `TxState` it is identical in shape --- the same four
constructors, the same eleven inhabitants once `RxData (Index 8)` fans
into eight. What differs is only what each phase *does*: where `TxStart`
drove the line low, `RxStart` *confirms* a start bit it thinks it saw;
where `TxData i` drove bit `i` out, `RxData i` shifts bit `i` in; where
`TxStop` drove a high stop bit, `RxStop` *checks* whether the stop bit
really came back high. Same skeleton, opposite verbs --- drive versus
read, the inversion the whole post turns on. The `deriving` block is the
[primer]'s incantation unchanged, read in full in the [transmitter][tx]
post.

The second type is where the receiver stops being a mirror. The
transmitter's record had three fields; the receiver's has seven:

```haskell
data RxS = RxS
  { rxState :: RxState
  , rxCnt :: Index 16
  , rxShift :: BitVector 8
  , rxS7 :: Bit
  , rxS8 :: Bit
  , rxS9 :: Bit
  , rxPrev :: Bit
  }
```

The first three are `TxS` again, one of them reordered. `rxState` is the
phase we just met. `rxCnt :: Index 16` is the position within the current
bit, counting the sixteen oversample ticks --- `0..15`, never 16, the
width load-bearing exactly as it was for `txCnt`. `rxShift :: BitVector
8` is the byte, though here it is *assembled* rather than *emitted* ---
bits are written into it as they arrive, not shifted out of it. Three
fields, and if the receiver were merely "TX in reverse" that would be the
end of the record.

It is not the end. Four more fields carry the ideas the transmitter
never needed:

- **`rxS7`, `rxS8`, `rxS9`**, three lone `Bit`s, are the three samples of
  the line taken at oversample counts 7, 8, and 9 --- the three readings
  around the center of the bit that the majority vote will resolve into a
  decision. The transmitter *drove* a level and knew what it was; the
  receiver must *read* a level it does not know, and reads it three times
  to be sure. These three flip-flops are the physical memory of "I looked
  at the wire near the middle of this bit and here is what I saw, three
  times." Nothing in `TxS` corresponds to them, because nothing in the
  transmitter ever had to look.
- **`rxPrev`**, one more `Bit`, is the synchronized line as it stood at
  the *previous* oversample tick. It exists for one purpose: so that the
  idle state can trigger on a high-to-low *edge* rather than a low
  *level*. To notice an edge you must remember where you were, and
  `rxPrev` is that memory --- last tick's line, held so this tick's line
  can be compared against it. Again, the transmitter had no analogue: it
  chose when the start bit began, so it never had to detect the moment
  one arrived.

Add it up and the receiver's memory is a phase, a four-bit counter, a
byte, three sampled bits, and one remembered bit --- some twenty-odd bits
of flip-flop against the transmitter's fifteen. Every extra bit buys one
of the jobs that reading-a-wire-you-do-not-own demands. The initial
value wires them all to their resting state:

```haskell
initRx = RxS RxIdle 0 0 0 0 0 high
```

Read positionally against the record: `rxState = RxIdle`, `rxCnt = 0`,
`rxShift = 0`, `rxS7 = rxS8 = rxS9 = 0`, and --- the one non-zero ---
`rxPrev = high`. That last is not an accident. The idle line sits high,
so at power-up the receiver must *believe* the line was high a tick ago;
otherwise its very first observation of a genuine high line would look
like no change, or worse, a spurious edge. Priming `rxPrev` to `high` is
the software equivalent of assuming the wire was idle before we started
watching --- which, on a UART, it was.

<figure class="rxfsm-fig" style="margin:2rem 0">
<svg class="rxfsm" viewBox="0 0 760 288" role="img" aria-labelledby="rxfsm-t rxfsm-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxfsm-t">The receiver's RxState finite-state machine</title>
<desc id="rxfsm-d">Four state nodes in a row: RxIdle (await edge), RxStart (confirm start), RxData i (assemble bit i), and RxStop (test stop). RxIdle has a dashed self-loop labelled no edge, meaning it stays idle while the line does not fall. An accented arrow leaves RxIdle for RxStart labelled high-to-low edge, the falling-edge trigger. A back arrow from RxStart to RxIdle is labelled vote high implies false start, the glitch rejection. RxStart advances to RxData after count 15 when the vote is low; RxData has a self-loop that votes and writes bit i with setBit while i is less than seven; after bit seven it advances to RxStop; RxStop returns to RxIdle early at count 9, and the received-byte and framing-error strobes fire on that stop resolution.</desc>
<style>
.rxfsm{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxfsm .st{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rxfsm .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxfsm .hs{stroke:var(--accent);stroke-width:2.5;fill:none}
.rxfsm .loop{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxfsm .idleloop{stroke:var(--fg-dim);stroke-width:2;fill:none;stroke-dasharray:4 4}
.rxfsm text{font-family:var(--sans)}
.rxfsm .name{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.rxfsm .lab{fill:var(--fg-main);font-size:12px}
.rxfsm .labA{fill:var(--accent);font-size:12px}
.rxfsm .dim{fill:var(--fg-dim);font-size:11.5px}
.rxfsm .ah{fill:var(--fg-main)}
.rxfsm .aha{fill:var(--accent)}
</style>
<defs>
<marker id="rxfsm-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="rxfsm-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<rect class="st" x="40" y="118" width="96" height="54" rx="8"/>
<rect class="st" x="236" y="118" width="96" height="54" rx="8"/>
<rect class="st" x="432" y="118" width="96" height="54" rx="8"/>
<rect class="st" x="628" y="118" width="96" height="54" rx="8"/>
<path class="idleloop" d="M72,118 C69,92 103,92 100,118" marker-end="url(#rxfsm-a)"/>
<path class="loop" d="M464,118 C460,90 496,90 492,118" marker-end="url(#rxfsm-a)"/>
<line class="hs" x1="136" y1="145" x2="234" y2="145" marker-end="url(#rxfsm-aa)"/>
<line class="wire" x1="332" y1="145" x2="430" y2="145" marker-end="url(#rxfsm-a)"/>
<line class="wire" x1="528" y1="145" x2="626" y2="145" marker-end="url(#rxfsm-a)"/>
<path class="wire" d="M252,118 L252,76 L120,76 L120,116" marker-end="url(#rxfsm-a)"/>
<path class="wire" d="M676,172 L676,232 L88,232 L88,174" marker-end="url(#rxfsm-a)"/>
<text class="name" x="88" y="141" text-anchor="middle">RxIdle</text>
<text class="name" x="284" y="141" text-anchor="middle">RxStart</text>
<text class="name" x="480" y="141" text-anchor="middle">RxData i</text>
<text class="name" x="676" y="141" text-anchor="middle">RxStop</text>
<text class="dim" x="88" y="160" text-anchor="middle">await edge</text>
<text class="dim" x="284" y="160" text-anchor="middle">confirm start</text>
<text class="dim" x="480" y="160" text-anchor="middle">assemble bit i</text>
<text class="dim" x="676" y="160" text-anchor="middle">test stop</text>
<text class="dim" x="86" y="86" text-anchor="middle">no edge</text>
<text class="lab" x="186" y="70" text-anchor="middle">vote high ⇒ false start</text>
<text class="labA" x="185" y="138" text-anchor="middle">high→low edge</text>
<text class="dim" x="185" y="163" text-anchor="middle">not a level</text>
<text class="lab" x="480" y="82" text-anchor="middle">vote → setBit i, i&lt;7</text>
<text class="lab" x="380" y="138" text-anchor="middle">count 15, vote low</text>
<text class="lab" x="577" y="138" text-anchor="middle">bit 7 done</text>
<text class="dim" x="382" y="250" text-anchor="middle">count 9 — resolve early, back to idle</text>
<text class="labA" x="382" y="268" text-anchor="middle">byte / framing-error strobe fires here</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The receiver's eleven states as an FSM — the <a href="https://balbi.sh/posts/tamal-uart-tx/">transmitter's</a> diagram read in a mirror, with two genuinely new edges. The accented transition out of <code>RxIdle</code> fires on a high→low <em>edge</em> (tracked through <code>rxPrev</code>), not a low level, so a line simply held low cannot fake a frame. The back-edge <code>RxStart → RxIdle</code> is glitch rejection: if the confirming vote comes back high, the "start" was noise and we abandon it. <code>RxData i</code>'s self-loop votes the three center samples and writes bit <code>i</code> with <code>setBit</code>/<code>clearBit</code>. All transitions are tick-gated (they advance only on an oversample tick); <code>RxStart</code> and <code>RxData</code> resolve at the end of the 16-tick window (count 15), but <code>RxStop</code> resolves <em>early</em>, at its center sample (count 9), and it is there that the byte and framing-error strobes fire.</figcaption>
</figure>

## The same silhouette

Here is the line that lifts the whole thing into hardware, and it is the
transmitter's line wearing a hat:

```haskell
uartRx tick rxLine = unbundle (mealy rxStep initRx (bundle (tick, synced)))
 where
  sync1 = register high rxLine
  synced = register high sync1
  initRx = RxS RxIdle 0 0 0 0 0 high
```

Strip the `where` for a second and the outer shape is *exactly*
`uartTx`'s: `unbundle (mealy rxStep initRx (bundle (tick, ...)))`, the
register-plus-pure-function machine the [transmitter][tx] post read at
length --- `mealy` clocking a pure step, `bundle`/`unbundle` zipping the
several signals into the one the combinator wants and back. The drill is
unchanged, so I will not run it again: find the `mealy`, find the step
function beside it, and `rxStep` is the brain.

What *is* new is one substitution, and it is the whole reason this
section exists. Look at what gets bundled:

```haskell
mealy rxStep initRx (bundle (tick, synced))
```

Not `bundle (tick, rxLine)` --- `bundle (tick, synced)`. The state
machine is not fed the raw line at all. It is fed `synced`, a signal that
does not appear in the argument list, defined in the `where` block by two
`register`s standing between the pin and the logic. That little
substitution --- `rxLine` in the type, `synced` into the machine --- is
the 2-flop synchronizer, and it is the first genuinely new idea in the
receiver. It deserves its own section.

## The line is now an input: the 2-flop synchronizer

Two lines of the `where` block are the entire mechanism:

```haskell
  sync1 = register high rxLine
  synced = register high sync1
```

`rxLine` --- the asynchronous wire from the pin --- goes into a `register`,
whose output `sync1` goes into a second `register`, whose output `synced`
is what the state machine reads. Two flip-flops in series, both
initialised `high`, and the line is delayed by two clock cycles before
any logic touches it. That is the whole thing. It is also one of the most
important two lines in the codebase, because it is the guard against
**metastability**, and metastability is the failure mode that a
transmitter, driving its own wire, is simply never exposed to.[^metastability]

Here is the problem it solves. A flip-flop is a promise: sample the input
at the clock edge, hold it steady until the next edge. But the promise
has fine print --- the input must itself be steady for a small window
around the clock edge, a *setup* time before and a *hold* time after.
Meet that window and the flip-flop resolves cleanly to `0` or `1`. Miss
it --- change the input *while* the edge is happening --- and the flip-flop
can enter a **metastable** state: its output hovers at neither level, a
voltage stuck halfway, for an unbounded and unpredictable time before it
eventually, randomly, falls to `0` or `1`. A transmitter never risks this
because it changes its line in step with its own clock; the setup and
hold windows are honoured by construction. A receiver has no such luck.
The host's line changes when the *host's* clock says so, which bears no
relationship to Tamal's clock, so sooner or later a bit edge will land
exactly on a Tamal clock edge and the first flip-flop that samples it
will go metastable. There is no avoiding that first strike. The line is
asynchronous; asynchronous means "will, eventually, violate setup/hold";
and no amount of cleverness changes it.

What you *can* do is contain the damage, and that is what the second
flip-flop is for. When `sync1` goes metastable, you give it one whole
clock cycle --- the time between this edge and the next --- to settle,
untouched by any logic, before `synced` samples it. A clock period is an
eternity next to the time a flip-flop stays balanced on the knife-edge;
the probability that `sync1` is *still* undecided a full cycle later is
vanishingly small, and `synced` almost certainly captures a clean `0` or
`1`. "Almost certainly" is the honest phrase --- two flops do not make
metastability impossible, they push its mean-time-between-failures out to
centuries, which for a hobby FPGA link is the same as impossible. The
cost is two flip-flops and two cycles of latency, and the receiver
happily pays it: two cycles is nothing against a bit that is fifty clocks
wide.[^metastability]

Two details reward a second look. The first is that these two `register`s
are clocked on **every single cycle** --- they are *not* tick-gated. This
is the sharpest possible contrast with everything downstream. The state
machine, `rxStep`, freezes between oversample ticks; it is enabled by the
tick and does nothing on the roughly two cycles in three when the tick is
low. The synchronizer must do the opposite. A metastable event can happen
on *any* clock edge, tick or no tick, because the host's line does not
know or care where Tamal's oversample cadence sits. So the synchronizer
runs free, catching and settling the line continuously, and hands the
already-cleaned `synced` to the tick-gated machine. Free-running guard,
gated brain: the two live at different rhythms on purpose.

The second is the initial value, `register high` and not `register 0`.
The idle UART line sits high, so the synchronizer powers up *believing
the line is idle*. Were it to power up low, the machine's freshly-primed
`rxPrev = high` would meet a `synced = low` on the very first tick and
read a phantom falling edge --- a start bit that never happened, out of a
line that was never driven. Seeding both the synchronizer and `rxPrev` to
`high` makes the receiver's first belief about the world match the
world's actual resting state: quiet, high, waiting.[^metastability]

This synchronizer is decision 6 in the [UART design][baudgen], and its
rationale is placement: the line is asynchronous because it comes from a
pin, so the synchronizer belongs *with the receiver*, inside `uartRx`,
rather than bolted on by whatever shell wires the pin. The receiver is
self-contained and metastability-safe on its own terms; a caller need
only hand it the raw pad. It is the one piece of the receiver with no
transmitter analogue whatsoever --- the transmitter drove the wire, and
you do not synchronize a wire you are driving.

<figure class="rxsy-fig" style="margin:2rem 0">
<svg class="rxsy" viewBox="0 0 760 300" role="img" aria-labelledby="rxsy-t rxsy-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxsy-t">The 2-flop synchronizer feeding the tick-gated FSM</title>
<desc id="rxsy-d">The asynchronous rxLine crosses a dashed boundary into the Dom100 clock domain and enters the first synchronizer flip-flop sync1, whose output feeds the second flip-flop synced. Both flip-flops are clocked every cycle with no enable, marked by clock notches; a brace beneath them reads two-flop synchronizer, clocked every cycle. The synced line then enters a dashed mealy rxStep box, the state machine, which additionally takes the tick input from below as an enable that gates it. The machine emits two outputs on the right, a byte strobe and an error strobe. The accent highlights the async crossing and the tick enable, and a note contrasts the always-clocked flops with the tick-gated FSM.</desc>
<style>
.rxsy{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxsy .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rxsy .mbox{fill:none;stroke:var(--fg-dim);stroke-width:1.5;stroke-dasharray:6 5}
.rxsy .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxsy .in{stroke:var(--accent);stroke-width:2.5;fill:none}
.rxsy .cross{stroke:var(--accent);stroke-width:1.5;fill:none;stroke-dasharray:5 4}
.rxsy .brace{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.rxsy .clk{fill:none;stroke:var(--fg-main);stroke-width:1.5}
.rxsy text{font-family:var(--sans)}
.rxsy .name{fill:var(--fg-main);font-family:var(--mono);font-size:13px}
.rxsy .lab{fill:var(--fg-main);font-size:12px}
.rxsy .dim{fill:var(--fg-dim);font-size:11.5px}
.rxsy .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.rxsy .sigA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.rxsy .note{fill:var(--accent);font-size:11.5px}
.rxsy .ah{fill:var(--fg-main)}
.rxsy .aha{fill:var(--accent)}
</style>
<defs>
<marker id="rxsy-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="rxsy-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<line class="cross" x1="98" y1="70" x2="98" y2="250"/>
<text class="note" x="98" y="62" text-anchor="middle">async → Dom100</text>
<text class="sigA" x="30" y="151" text-anchor="start">rxLine</text>
<text class="dim" x="30" y="168" text-anchor="start">async · pin</text>
<line class="in" x1="72" y1="155" x2="126" y2="155" marker-end="url(#rxsy-aa)"/>
<rect class="box" x="128" y="131" width="84" height="48" rx="6"/>
<path class="clk" d="M128,166 L138,172 L128,178"/>
<line class="wire" x1="212" y1="155" x2="246" y2="155" marker-end="url(#rxsy-a)"/>
<rect class="box" x="248" y="131" width="84" height="48" rx="6"/>
<path class="clk" d="M248,166 L258,172 L248,178"/>
<line class="wire" x1="332" y1="155" x2="430" y2="155" marker-end="url(#rxsy-a)"/>
<path class="brace" d="M128,190 V198 H332 V190"/>
<text class="lab" x="230" y="214" text-anchor="middle">2-flop synchronizer</text>
<text class="note" x="230" y="230" text-anchor="middle">clocked EVERY cycle — no enable</text>
<rect class="mbox" x="432" y="108" width="286" height="96" rx="10"/>
<text class="sig" x="575" y="100" text-anchor="middle">mealy rxStep initRx</text>
<text class="name" x="575" y="140" text-anchor="middle">register RxS + rxStep</text>
<text class="dim" x="575" y="159" text-anchor="middle">the TX silhouette,</text>
<text class="dim" x="575" y="174" text-anchor="middle">but freezes when ¬tick</text>
<line class="in" x1="120" y1="272" x2="575" y2="272" marker-end="url(#rxsy-aa)"/>
<path class="in" d="M575,272 V206" marker-end="url(#rxsy-aa)"/>
<text class="sigA" x="30" y="269" text-anchor="start">tick</text>
<text class="dim" x="30" y="285" text-anchor="start">enable</text>
<text class="note" x="360" y="266" text-anchor="middle">gates the FSM (never the sync flops)</text>
<text class="sig" x="380" y="148" text-anchor="middle">synced</text>
<text class="name" x="180" y="152" text-anchor="middle">sync1</text>
<text class="name" x="290" y="152" text-anchor="middle">synced</text>
<line class="wire" x1="718" y1="140" x2="748" y2="140" marker-end="url(#rxsy-a)"/>
<line class="wire" x1="718" y1="172" x2="748" y2="172" marker-end="url(#rxsy-a)"/>
<text class="sig" x="752" y="144" text-anchor="start">byte</text>
<text class="sig" x="752" y="176" text-anchor="start">error</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">Where the receiver differs from the transmitter before a single bit is decoded. The asynchronous <code>rxLine</code> crosses out of no clock domain and into <code>Dom100</code> (accent), and is caught by two back-to-back flip-flops — <code>sync1</code> then <code>synced</code> — that are clocked on <em>every</em> cycle, with no enable, so they can settle a metastable strike whenever it lands. Only the already-cleaned <code>synced</code> reaches the <code>mealy rxStep</code> machine, which wears the <a href="https://balbi.sh/posts/tamal-uart-tx/">transmitter's</a> silhouette but is <em>tick-gated</em>: the <code>tick</code> enable (accent) freezes it between oversample ticks. Free-running guard, gated brain — the two run at different rhythms on purpose.</figcaption>
</figure>

## Finding the start bit: an edge, not a level

Now we can read `rxStep`, and the natural place to start is where the
frame starts --- the idle arm, where the receiver is watching a quiet
line and waiting for a byte to begin. Here is that arm, lifted out of the
`case`:

```haskell
            RxIdle
              | rxPrev s == high && line == low -> (s0{rxState = RxStart, rxCnt = 0}, (Nothing, False))
              | otherwise -> (s0, (Nothing, False))
```

The transmitter's idle arm looked at `mbyte` and, on a `Just b`, latched
the byte and left for `TxStart`. It *decided* when the frame began,
because it was the one sending it. The receiver cannot decide; it can
only *notice*. And what it notices is not that the line is low but that
the line has just *gone* low --- the guard is `rxPrev s == high && line ==
low`, which is true on exactly the tick where the previous sample was
high and this one is low. A high-to-low transition. The **falling edge**
that opens every UART frame, because the line idles high and the start
bit is the first thing to pull it down.

Why insist on the edge? Why not the far simpler `line == low` --- if the
line is low, a start bit must be here, so go? Because "the line is low"
is true for reasons that are not the start of a frame, and a receiver
that triggered on the level would start decoding garbage out of all of
them. Consider a **break condition**: a host holding the line low for a
long stretch, deliberately, as an out-of-band signal. A level-triggered
receiver would see `low`, leap into `RxStart`, decode a frame of zeros,
return to idle, see `low` *again* --- still the same break --- and leap
right back in, manufacturing frame after frame out of one long silence.
Or consider the ordinary end of a normal frame that happens to carry a
low most-significant bit: the line is low right up until the stop bit
lifts it. A level trigger has no way to tell "the line is low because a
new frame is starting" from "the line is low because it never went back
up." An edge trigger does, and trivially: a new frame requires a *fresh*
high-to-low transition, and a line already held low offers none. There is
no new edge, so there is no new frame. Break conditions, stuck lines, and
lingering low bits all sail past.[^edge]

This is exactly what `rxPrev` was carried for. To notice a transition you
must remember the previous value, and `rxPrev` is that memory --- the
synchronized line as it stood at the *previous tick*, not the previous
cycle. (It updates only on ticks, because off a tick the whole `rxStep`
returns `s` untouched, `rxPrev` included.) On every idle tick, `s0 =
s{rxPrev = line}` refreshes it, so the comparison always pits last tick's
level against this tick's. When they differ in the falling direction, the
frame is on, and the machine moves to `RxStart` with `rxCnt` reset to
zero to begin timing the start bit. On the non-edge case it returns
`s0` --- staying idle, but with `rxPrev` freshly updated, always primed to
catch the next fall.

There is one honest imprecision to name, because the design names it too.
Start detection is checked *once per tick*, not continuously. The true
falling edge on the wire can occur anywhere within a tick interval, so
the tick that first observes `line == low` can lag the real edge by up to
one full tick --- up to one sixteenth of a bit. The receiver's whole sense
of "where am I in this bit" is therefore up to a sixteenth of a bit late
from the very start. This sounds alarming and is completely harmless, and
the reason it is harmless is the subject of the next section: the
receiver does not sample at the *edge* of a bit, where a sixteenth's slip
would matter, but at its *center*, where there is a full eight ticks of
margin on either side to absorb the slip and then some. The lag is real;
the center swallows it.[^edge]

<figure class="rxedge-fig" style="margin:2rem 0">
<svg class="rxedge" viewBox="0 0 760 240" role="img" aria-labelledby="rxedge-t rxedge-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxedge-t">Falling-edge start detection versus a held-low line</title>
<desc id="rxedge-d">A tick-by-tick strip of the synchronized line. It is high for ticks 0 to 3, falls low at tick 4, stays low through tick 10, and rises again at tick 11. The falling transition at tick 4 is highlighted in accent and labelled START, because there rxPrev was high and line is low. Ticks 5 through 10, where the line is held low, are bracketed and labelled as carrying no new high-to-low edge, so no phantom re-trigger occurs. The point is that the receiver fires on the edge, not the level.</desc>
<style>
.rxedge{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxedge .line{stroke:var(--fg-main);stroke-width:2.5;fill:none}
.rxedge .guide{stroke:var(--fg-dim);stroke-width:1;fill:none;stroke-dasharray:3 4}
.rxedge .fire{stroke:var(--accent);stroke-width:2.5;fill:none;stroke-dasharray:5 4}
.rxedge .brace{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.rxedge .dot{fill:var(--fg-main)}
.rxedge .dotA{fill:var(--accent)}
.rxedge text{font-family:var(--sans)}
.rxedge .lab{fill:var(--fg-main);font-size:12px}
.rxedge .dim{fill:var(--fg-dim);font-size:11px}
.rxedge .acc{fill:var(--accent);font-size:12px}
.rxedge .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:11px}
</style>
<line class="guide" x1="78" y1="78" x2="690" y2="78"/>
<line class="guide" x1="78" y1="128" x2="690" y2="128"/>
<text class="sig" x="70" y="82" text-anchor="end">high</text>
<text class="sig" x="70" y="132" text-anchor="end">low</text>
<line class="fire" x1="282" y1="58" x2="282" y2="142"/>
<text class="acc" x="282" y="48" text-anchor="middle">high→low edge → START</text>
<polyline class="line" points="78,78 258,78 258,128 594,128 594,78 690,78"/>
<circle class="dot" cx="90" cy="78" r="3"/>
<circle class="dot" cx="138" cy="78" r="3"/>
<circle class="dot" cx="186" cy="78" r="3"/>
<circle class="dot" cx="234" cy="78" r="3"/>
<circle class="dotA" cx="282" cy="128" r="4.5"/>
<circle class="dot" cx="330" cy="128" r="3"/>
<circle class="dot" cx="378" cy="128" r="3"/>
<circle class="dot" cx="426" cy="128" r="3"/>
<circle class="dot" cx="474" cy="128" r="3"/>
<circle class="dot" cx="522" cy="128" r="3"/>
<circle class="dot" cx="570" cy="128" r="3"/>
<circle class="dot" cx="618" cy="78" r="3"/>
<circle class="dot" cx="666" cy="78" r="3"/>
<text class="sig" x="90" y="158" text-anchor="middle">0</text>
<text class="sig" x="138" y="158" text-anchor="middle">1</text>
<text class="sig" x="186" y="158" text-anchor="middle">2</text>
<text class="sig" x="234" y="158" text-anchor="middle">3</text>
<text class="sig" x="282" y="158" text-anchor="middle">4</text>
<text class="sig" x="330" y="158" text-anchor="middle">5</text>
<text class="sig" x="378" y="158" text-anchor="middle">6</text>
<text class="sig" x="426" y="158" text-anchor="middle">7</text>
<text class="sig" x="474" y="158" text-anchor="middle">8</text>
<text class="sig" x="522" y="158" text-anchor="middle">9</text>
<text class="sig" x="570" y="158" text-anchor="middle">10</text>
<text class="sig" x="618" y="158" text-anchor="middle">11</text>
<text class="sig" x="666" y="158" text-anchor="middle">12</text>
<path class="brace" d="M330,176 V184 H570 V176"/>
<text class="lab" x="450" y="202" text-anchor="middle">line held low — no new high→low edge</text>
<text class="acc" x="450" y="220" text-anchor="middle">so no phantom re-trigger (a break, or a low bit, is ignored)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">Why the trigger is an <em>edge</em> and not a <em>level</em>. The receiver fires <code>RxStart</code> only on the tick where <code>rxPrev</code> was high and the synchronized <code>line</code> is low — the single accented falling transition at tick 4. Through ticks 5–10 the line stays low, but there is no <em>new</em> high→low edge, so nothing re-triggers: a break condition, or a frame ending on a low bit, cannot manufacture a phantom start the way a bare <code>line == low</code> test would. Detection is checked once per tick, so it can lag the true edge by under one tick — harmless, because the receiver samples each bit at its center, not here at its edge.</figcaption>
</figure>

## Zoom into one bit: the center and the vote

This is the heart of the receiver, the section the whole post has been
walking toward, and it answers a question the transmitter never had to
ask: *given a bit that is sixteen oversample ticks wide, where in those
sixteen ticks do you actually look, and how many times?* The
transmitter's answer was trivial --- it held the level for all sixteen and
never looked at all. The receiver's answer is the one interesting design
decision in the module, and it is worth deriving slowly.

Start with the window. Within any bit the receiver counts `rxCnt ::
Index 16`, running `0..15`, one step per oversample tick --- sixteen
positions tiling one bit-time. The transmitter used the same counter to
*hold* a level for sixteen ticks; the receiver uses it to *locate* itself
within a bit it is reading. And of those sixteen positions, the receiver
reads the line at exactly three of them --- counts 7, 8, and 9 --- stashing
each reading into its own field:

```haskell
captureSample :: RxS -> Bit -> RxS
captureSample s bit' = case rxCnt s of
  7 -> s{rxS7 = bit'}
  8 -> s{rxS8 = bit'}
  9 -> s{rxS9 = bit'}
  _ -> s -- actually unreachable
```

At count 7 the current line goes into `rxS7`, at 8 into `rxS8`, at 9 into
`rxS9`, and at every other count --- the twelve counts we are not sampling
--- the function returns `s` unchanged, a deliberate no-op. Three
flip-flops filled once each per bit, at three adjacent positions near the
middle of the window. Everything interesting about the receiver's
robustness is in the choice of *those three positions*, so let us take
the choice apart in three questions.

### Why the center at all?

Because the center of the bit is the one place on the waveform that is
furthest from trouble, and the trouble is at the edges. A bit is bounded
by two *transitions* --- the moment the line moves from the previous bit's
level to this one's, and the moment it moves on to the next. Right at
those transitions the line is in flight: slewing between levels, ringing,
not yet settled, and --- because of everything in the last two sections ---
possibly a fraction of a tick misaligned from where the receiver thinks
the bit boundary is. Sample there and you are reading a value that is
changing. Sample at the center, count 8 of 16, and you are as far from
both transitions as it is possible to be --- eight ticks of clear air on
each side --- where the line has long since settled to its true level and
will sit there untouched until the next boundary.

And here the two halves of the UART clasp hands. Recall the single most
important property of the [transmitter][tx]: its `line` was a **Moore**
output, a pure function of state, *dead-steady for the entire sixteen-tick
bit* because nothing but a state change could move it and the state
changed only at boundaries. The transmitter went to the trouble of making
its wire rock-solid across the whole cell precisely so that a receiver
sampling the middle would land on solid ground. The receiver is the other
end of that bargain: it samples the center *because* the transmitter
guaranteed the center is flat. Center sampling and Moore outputs are one
design decision made twice, once at each end. The center is also where
the receiver's own small errors go to be forgiven: the sub-tick
start-detection lag from the last section, and any slow baud-rate
mismatch that accumulates across a frame, both push the sample point
away from the true center --- but they have to push it a full eight ticks
before it reaches an edge, and neither is remotely that large over one
frame. The center is a generous target.

### Why three samples and not one?

A single center sample is the classic minimal UART, and it works right up
until it doesn't. One sample means zero noise immunity: a single glitch
on the line at the one instant you look --- a coupled spike, a runt from
imperfect signal integrity, one unlucky sample of a marginal level --- and
the bit is simply wrong, with nothing to catch it. You looked once, you
saw the wrong thing, you believed it.

Three samples, resolved by a majority vote, tolerate exactly one such
corruption. If two of the three readings agree --- and in a clean signal
all three agree, since they sit within two ticks of each other on a flat
level --- then a single glitched sample is outvoted two-to-one and the bit
comes out right anyway. This is, in the [design doc's][baudgen] blunt
phrase, *the actual point of 16× oversampling*. You do not oversample
sixteen times in order to sample sixteen times; you oversample so that you
can afford to take several readings clustered at the center and let them
check each other. Majority-of-three is the smallest arrangement that buys
any noise immunity at all, and it turns "one bad sample ruins the byte"
into "one bad sample is shrugged off." The [tests][hedgehog] prove exactly
this, as we will see: they flip a single center sample and demand the byte
still decode.

### Why 7, 8, and 9 --- why not others, why not more?

This is the precise question, and it has a precise answer with four parts.

*Odd, so the vote cannot tie.* A majority vote needs an odd number of
voters or it can deadlock --- two against two decides nothing. Three is
odd; the vote `maj` always has a strict winner. Four samples would
reintroduce the possibility of a tie and force some tie-break rule, which
is complexity bought for nothing.

*Three, because it is the smallest odd number greater than one.* One
sample gives no immunity, as we just saw. Three is the next odd count up,
and it already tolerates a single fault --- the common case, a lone
glitch. Five would tolerate two simultaneous faults, but two independent
glitches landing inside the same three-tick-wide center window of the
same bit is a scenario a 2 Mbaud hobby link never sees, and you would pay
for the guard against it in every bit forever. Three is the sweet spot:
the cheapest count that does anything.[^vote]

*Tight around the center, so drift can never push a sample onto an edge.*
The three samples sit at 7, 8, 9 --- adjacent, packed into the middle three
ticks of the sixteen. This matters. Suppose instead you spread the three
readings out --- say counts 4, 8, 12 --- reasoning that a wider spread
somehow samples "more" of the bit. It does the opposite of what you want:
counts 4 and 12 are only four ticks from their respective transitions, so
the moment the receiver's alignment drifts by even a few ticks --- start
lag plus a little baud mismatch --- one of those outer samples wanders onto
a bit edge and reads a value in flight, corrupting the very vote that was
supposed to protect you. Keeping all three within two ticks of the center
keeps all three deep in the flat region under every realistic drift. The
spread is not a feature; it is a liability. Tight is correct.

*Cheap, because it is three flip-flops and a three-term boolean.* The
whole apparatus is `rxS7`, `rxS8`, `rxS9` --- three single-bit registers ---
and the `maj` function, which is two ANDs-of-two ORed... three ANDs and
an OR, a handful of gates. More samples would mean more capture registers
and a wider voting function for a robustness gain the link cannot use.
The design spends exactly what buys the one property that matters and not
a gate more.

One honest note on symmetry, because it is easy to overclaim. In a
`0..15` window the exact geometric center is 7.5 --- there is no integer
count *at* the center, because sixteen is even. The design nominates count
**8** as the working "center" and brackets it with its two neighbours, 7
and 9. So the three samples are symmetric about count 8, but sit very
slightly forward of the true midpoint 7.5: count 7 is half a tick before
it, count 8 half a tick after, count 9 a tick and a half after. Half a
tick of asymmetry in a window with eight ticks of margin to either edge is
nothing --- but it is there, and it is more accurate to say "count 8 and
its neighbours" than to pretend the three straddle 7.5 evenly. They do
not; they cluster just past it.

That leaves the `_ -> s` arm of `captureSample` --- the no-op at all twelve
non-sampled counts --- and its comment, `-- actually unreachable`. It is
reached constantly in the ordinary sense: on every tick that is not a 7,
8, or 9, `captureSample` runs and does nothing. The "unreachable" is
subtler and is the same totality point the [transmitter][tx] post made
about `TxIdle -> s`: the `case` must cover every `Index 16` value or GHC's
exhaustiveness warning fires, so the catch-all is there to satisfy the
compiler that the function is total. Whether any *particular* count ever
flows through it is a runtime matter the type cannot see; the wildcard
makes the function honest for all sixteen regardless.[^unreachable]

<figure class="rxbit-fig" style="margin:2rem 0">
<svg class="rxbit" viewBox="0 0 760 344" role="img" aria-labelledby="rxbit-t rxbit-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxbit-t">Zoom into one bit: the sixteen oversample ticks and the three center samples</title>
<desc id="rxbit-d">A single bit spans sixteen oversample ticks, counts 0 through 15, marked as dots along a settled flat level between two edge transitions at the far left and far right. The exact geometric center is 7.5; the design nominates count 8 as the center. An accent band highlights counts 7, 8 and 9, whose dots are enlarged and labelled s7, s8 and s9 — the three samples captured near the center. Arrows show that these three sit about eight ticks from each edge, the maximum margin, where the Moore-driven line is flat and settled. A small accent arrow at the left edge notes that the sub-tick start-detection lag is absorbed here. Below, the three samples s7, s8, s9 feed a majority box that outputs the decided bit.</desc>
<style>
.rxbit{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxbit .cell{fill:var(--fg-main);stroke:none}
.rxbit .band{fill:var(--accent);opacity:0.13}
.rxbit .edge{stroke:var(--fg-dim);stroke-width:1.5;fill:none;stroke-dasharray:4 4}
.rxbit .ctr{stroke:var(--fg-dim);stroke-width:1.5;fill:none;stroke-dasharray:2 3}
.rxbit .line{stroke:var(--fg-main);stroke-width:2.5;fill:none}
.rxbit .marg{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.rxbit .lag{stroke:var(--accent);stroke-width:2;fill:none}
.rxbit .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rxbit .fan{stroke:var(--accent);stroke-width:2;fill:none}
.rxbit .out{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxbit .dot{fill:var(--fg-dim)}
.rxbit .dotA{fill:var(--accent)}
.rxbit text{font-family:var(--sans)}
.rxbit .num{fill:var(--fg-dim);font-family:var(--mono);font-size:10.5px}
.rxbit .samp{fill:var(--accent);font-family:var(--mono);font-size:12px}
.rxbit .lab{fill:var(--fg-main);font-size:12px}
.rxbit .dim{fill:var(--fg-dim);font-size:11px}
.rxbit .acc{fill:var(--accent);font-size:11.5px}
.rxbit .ah{fill:var(--fg-dim)}
.rxbit .aha{fill:var(--accent)}
.rxbit .af{fill:var(--fg-main)}
</style>
<defs>
<marker id="rxbit-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="rxbit-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
<marker id="rxbit-af" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="af"/></marker>
</defs>
<rect class="band" x="345" y="70" width="105" height="74"/>
<line class="edge" x1="100" y1="70" x2="100" y2="150"/>
<line class="edge" x1="660" y1="70" x2="660" y2="150"/>
<line class="ctr" x1="380" y1="64" x2="380" y2="107"/>
<text class="dim" x="380" y="58" text-anchor="middle">7.5</text>
<text class="dim" x="100" y="62" text-anchor="middle">edge</text>
<text class="dim" x="660" y="62" text-anchor="middle">edge</text>
<line class="line" x1="100" y1="107" x2="660" y2="107"/>
<circle class="dot" cx="117.5" cy="107" r="3"/>
<circle class="dot" cx="152.5" cy="107" r="3"/>
<circle class="dot" cx="187.5" cy="107" r="3"/>
<circle class="dot" cx="222.5" cy="107" r="3"/>
<circle class="dot" cx="257.5" cy="107" r="3"/>
<circle class="dot" cx="292.5" cy="107" r="3"/>
<circle class="dot" cx="327.5" cy="107" r="3"/>
<circle class="dotA" cx="362.5" cy="107" r="5"/>
<circle class="dotA" cx="397.5" cy="107" r="5"/>
<circle class="dotA" cx="432.5" cy="107" r="5"/>
<circle class="dot" cx="467.5" cy="107" r="3"/>
<circle class="dot" cx="502.5" cy="107" r="3"/>
<circle class="dot" cx="537.5" cy="107" r="3"/>
<circle class="dot" cx="572.5" cy="107" r="3"/>
<circle class="dot" cx="607.5" cy="107" r="3"/>
<circle class="dot" cx="642.5" cy="107" r="3"/>
<text class="samp" x="362.5" y="90" text-anchor="middle">s7</text>
<text class="samp" x="397.5" y="90" text-anchor="middle">s8</text>
<text class="samp" x="432.5" y="90" text-anchor="middle">s9</text>
<text class="num" x="117.5" y="125" text-anchor="middle">0</text>
<text class="num" x="152.5" y="125" text-anchor="middle">1</text>
<text class="num" x="187.5" y="125" text-anchor="middle">2</text>
<text class="num" x="222.5" y="125" text-anchor="middle">3</text>
<text class="num" x="257.5" y="125" text-anchor="middle">4</text>
<text class="num" x="292.5" y="125" text-anchor="middle">5</text>
<text class="num" x="327.5" y="125" text-anchor="middle">6</text>
<text class="num" x="362.5" y="125" text-anchor="middle">7</text>
<text class="num" x="397.5" y="125" text-anchor="middle">8</text>
<text class="num" x="432.5" y="125" text-anchor="middle">9</text>
<text class="num" x="467.5" y="125" text-anchor="middle">10</text>
<text class="num" x="502.5" y="125" text-anchor="middle">11</text>
<text class="num" x="537.5" y="125" text-anchor="middle">12</text>
<text class="num" x="572.5" y="125" text-anchor="middle">13</text>
<text class="num" x="607.5" y="125" text-anchor="middle">14</text>
<text class="num" x="642.5" y="125" text-anchor="middle">15</text>
<line class="marg" x1="343" y1="162" x2="103" y2="162" marker-end="url(#rxbit-a)"/>
<line class="marg" x1="452" y1="162" x2="657" y2="162" marker-end="url(#rxbit-a)"/>
<text class="dim" x="223" y="156" text-anchor="middle">≈8 ticks of margin</text>
<text class="dim" x="555" y="156" text-anchor="middle">≈8 ticks of margin</text>
<text class="lab" x="397" y="182" text-anchor="middle">the Moore-driven line is flat and settled here</text>
<line class="lag" x1="100" y1="206" x2="146" y2="206" marker-end="url(#rxbit-aa)"/>
<text class="acc" x="152" y="210" text-anchor="start">start-detect lag &lt; 1 tick — absorbed by the center</text>
<circle class="dotA" cx="352" cy="278" r="5"/>
<circle class="dotA" cx="392" cy="278" r="5"/>
<circle class="dotA" cx="432" cy="278" r="5"/>
<text class="samp" x="352" y="266" text-anchor="middle">s7</text>
<text class="samp" x="392" y="266" text-anchor="middle">s8</text>
<text class="samp" x="432" y="266" text-anchor="middle">s9</text>
<line class="fan" x1="359" y1="278" x2="494" y2="271" marker-end="url(#rxbit-aa)"/>
<line class="fan" x1="399" y1="278" x2="494" y2="278" marker-end="url(#rxbit-aa)"/>
<line class="fan" x1="439" y1="278" x2="494" y2="285" marker-end="url(#rxbit-aa)"/>
<rect class="box" x="496" y="258" width="82" height="40" rx="6"/>
<text class="lab" x="537" y="276" text-anchor="middle" style="font-family:var(--mono)">maj</text>
<text class="dim" x="537" y="291" text-anchor="middle">≥ 2 of 3</text>
<line class="out" x1="578" y1="278" x2="636" y2="278" marker-end="url(#rxbit-af)"/>
<text class="lab" x="642" y="282" text-anchor="start" style="font-family:var(--mono)">bit'</text>
<text class="dim" x="300" y="326" text-anchor="middle">three center samples → one voted bit</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">One bit, its sixteen oversample ticks, and the three the receiver actually reads. The geometric center is 7.5 (sixteen is even, so no count sits exactly on it); the design nominates count <strong>8</strong> and brackets it with 7 and 9 — the accent band. Those three samples sit about eight ticks from <em>both</em> edge transitions, the maximum possible margin, where the transmitter's Moore-driven line is guaranteed flat, and where the sub-tick start-detection lag is comfortably absorbed. Why these three and no others: <strong>odd</strong> so the vote cannot tie, <strong>three</strong> because it is the smallest odd count that tolerates a glitch, <strong>tight around the center</strong> so drift never pushes a sample onto an edge, and <strong>cheap</strong> — three flip-flops and a boolean. The three feed <code>maj</code>, which returns the bit the majority saw.</figcaption>
</figure>

## The majority vote

The vote itself is one line, and it is pure combinational logic --- no
clock, no state, the [CRC][crc]'s world briefly revisited inside the
receiver:

```haskell
maj :: Bit -> Bit -> Bit -> Bit
maj a b c = (a .&. b) .|. (a .&. c) .|. (b .&. c)
```

Read it as gates. `.&.` is bitwise AND, `.|.` is bitwise OR, and on three
single `Bit`s the expression is `(a AND b) OR (a AND c) OR (b AND c)` ---
high if and only if at least two of the three inputs are high. Every pair
gets its own AND; if *any* pair is both-high, the OR fires. It is the
textbook majority function, and it does exactly what the last section
promised: it returns the value that at least two of the three samples
agreed on, so a lone dissenter --- a single glitched sample --- is
outvoted.

Trace the one case that matters, a clean bit corrupted by a single
glitch. Say the true bit is high, so in a clean signal `s7 = s8 = s9 =
1`, and `maj 1 1 1 = (1 AND 1) OR ... = 1` --- correct. Now let one
sample be glitched low, say `s8`: the inputs are `1 0 1`, and `maj 1 0 1 =
(1 AND 0) OR (1 AND 1) OR (0 AND 1) = 0 OR 1 OR 0 = 1` --- still correct.
The pair `s7 AND s9` carried the day. The same holds whichever single
sample flips, and whichever way the true bit points: two good samples
always contain a both-agreeing pair, and that pair drives the OR. Only a
*second* simultaneous corruption --- two of the three flipped --- could
swing the vote, and that is precisely the fault the design declined to
guard against because the link never produces it. One glitch in, correct
bit out. That property has a name in the test suite, and we will watch
Hedgehog flip a sample and demand the byte survive.

In `decideBit`, the vote is computed once, in a `where` clause, and every
arm reads its result:

```haskell
  bit' = maj (rxS7 s) (rxS8 s) (rxS9 s)
```

`bit'` is *the* recovered bit for this cell --- whatever the majority of
the three center samples saw --- and it is what the rest of `decideBit`
acts on. Whether the cell was a start bit, a data bit, or a stop bit,
the reading is the same: three samples near the center, one majority
vote, one bit. The [design doc][baudgen] calls this *reusing one
sampling primitive at every bit center*, and it is why the code has a
single `maj` and a single trio of sample fields rather than special-case
logic per phase. The vote does not care what kind of bit it is deciding;
it just decides.

<figure class="rxmaj-fig" style="margin:2rem 0">
<svg class="rxmaj" viewBox="0 0 760 300" role="img" aria-labelledby="rxmaj-t rxmaj-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxmaj-t">The majority vote as a gate network</title>
<desc id="rxmaj-d">Three inputs s7, s8 and s9 on the left fan out to three two-input AND gates: s7 and s8, s7 and s9, s8 and s9. The three AND outputs feed a single three-input OR gate, whose output is the decided bit. An annotation shows that a clean high bit reads 1,1,1 giving a vote of 1, and that a single glitch flipping one sample to 0,1,1 still votes 1, so the majority survives one corrupted sample.</desc>
<style>
.rxmaj{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxmaj .gate{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rxmaj .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.rxmaj .node{fill:var(--fg-main)}
.rxmaj text{font-family:var(--sans)}
.rxmaj .sig{fill:var(--accent);font-family:var(--mono);font-size:13px}
.rxmaj .gl{fill:var(--fg-main);font-family:var(--mono);font-size:11px}
.rxmaj .lab{fill:var(--fg-main);font-size:12px}
.rxmaj .dim{fill:var(--fg-dim);font-size:11px}
.rxmaj .acc{fill:var(--accent);font-size:11.5px}
.rxmaj .ah{fill:var(--fg-main)}
</style>
<defs>
<marker id="rxmaj-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
</defs>
<text class="sig" x="46" y="76" text-anchor="start">s7</text>
<text class="sig" x="46" y="156" text-anchor="start">s8</text>
<text class="sig" x="46" y="236" text-anchor="start">s9</text>
<circle class="node" cx="120" cy="72" r="3"/>
<circle class="node" cx="150" cy="152" r="3"/>
<circle class="node" cx="180" cy="232" r="3"/>
<path class="wire" d="M66,72 H240 V62 H300"/>
<path class="wire" d="M120,72 V150 H300"/>
<path class="wire" d="M66,152 H220 V84 H300"/>
<path class="wire" d="M150,152 V236 H300"/>
<path class="wire" d="M66,232 H255 V172 H300"/>
<path class="wire" d="M180,232 V258 H300"/>
<path class="gate" d="M300,50 L331,50 A23 23 0 0 1 331,96 L300,96 Z"/>
<path class="gate" d="M300,137 L331,137 A23 23 0 0 1 331,183 L300,183 Z"/>
<path class="gate" d="M300,224 L331,224 A23 23 0 0 1 331,270 L300,270 Z"/>
<text class="gl" x="312" y="77" text-anchor="middle">&amp;</text>
<text class="gl" x="312" y="164" text-anchor="middle">&amp;</text>
<text class="gl" x="312" y="251" text-anchor="middle">&amp;</text>
<text class="dim" x="355" y="46" text-anchor="middle">s7·s8</text>
<text class="dim" x="355" y="205" text-anchor="middle">s7·s9</text>
<text class="dim" x="355" y="292" text-anchor="middle">s8·s9</text>
<path class="wire" d="M354,73 H430 V140 H470" marker-end="url(#rxmaj-a)"/>
<path class="wire" d="M354,160 H470" marker-end="url(#rxmaj-a)"/>
<path class="wire" d="M354,247 H430 V190 H470" marker-end="url(#rxmaj-a)"/>
<path class="gate" d="M470,120 Q502,120 548,165 Q502,210 470,210 Q486,165 470,120 Z"/>
<text class="gl" x="498" y="169" text-anchor="middle">≥1</text>
<line class="wire" x1="548" y1="165" x2="628" y2="165" marker-end="url(#rxmaj-a)"/>
<text class="sig" x="634" y="169" text-anchor="start">bit'</text>
<text class="acc" x="512" y="250" text-anchor="middle">clean 1 1 1 → 1</text>
<text class="acc" x="512" y="268" text-anchor="middle">one glitch 0 1 1 → 1 (survives)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The vote <code>(s7·s8) + (s7·s9) + (s8·s9)</code> as gates: each pair of samples meets in a two-input AND, and the three ANDs meet in one OR, so the output is high exactly when at least two of the three inputs are. The point is fault tolerance: a clean high bit reads <code>1 1 1</code> and votes <code>1</code>; a single glitch flipping one sample to <code>0 1 1</code> still votes <code>1</code>, because the surviving pair <code>s8·s9</code> holds. Only two simultaneous corruptions could swing it — the fault the link never produces. This is the whole return on sampling three times instead of once.</figcaption>
</figure>

## `decideBit`: confirm, assemble, test

`decideBit` is called once per bit, at the tick that resolves the
window, with the three samples already in hand. It is where the voted
`bit'` becomes a decision, and it reads as a `case` over the phase ---
one arm each for start, data, and stop, plus the obligatory unreachable
idle:

```haskell
decideBit :: RxS -> (RxS, (Maybe (BitVector 8), Bool))
decideBit s = case rxState s of
  RxStart
    | bit' == low -> (s{rxState = RxData 0, rxCnt = 0}, (Nothing, False))
    | otherwise -> (s{rxState = RxIdle, rxCnt = 0}, (Nothing, False))
  RxData i ->
    let sh =
          if bit' == high
            then setBit (rxShift s) (fromEnum i)
            else clearBit (rxShift s) (fromEnum i)
        s' = s{rxShift = sh, rxCnt = 0}
     in if i == maxBound
          then (s'{rxState = RxStop}, (Nothing, False))
          else (s'{rxState = RxData (i + 1)}, (Nothing, False))
  RxStop
    | bit' == high -> (s{rxState = RxIdle, rxCnt = 0}, (Just (rxShift s), False))
    | otherwise -> (s{rxState = RxIdle, rxCnt = 0}, (Nothing, True))
  RxIdle -> (s, (Nothing, False)) -- actually unreachable
 where
  bit' = maj (rxS7 s) (rxS8 s) (rxS9 s)
```

Read the three live arms in order; each is one job the transmitter never
had.

**`RxStart` confirms or abandons.** The receiver reached `RxStart`
because it saw a falling edge and *believed* a start bit had begun. Now,
at the center of that supposed start bit, it checks: is the voted `bit'`
still low? If so, the low level held across the center of the bit --- a
real start bit, not a momentary dip --- and the machine commits to the
frame, moving to `RxData 0` to read the first data bit. If instead `bit'`
came back high, then the thing that pulled the line down was *not* a start
bit at all --- a glitch, a runt, a noise spike that tripped the edge
detector --- and the machine abandons it, returning to `RxIdle` to wait for
a real edge. This is **glitch rejection at the frame level**, a second
line of defence behind the majority vote: the edge detector is
deliberately twitchy (it must catch every real start), so the start bit
is *confirmed* half a bit later before any data is believed. The
transmitter never needed this because it never doubted its own start bit;
the receiver doubts everything until the center agrees.

**`RxData i` assembles the byte.** Here is the one genuinely pretty
inversion of the transmitter, so it is worth slowing down. The
transmitter serialised a byte *out* of a shift register: it held the byte
in `txShift`, read `lsb (txShift s)` onto the wire, and `shiftR`-ed the
register at each boundary so the next bit fell into the bottom
position --- the byte walked out the bottom, LSB first. The receiver does
the mirror image, assembling a byte *in* --- but notice it does not shift
at all:

```haskell
    let sh =
          if bit' == high
            then setBit (rxShift s) (fromEnum i)
            else clearBit (rxShift s) (fromEnum i)
```

Instead of sliding the register and always writing the bottom, the
receiver writes bit number `i` *directly* --- `setBit` to make position `i`
high, `clearBit` to make it low, with `fromEnum i` turning the `Index 8`
into the plain `Int` position that `setBit`/`clearBit` want. At `RxData
0` it writes bit 0; at `RxData 7`, bit 7; so bit `i` of the recovered
`rxShift` is exactly the `i`-th data bit received, LSB first, matching the
transmitter's convention wire for wire. Where the transmitter *moved the
byte past a fixed read point*, the receiver *holds the byte still and
addresses it by index*. Both are LSB-first, both walk bit 0 through bit 7,
but one slides and the other indexes --- the same byte, the opposite
direction, and the [CRC][crc] and [transmitter][tx] shift-register motif
seen a third time, now assembling rather than emitting.[^assemble] After
writing, if `i == maxBound` --- bit 7, the last one, the `Index 8`
guaranteeing there is no bit 8 --- the machine goes to `RxStop`; otherwise
it advances to `RxData (i + 1)` for the next data bit. Either way `rxCnt`
resets to begin a fresh sixteen-tick window.

**`RxStop` delivers the verdict.** At the center of the stop bit the
receiver votes one last time, and this vote decides whether the whole
frame was well-formed:

```haskell
  RxStop
    | bit' == high -> (s{rxState = RxIdle, rxCnt = 0}, (Just (rxShift s), False))
    | otherwise -> (s{rxState = RxIdle, rxCnt = 0}, (Nothing, True))
```

A UART stop bit is *defined* to be high; the line must return to its idle
level to frame the byte. So if `bit'` is high, the frame closed cleanly:
the machine emits `Just (rxShift s)` --- the fully assembled byte, handed
up as the one-cycle strobe --- with the error flag `False`, and returns to
idle. But if `bit'` is low, the stop bit was *missing*: the line was still
low where it should have lifted, which means the framing was wrong ---
mismatched baud, a line fault, a spurious mid-noise "frame." The receiver
refuses to hand up a byte it cannot trust. It emits `(Nothing, True)`:
**no byte, framing error raised**. The assembled `rxShift` is simply
dropped, never delivered. This is the framing-error path, the third
output the transmitter had no counterpart for, and it too has made a
test. Both stop arms return to `RxIdle`, because whether the frame was
good or bad, it is over.

That leaves `RxIdle -> (s, (Nothing, False))`, the fourth arm, tagged
`-- actually unreachable`. As in `captureSample`, and as in the
[transmitter's][tx] `txAdvance`, it exists to make the `case` total: GHC
insists every `RxState` constructor be handled, so the idle arm is
written even though `decideBit` is only ever *called* from a resolving
tick, and idle never resolves. Returning `s` unchanged with no output is
the honest do-nothing that keeps the function total and the compiler
quiet.[^unreachable]

## The outputs are genuinely Mealy

Now for the sharpest contrast with the transmitter, and a direct callback
to the longest argument in that post. The [transmitter][tx] spent pages
establishing that although it was lifted with the general `mealy`
combinator, its outputs were pure **Moore** --- `line` and `ready` were
functions of the state alone, no input in sight, so the wire sat
dead-steady between boundaries. We drew a whole figure of it: the Moore
machine whose output reads only the state, next to the Mealy machine with
an accent tap carrying the input up into the output. The transmitter, we
concluded, had no such tap. Its handshake bought a same-cycle
*transition*, but never a same-cycle *output*.

The receiver has the tap. Its outputs are **genuinely Mealy**, and you
can see it in the first line of `rxStep`:

```haskell
rxStep s (tick, line)
  | not tick = (s, (Nothing, False)) -- only move on oversample ticks
  | otherwise = ...
```

Read what that guard says about the *output*, not the state. On any cycle
where `tick` is low --- most cycles --- `rxStep` returns the output
`(Nothing, False)` regardless of what state `s` holds. On a cycle where
`tick` is high and the state happens to be a resolving `RxStop`, the same
machine can return `(Just byte, False)`. So take a single fixed state ---
say `s` with `rxState = RxStop` and `rxCnt = 9` and a winning vote --- and
ask what the output is. On a non-tick cycle it is `(Nothing, False)`; on a
tick cycle it is `(Just byte, False)`. **Same state, different output,
and the thing that differs is the input `tick`.** The output reads an
input. That is the definition of a Mealy output, and it is exactly the
accent tap the transmitter's figure showed and the transmitter's code
lacked.

It is worth being as careful here as the transmitter post was, because
the distinction is easy to fumble. The Mealy-ness is *not* that the next
state depends on the input --- that is true of every state machine, Moore
included, and says nothing. The Mealy-ness is that the *output* depends on
the input: the byte-ready and framing-error strobes are functions of
`tick` (and, at the resolving stop tick, of the freshly-sampled `line`
folded into the vote), not of the state alone. By the one test that
separates the two machine styles --- does the input reach the output within
the cycle? --- the transmitter's outputs are Moore and the receiver's are
Mealy. The `mealy` combinator lifted both; only the receiver actually
uses the generality it offers.

And this is not an accident of implementation but a difference in the
*nature* of the two interfaces, which is the satisfying part. The
transmitter's outputs are **levels**: `line` is a level the receiver will
sample, `ready` is a level the caller will poll, and a level wants to be
Moore --- steady, glitch-free, held for as long as the state holds.
Levels and Moore go together. The receiver's outputs are **events**: "a
byte just completed," "a frame just failed," each true for one cycle and
then gone. An event is a strobe, and a strobe is inherently Mealy ---
it fires in response to a condition (this tick, this resolution) and must
be low every other cycle no matter what the state is. Events and Mealy go
together. The transmitter drove levels, so it was Moore; the receiver
raises events, so it is Mealy. The two machines wear the two halves of
the Moore/Mealy distinction the way they wear the two halves of
drive/read --- one at each end, by the nature of the job.[^strobe]

One consequence to nail down, because an integrator will care: the
strobes are **exactly one cycle wide**. `Just byte` appears on precisely
the single cycle where `tick` is high and `RxStop` resolves at count 9,
and on the very next cycle --- tick or not --- the machine is in `RxIdle`
and the output is `(Nothing, False)` again. There is no holding, no
latching, no "byte valid until you read it." A consumer of `rxByte` must
catch the strobe on the cycle it fires. That is the mirror of the
transmitter's rule that a caller must present `Just b` while `ready` is
high --- both are one-cycle contracts --- and both fall straight out of the
machines being built from a tick-gated `mealy` with no output register
tacked on.

## Resolving the stop bit early

There is one asymmetry in `rxStep` that is easy to skate past and worth a
paragraph, because it is a real design decision with a real reason. Start
bits and data bits are resolved at the *end* of their window --- at `rxCnt
== maxBound`, count 15, the sixteenth tick --- but the stop bit is resolved
*early*, at count 9, six ticks before the window would close. The two
dispatch arms make the difference plain:

```haskell
            RxStop ->
              let s1 = captureSample s0 line
               in if rxCnt s == 9
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
            _ ->
              -- RxStart / RxData
              let s1 = captureSample s0 line
               in if rxCnt s == maxBound
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
```

The `_` arm --- start and data --- resolves at `maxBound`; the `RxStop`
arm resolves at 9. Why not let the stop bit run out its full sixteen
ticks like every other bit? Because by count 9 the receiver already has
everything it needs. Its three samples were taken at 7, 8, and 9; the
moment `rxS9` is captured, the vote can be computed and the frame closed.
Waiting out counts 10 through 15 would decide nothing new --- it would just
sit on a stop bit it has already read. And those six idle ticks are worth
more spent elsewhere. The code comment lays out the reasoning in full:

```text
              -- Resolve the stop bit at its center sample (count 9) rather than
              -- waiting out the whole window: returning to idle ~6 ticks early
              -- gives the falling-edge detector idle-high ticks to arm rxPrev,
              -- so a back-to-back next frame (stop immediately followed by start)
              -- still resyncs.
```

Think about the worst case for start detection: **back-to-back frames**,
where a sender transmits one byte's stop bit and immediately drives the
next byte's start bit, with no idle gap between. The stop bit is high, the
next start bit is low, so there is a clean high-to-low edge right at the
boundary --- but the receiver can only catch that edge if it is *back in
`RxIdle`*, watching, with `rxPrev` already primed to high, by the time the
edge arrives. If the receiver were still grinding through counts 10–15 of
the stop bit when the next start edge fell, it would be in the wrong state
to notice, and the next frame would be missed or misaligned. Resolving at
count 9 returns the machine to `RxIdle` roughly six ticks before the stop
bit even ends, and those six ticks are high (it is a stop bit, after all),
so they land on `rxPrev` as idle-high observations, arming the edge
detector. By the time the next frame's falling edge arrives, the receiver
is idle, primed, and ready to catch it. Early resolution is not an
optimisation for its own sake; it is what makes the receiver survive a
sender that does not pause between bytes --- and the [tests][hedgehog]
lean on it, feeding lists of bytes through the loopback with no idle
padding and demanding every one come back.

## The whole `rxStep`, assembled

We have read `rxStep` in pieces --- the freeze, the idle edge-detect, the
early stop resolution, the general start/data resolution. Here it is
whole, the transition function `mealy` clocks, and now every line should
land:

```haskell
rxStep :: RxS -> (Bool, Bit) -> (RxS, (Maybe (BitVector 8), Bool))
rxStep s (tick, line)
  | not tick = (s, (Nothing, False)) -- only move on oversample ticks
  | otherwise =
      let s0 = s{rxPrev = line} -- remember this tick's level for next tick
       in case rxState s of
            RxIdle
              | rxPrev s == high && line == low -> (s0{rxState = RxStart, rxCnt = 0}, (Nothing, False))
              | otherwise -> (s0, (Nothing, False))
            RxStop ->
              let s1 = captureSample s0 line
               in if rxCnt s == 9
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
            _ ->
              let s1 = captureSample s0 line
               in if rxCnt s == maxBound
                    then decideBit s1
                    else (s1{rxCnt = rxCnt s + 1}, (Nothing, False))
```

Its type is `RxS -> (Bool, Bit) -> (RxS, (Maybe (BitVector 8), Bool))` ---
the primer's `s -> i -> (s, o)` exactly, with the receiver's state, the
receiver's `(tick, line)` input, and the receiver's `(byte, error)`
output. Like `txStep`, it splits into two clean halves, though the seam
runs differently.

The first half is the same freeze the transmitter had: `| not tick = (s,
(Nothing, False))`. Off a tick --- most cycles --- the state is returned
untouched and the output is the quiet `(Nothing, False)`. The receiver
does nothing between ticks, exactly as the transmitter did nothing; the
enable gates both. (This is also, as the last section argued, where the
Mealy output lives: the output is forced quiet here *by the input*.)

The second half, on a tick, is the receiver's own. First `s0 = s{rxPrev =
line}` records this tick's line so the *next* tick can detect an edge
against it --- the one piece of bookkeeping every tick does regardless of
phase. Then a three-way dispatch on the phase, and this is where the
transmitter and receiver finally diverge in shape rather than just in
verb. `RxIdle` does not count or sample at all --- it only watches for the
falling edge, firing `RxStart` on a high-to-low transition and otherwise
sitting idle with `rxPrev` refreshed. `RxStop` captures its sample and
resolves *early* at count 9. The catch-all `_` --- start and data ---
captures its sample and resolves at the window's end, count 15. Idle
hunts; stop resolves early; start and data resolve late. Three rhythms,
one per kind of bit, where the transmitter had marched every phase to the
identical sixteen-tick drum. The receiver has to be more supple because
it is chasing a frame it did not schedule, and the suppleness is all
right here, in the seams of one `case`.

## The tests

The receiver shares `Test/Uart.hs` with the rest of the UART, and because
it is sequential the tests drive it through Clash simulation --- `sampleN`,
`bundle`, `fromList` --- rather than the pure-function style. Four of the
properties are the receiver's own, and they are the four places the
design made a promise specific enough to break: it decodes a clean frame,
it flags a bad one, it shrugs off a single glitch, and it samples the
center and not the edge. Read them and you have read the receiver's
contract as executable claims.

They all run through one small harness, so it is worth meeting it first:

```haskell
bitAt :: BitVector 8 -> Int -> Bit
bitAt b i = if testBit b i then high else low

frame :: BitVector 8 -> Bit -> [Bit]
frame b stop =
  L.replicate 16 low
    <> L.concatMap (\i -> L.replicate 16 (bitAt b i)) [0 .. 7]
    <> L.replicate 16 stop

runRx :: [Bit] -> [(Maybe (BitVector 8), Bool)]
runRx samples =
  sampleN
    (8 + L.length samples + 24)
    (bundle (uartRx (pure True) lineSig) :: Signal Dom100 (Maybe (BitVector 8), Bool))
 where
  lineSig = fromList (L.replicate 8 high <> samples <> L.repeat high)

recovered :: [(Maybe (BitVector 8), Bool)] -> [BitVector 8]
recovered xs = [b | (Just b, _) <- xs]

anyErr :: [(Maybe (BitVector 8), Bool)] -> Bool
anyErr xs = L.or [e | (_, e) <- xs]
```

`frame b stop` hand-builds the oversampled line for a byte: sixteen `low`
samples for the start bit, then for each `i` from 0 to 7 sixteen copies of
that byte's `i`-th bit (`bitAt`, LSB-first --- the same convention the
transmitter drives and `decideBit` assembles), then sixteen copies of
whatever `stop` level you pass. Sixteen samples a bit because that is the
oversampling; a whole 8N1 frame is 160 samples. `runRx` is the driver: it
sets `tick = pure True` so *every* cycle is an oversample tick --- sixteen
cycles to a bit, the fast cadence, no NCO needed here --- prepends eight
idle-high samples (giving the two-flop synchronizer time to fill and
`rxPrev` time to arm high before any edge), pads the tail with an infinite
idle-high `L.repeat high`, and samples the bundled `(byte, error)` output
for enough cycles to see the frame through. `recovered` sifts the `Just`
strobes out of the output stream into a list of bytes; `anyErr` ORs the
error strobes. With that vocabulary the four properties are one or two
lines each.

<figure class="rxwf-fig" style="margin:2rem 0">
<svg class="rxwf" viewBox="0 0 760 208" role="img" aria-labelledby="rxwf-t rxwf-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxwf-t">A received 8N1 frame with per-bit center sampling</title>
<desc id="rxwf-d">The received line over time for the byte 0x4B. It idles high, drops low for one start bit, carries the eight data bits least-significant first (1, 1, 0, 1, 0, 0, 1, 0), returns high for one stop bit, and idles high. Each cell is sixteen oversample samples wide. An accent dot marks the center of each cell, where the receiver takes its three-sample majority vote; the recovered bits assemble bit-for-bit into the original byte.</desc>
<style>
.rxwf{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxwf .line{stroke:var(--fg-main);stroke-width:2.5;fill:none}
.rxwf .startseg{stroke:var(--accent);stroke-width:2.5;fill:none}
.rxwf .guide{stroke:var(--fg-dim);stroke-width:1;fill:none;stroke-dasharray:3 4}
.rxwf .dot{fill:var(--accent)}
.rxwf text{font-family:var(--sans)}
.rxwf .lab{fill:var(--fg-main);font-size:13px}
.rxwf .dim{fill:var(--fg-dim);font-size:11px}
.rxwf .acc{fill:var(--accent);font-size:11px}
.rxwf .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
</style>
<text class="lab" x="382" y="26" text-anchor="middle">recovered byte = 0x4B  ('K')</text>
<line class="guide" x1="122" y1="52" x2="122" y2="140"/>
<line class="guide" x1="174" y1="52" x2="174" y2="140"/>
<line class="guide" x1="590" y1="52" x2="590" y2="140"/>
<line class="guide" x1="642" y1="52" x2="642" y2="140"/>
<polyline class="line" points="70,66 122,66 122,126 174,126 174,66 278,66 278,126 330,126 330,66 382,66 382,126 486,126 486,66 538,66 538,126 590,126 590,66 694,66"/>
<polyline class="startseg" points="122,66 122,126 174,126 174,66"/>
<circle class="dot" cx="148" cy="126" r="3"/>
<circle class="dot" cx="200" cy="66" r="3"/>
<circle class="dot" cx="252" cy="66" r="3"/>
<circle class="dot" cx="304" cy="126" r="3"/>
<circle class="dot" cx="356" cy="66" r="3"/>
<circle class="dot" cx="408" cy="126" r="3"/>
<circle class="dot" cx="460" cy="126" r="3"/>
<circle class="dot" cx="512" cy="66" r="3"/>
<circle class="dot" cx="564" cy="126" r="3"/>
<circle class="dot" cx="616" cy="66" r="3"/>
<text class="sig" x="60" y="70" text-anchor="end">1</text>
<text class="sig" x="60" y="130" text-anchor="end">0</text>
<text class="dim" x="96" y="154" text-anchor="middle">idle</text>
<text class="acc" x="148" y="154" text-anchor="middle">start</text>
<text class="lab" x="200" y="154" text-anchor="middle" style="font-size:11px">b0</text>
<text class="lab" x="252" y="154" text-anchor="middle" style="font-size:11px">b1</text>
<text class="lab" x="304" y="154" text-anchor="middle" style="font-size:11px">b2</text>
<text class="lab" x="356" y="154" text-anchor="middle" style="font-size:11px">b3</text>
<text class="lab" x="408" y="154" text-anchor="middle" style="font-size:11px">b4</text>
<text class="lab" x="460" y="154" text-anchor="middle" style="font-size:11px">b5</text>
<text class="lab" x="512" y="154" text-anchor="middle" style="font-size:11px">b6</text>
<text class="lab" x="564" y="154" text-anchor="middle" style="font-size:11px">b7</text>
<text class="acc" x="616" y="154" text-anchor="middle">stop</text>
<text class="dim" x="668" y="154" text-anchor="middle">idle</text>
<text class="dim" x="382" y="184" text-anchor="middle">each dot: 3-sample majority vote at counts 7/8/9 — data read LSB-first</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The same byte the <a href="https://balbi.sh/posts/tamal-uart-tx/">transmitter</a> sent — ASCII <code>'K'</code>, <code>0x4B</code> = <code>0b0100_1011</code> — now arriving at the receiver. The frame is idle-high, one accent <strong>start</strong> bit, the eight data bits <strong>LSB-first</strong> (<code>1 1 0 1 0 0 1 0</code>), one high <strong>stop</strong> bit, then idle. Each accent dot marks a cell center, where the receiver's three samples (counts 7/8/9) are majority-voted into that cell's bit; <code>decideBit</code> writes each into <code>rxShift</code> by index and, on the stop bit's high vote, strobes out <code>Just 0x4B</code>. This is the transmitter's waveform figure read from the sampling end.</figcaption>
</figure>

The first property is the whole receiver in four lines: build a clean
frame for a random byte, run it in, get the byte back:

```haskell
testProperty "RX decodes a clean 8N1 frame" $ property $ do
  b <- forAll genByte
  let out = runRx (frame b high)
  recovered out === [b]
  anyErr out === False
```

[Hedgehog] draws a random byte `b`, `frame b high` renders it as a clean
160-sample line with a proper high stop bit, and the assertions are
exactly the contract: `recovered out === [b]` --- one byte came back, and
it was `b`, bit-for-bit --- and `anyErr out === False` --- no framing error
along the way. Every claim we have made about the receiver has to hold at
once for this to pass: the synchronizer has to pass the line through, the
edge detector has to catch the start, the center sampling has to land in
each cell, the vote has to read each bit, `decideBit` has to assemble them
LSB-first, and the stop bit has to close the frame and strobe the byte
out. It is the counterpart of the transmitter's waveform test, run in the
opposite direction, and it is the floor the other three build on.

The second flips the stop bit and demands the failure:

```haskell
testProperty "RX flags a framing error on a low stop bit" $ property $ do
  b <- forAll genByte
  let out = runRx (frame b low)
  recovered out === []
  anyErr out === True
```

The only change is `frame b low` --- the same start and data bits, but the
stop bit driven *low* instead of high, a malformed frame. Now the
assertions invert: `recovered out === []` --- **no** byte comes back, the
assembled `rxShift` is dropped and never strobed --- and `anyErr out ===
True` --- the framing-error flag fired. This is the `RxStop` arm's `low`
branch, `(Nothing, True)`, made into a property. The receiver does not
quietly hand up a byte from a frame that did not close; it raises the flag
and swallows the byte, and the test pins both halves of that behaviour.
The framing-error path the transmitter had no counterpart for is not
dead code --- it has a test that fails if it ever stops working.

The third is the majority vote made honest. It corrupts exactly one
sample --- at a bit center --- and demands the byte decode anyway:

```haskell
testProperty "RX majority vote rejects a single glitch" $ property $ do
  b <- forAll genByte
  let out = runRx (flipAt (16 + 3 * 16 + 8) (frame b high))
  recovered out === [b]
```

The instrument is `flipAt`, which inverts the single sample at one index:

```haskell
flipAt :: Int -> [Bit] -> [Bit]
flipAt k xs = [if j == k then complement x else x | (j, x) <- L.zip ([0 ..] :: [Int]) xs]
```

Everything is in the index, `16 + 3 * 16 + 8`, so read it as the test
author wrote it. The `16` skips the start bit's sixteen samples. The `3 *
16` skips three whole data bits --- bit 0, bit 1, bit 2. That lands us at
the start of data bit 3's window, and the `+ 8` steps eight samples into
it --- to offset 8, the dead center of the cell, where `rxCnt` reaches 8,
the middle of the three voted samples. So `flipAt (16 + 3*16 + 8)` reaches
into data bit 3 and flips *one* of the three samples the vote will read
--- `s8`, the center one. The other two, `s7` and `s9` at offsets 7 and 9,
still carry the true value.

And that is exactly the situation the majority vote was built for. Two of
the three samples of data bit 3 are correct, one is flipped; `maj` returns
what the two agree on; bit 3 decodes correctly despite the glitch; and the
whole byte comes back --- `recovered out === [b]`, with no weakened
assertion, no allowance for the corruption. The test does not check "the
byte is *mostly* right"; it checks the byte is *exactly* right, because
the vote makes the single glitch vanish. It is also, quietly, the argument
for three samples over one: had the receiver sampled the center only once,
this very flip would have hit that one sample and corrupted bit 3, and the
byte would have come back wrong. The test would fail against a single-sample
design and passes against this one. That is decision 5 of the [design][baudgen]
--- majority-of-three rejecting a glitch --- locked in silicon-shaped amber.

The fourth is the cleverest, and it guards the one property the first
three cannot see. Every frame the earlier tests built used *constant*
cells --- sixteen identical samples per bit --- so a receiver that sampled
the leading edge of each cell, or the trailing edge, or the center, would
decode them all identically. Those tests cannot tell center sampling from
edge sampling, because on a constant cell there is nothing to tell. This
one builds a cell that is *not* constant:

```haskell
centerCell :: Bit -> [Bit]
centerCell v = [if 6 <= j && j <= 10 then v else complement v | j <- [0 .. 15 :: Int]]

centerFrame :: BitVector 8 -> [Bit]
centerFrame b =
  L.replicate 16 low
    <> L.concatMap (centerCell . bitAt b) [0 .. 7]
    <> L.replicate 16 high
```

`centerCell v` carries the true value `v` only in its center --- offsets
6 through 10 --- and the *complement* of `v` everywhere else, at the edges.
`centerFrame` frames a byte out of these poisoned cells. Now sampling
position is everything: a receiver that reads the center (offsets 6–10,
which is where counts 7, 8, 9 land) recovers `v`; a receiver that reads a
cell boundary --- say because its `rxCnt` were initialised half a bit early,
so it sampled near offset 0 or 15 --- recovers `complement v`, the exact
opposite byte. The test demands the center reading:

```haskell
testProperty "RX samples the bit center, not the edge" $ property $ do
  b <- forAll genByte
  let out = runRx (centerFrame b)
  recovered out === [b]
  anyErr out === False
```

`recovered out === [b]` --- the true byte, not its complement --- and no
error. This is a **regression guard**, and a sharp one: it pins the whole
center-sampling design against a plausible mistake. If someone "simplified"
the alignment and started sampling near a bit boundary, every earlier test
would still pass --- constant cells don't care --- but this one would fail,
recovering the complement of every byte. The test comment says exactly
this: *a half-bit-early `rxCnt` init recovers the complement and fails
here, while the constant-cell tests above cannot tell center from edge.*
It is the property that makes "sample the center" a checked invariant
rather than a comment, and it is the reason Figure C's whole argument ---
center, not edge --- is load-bearing rather than decorative.

<figure class="rxcve-fig" style="margin:2rem 0">
<svg class="rxcve" viewBox="0 0 760 238" role="img" aria-labelledby="rxcve-t rxcve-d" xmlns="http://www.w3.org/2000/svg">
<title id="rxcve-t">Why center sampling matters: a cell that lies at its edges</title>
<desc id="rxcve-d">One data cell built by centerCell for a true bit of 1. Its center, offsets 6 through 10, carries the true value high; its edges, offsets 0 through 5 and 11 through 15, carry the complement low. An accent band highlights the center. The three center samples at offsets 7, 8 and 9 land in the true-value band and read 1, recovering the byte. A hollow marker near offset 1 shows where a boundary sampler would land, in the complement region, reading 0 and recovering garbage. This is the regression the centerFrame test guards.</desc>
<style>
.rxcve{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rxcve .line{stroke:var(--fg-main);stroke-width:2.5;fill:none}
.rxcve .band{fill:var(--accent);opacity:0.13}
.rxcve .dot{fill:var(--fg-dim)}
.rxcve .dotA{fill:var(--accent)}
.rxcve .bnd{fill:none;stroke:var(--accent);stroke-width:2}
.rxcve text{font-family:var(--sans)}
.rxcve .num{fill:var(--fg-dim);font-family:var(--mono);font-size:10px}
.rxcve .samp{fill:var(--accent);font-family:var(--mono);font-size:11px}
.rxcve .lab{fill:var(--fg-main);font-size:12px}
.rxcve .acc{fill:var(--accent);font-size:11.5px}
.rxcve .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:11px}
</style>
<rect class="band" x="291" y="60" width="165" height="98"/>
<text class="acc" x="373" y="52" text-anchor="middle">centerCell 1: TRUE only at offsets 6–10</text>
<text class="sig" x="86" y="84" text-anchor="end">high (v)</text>
<text class="sig" x="86" y="144" text-anchor="end">low (¬v)</text>
<polyline class="line" points="95,140 291,140 291,80 456,80 456,140 620,140"/>
<circle class="dot" cx="110" cy="140" r="3"/>
<circle class="bnd" cx="143" cy="140" r="5.5"/>
<circle class="dot" cx="176" cy="140" r="3"/>
<circle class="dot" cx="209" cy="140" r="3"/>
<circle class="dot" cx="242" cy="140" r="3"/>
<circle class="dot" cx="275" cy="140" r="3"/>
<circle class="dot" cx="308" cy="80" r="3"/>
<circle class="dotA" cx="341" cy="80" r="5"/>
<circle class="dotA" cx="374" cy="80" r="5"/>
<circle class="dotA" cx="407" cy="80" r="5"/>
<circle class="dot" cx="440" cy="80" r="3"/>
<circle class="dot" cx="473" cy="140" r="3"/>
<circle class="dot" cx="506" cy="140" r="3"/>
<circle class="dot" cx="539" cy="140" r="3"/>
<circle class="dot" cx="572" cy="140" r="3"/>
<circle class="dot" cx="605" cy="140" r="3"/>
<text class="samp" x="341" y="70" text-anchor="middle">s7</text>
<text class="samp" x="374" y="70" text-anchor="middle">s8</text>
<text class="samp" x="407" y="70" text-anchor="middle">s9</text>
<text class="num" x="110" y="170" text-anchor="middle">0</text>
<text class="num" x="143" y="170" text-anchor="middle">1</text>
<text class="num" x="176" y="170" text-anchor="middle">2</text>
<text class="num" x="209" y="170" text-anchor="middle">3</text>
<text class="num" x="242" y="170" text-anchor="middle">4</text>
<text class="num" x="275" y="170" text-anchor="middle">5</text>
<text class="num" x="308" y="170" text-anchor="middle">6</text>
<text class="num" x="341" y="170" text-anchor="middle">7</text>
<text class="num" x="374" y="170" text-anchor="middle">8</text>
<text class="num" x="407" y="170" text-anchor="middle">9</text>
<text class="num" x="440" y="170" text-anchor="middle">10</text>
<text class="num" x="473" y="170" text-anchor="middle">11</text>
<text class="num" x="506" y="170" text-anchor="middle">12</text>
<text class="num" x="539" y="170" text-anchor="middle">13</text>
<text class="num" x="572" y="170" text-anchor="middle">14</text>
<text class="num" x="605" y="170" text-anchor="middle">15</text>
<text class="acc" x="374" y="196" text-anchor="middle">center samples 7/8/9 → read 1 (recover the byte)</text>
<text class="lab" x="143" y="214" text-anchor="middle" style="font-size:11px">boundary sampler → reads 0, the complement (fails)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The trap the <code>centerFrame</code> test sets. Each data cell (here a true bit of <code>1</code>) carries its real value <em>only</em> in the center band, offsets 6–10, and the <em>complement</em> at both edges. The receiver's three samples (counts 7/8/9, accent) land squarely in the true band and read <code>1</code>, so the byte decodes. A receiver that sampled near a boundary — the hollow marker, where a half-bit-early <code>rxCnt</code> would land — would read <code>0</code>, the complement, and recover the opposite byte. Constant-cell tests cannot see this difference; this one can, which is why it pins center sampling as a checked invariant.</figcaption>
</figure>

## The keystone closes

The remaining three properties are not the receiver's alone --- they are
the whole UART's, and the [transmitter][tx] post met them from its side.
Read now from the receiver's end, they are the moment the series' long
promise pays off. The [baud generator][baudgen] closed by saying its one
blunt tick-rate test was only half a keystone, and that we would meet the
other half "when the receiver is built." The receiver is built. Here is
the other half.

The fast loopback wires the transmitter's line straight into the
receiver's, sharing one tick:

```haskell
fastLoop txByte = rxByte
 where
  tick = pure True
  (txLine, _txReady) = uartTx tick txByte
  (rxByte, _rxErr) = uartRx tick txLine
```

`uartTx`'s output line *is* `uartRx`'s input line --- no wire, no model,
no golden waveform in between, just the two halves of the UART bolted face
to face. Feed a byte to the transmitter and ask the receiver what it got:

```haskell
testProperty "TX->RX fast loopback recovers the byte" $ property $ do
  b <- forAll genByte
  let out = runFastLoop [Nothing, Just b] 400
  [b' | Just b' <- out] === [b]
```

The byte comes back, bit-for-bit. This is the test that would catch any
disagreement between the two halves that a one-sided test could miss: an
LSB/MSB slip, an off-by-one on the stop bit, a sampling point a tick too
early. The transmitter serialises out of a right-shifting register
LSB-first; the receiver assembles by indexed writes LSB-first; and the
only way the byte survives the round trip is if those two conventions
agree exactly. They check each other, with no third party to be wrong.

The second is the transmitter's handshake, seen from the receiving end.
The caller jams `Just b` at the transmitter a hundred cycles running, and
the receiver counts the result:

```haskell
testProperty "TX ignores input while busy (ready gating)" $ property $ do
  b <- forAll genByte
  let out = runFastLoop (Nothing : L.replicate 100 (Just b)) 400
  [b' | Just b' <- out] === [b]
```

Exactly one byte comes out of the receiver, not a hundred and not a
garbled smear of restarted frames. From the transmitter's side this was
"a `Just` presented mid-transmission is ignored"; from the receiver's
side it is "I got precisely one clean frame." The receiver is the witness
that the transmitter's no-corruption property actually holds on the wire.

The third swaps the always-true tick for the real thing:

```haskell
fullLoop txByte = rxByte
 where
  (rxByte, _err, txLine, _rdy) = uart (SNat @2_000_000) rxLine txByte
  rxLine = txLine

testProperty "full UART loopback (real NCO) recovers the byte" $ property $ do
  b <- forAll genByte
  let out = runFullLoop (Nothing : L.replicate 60 (Just b)) 1200
  [b' | Just b' <- out] === [b]
```

`fullLoop` calls the umbrella `uart (SNat @2_000_000)` --- [baud
generator][baudgen], transmitter, and receiver all wired together --- and
loops the transmitter's line back to the receiver's. Now a bit is not
sixteen cycles but fifty, tiled by sixteen NCO ticks whose gaps alternate
three and four clocks, averaging the fractional 3.125 the [baud
generator][baudgen] fought so hard to keep honest. And the byte still
comes back. That single passing assertion is the entire UART's keystone:
the fractional-3.125 tick spacing, the transmitter's Moore-steady
sixteen-tick bits, the synchronizer, the falling-edge start detection, the
center sampling, the majority vote, the early stop resolution --- every
one of them has to be right *at once*, because the byte is recovered only
if the tick lands sixteen times per bit, the transmitter holds each level
flat, and the receiver samples each center. The [baud generator's][baudgen]
lone tick-rate test measured the heartbeat; this test spends it, end to
end, and gets the byte back. The keystone the [baud generator][baudgen]
opened, and the [transmitter][tx] began to close, is closed here: the
heartbeat carries a byte across, TX line to RX line, and it arrives
whole.

## What we read

Thirty-odd lines, one exported name, and the transmitter turned inside
out. The skeleton was the transmitter's --- the same four-constructor
`RxState`, the same record carried tick to tick, the same `Index 16`
counting sixteen ticks a bit, the same `mealy` at the top, the same
freeze-off-tick cadence, the same LSB-first byte --- so we read all of that
fast, in a mirror. The post's words went where the mirror breaks. The
line is an *input* now, asynchronous from a pin, so a **2-flop
synchronizer** clocked every cycle catches it before the tick-gated logic
dares look --- the one part of the receiver with no transmitter analogue,
because you do not synchronize a wire you drive. The start bit is *hunted*
by its falling **edge**, tracked through `rxPrev`, so a line merely held
low can never fake a frame. Each bit is decided at its **center** by a
**three-sample majority vote** on counts 7, 8, 9 --- odd so it cannot tie,
three because that is the cheapest count that survives a glitch, tight
around the center so drift never strays onto an edge, and cheap enough to
be three flip-flops and a boolean --- which is the actual point of
oversampling sixteen times. A low stop bit raises a **framing error** and
drops the byte. The stop bit resolves **early**, at count 9, so
back-to-back frames resync. And the outputs are **genuinely Mealy**
strobes --- one-cycle events that read the `tick` input --- where the
transmitter's were steady Moore levels, because levels want Moore and
events want Mealy, one machine at each end.

The [transmitter][tx] taught us the silhouette --- find the `mealy`, find
the step function, split it into outputs and next state, decide Moore or
Mealy by whether the *output* reads the input. The receiver wears that
silhouette and fills in the half the transmitter left blank: the input
side of a UART, where you do not own the wire and must *recover* what
someone else put on it. Between the two of them the byte-exact loopback
finally closes --- the heartbeat the [baud generator][baudgen] started now
carries a byte from one end to the other and back, with the real
fractional tick, the two halves checking each other with no reference
model in sight.

Which is the whole UART, and it is behind us. What waited on the far side
of the transport --- the thing the transport exists to serve --- is the
Engine: the stateful difficulty spike the [design][baudgen] kept
deferring, the eSPI machine the [CRC][crc] was a limb of and the
[introduction][intro] promised. It has the same silhouette we have now
read four times --- a `mealy`, a pure step, a sum for the phase and a
record for the rest --- but it is bigger than a frame, and it is where the
project has been heading all along. The link is up; next we start sending
it something worth carrying.

[^metastability]: **Metastability** is the failure a synchronous flip-flop
suffers when its data input violates the *setup/hold* window --- the small
interval around the clock edge during which the input must be stable. A
signal that changes inside that window can leave the flop's output
balanced between `0` and `1`, at an invalid intermediate voltage, for an
unbounded time before it resolves *randomly* to one rail or the other. It
is not a bug you can code around; it is physics, the metastable point of a
bistable element being a genuine (if unstable) equilibrium. You cannot
*prevent* an asynchronous input from occasionally hitting the window ---
the RX line comes from a host whose clock bears no relation to `Dom100`,
so eventually a line edge coincides with a Tamal clock edge --- so instead
you *contain* it: sample into `sync1`, then give it a whole clock period,
untouched, to fall off the knife-edge before `synced` reads it. The
governing figure of merit is **mean time between failures**, which grows
roughly exponentially with the settling time you allow; one extra
flip-flop turns an MTBF of seconds into one of centuries. Two flops is the
industry-standard minimum for a slow-ish single-bit crossing like a UART
line, which is exactly what `register high rxLine` twice provides. The
`high` initial value is not about metastability but about *belief*: the
idle line is high, so the synchronizer and `rxPrev` both power up assuming
the world was quiet, which stops a phantom start edge on the first tick.
Multi-bit crossings (a whole byte at once) need more than a 2-flop
synchronizer --- gray coding, or a handshake, or an asynchronous FIFO ---
which is one more reason the [design][baudgen] kept the UART in a single
clock domain and paid the synchronizer only on the one genuinely
asynchronous bit, the incoming line.

[^edge]: Edge detection on a synchronized signal is the standard idiom:
register the signal, then compare the registered copy against the live
one, and a difference is an edge --- `rxPrev s == high && line == low` is
precisely "the previous sample was high and this one is low," a falling
edge. Doing it on the *synchronized* line rather than the raw pin matters:
comparing two samples of a metastable signal could see an edge that isn't
there, so the synchronizer must come first. The sub-tick latency is the
price of checking only once per tick rather than continuously: the true
edge can fall anywhere in a tick interval, so the observing tick lags it
by up to one tick. A design that cared could halve this by sampling the
edge on the fast clock instead of the tick, but the UART does not care,
because the whole point of aligning to the bit *center* is to put eight
ticks of slack between the sample and the nearest edge --- far more than
the sub-tick start error and the accumulated baud drift combined. The lag
is real and the center makes it free.

[^vote]: The odd-count requirement is intrinsic to majority voting: with
an even number of voters a tie is possible and "the majority" is
undefined, so you either add a tie-break (complexity, and an arbitrary
bias) or use an odd count and avoid the question. Among odd counts, the
returns diminish fast. One sample tolerates zero faults; three tolerate
one; five tolerate two; in general `2k+1` samples tolerate `k`. The fault
being guarded against is a glitch landing on a sample *at the bit center*,
within the three-tick window 7–9 --- a rare event to begin with, since the
center is the quietest part of the settled line --- and the probability of
*two* independent such glitches in the same window of the same bit is that
rare event squared, which at 2 Mbaud over a short board trace is never.
Three samples buy the entire realistic benefit; five would spend more
flip-flops and a wider vote to guard a case that does not occur. This is
decision 5 of the [design][baudgen], and it is a nice instance of sizing a
mechanism to the actual threat rather than to the most general one
imaginable --- the same instinct that chose a one-way `ready` handshake
over full backpressure in the transmitter.

[^assemble]: The receiver's indexed assembly and the transmitter's shift
are two ways to (de)serialise the same LSB-first byte, and it is worth
seeing why the receiver picks the one it does. The transmitter *shifted*:
it held the byte in a register, always read the bottom bit, and slid the
register right each bit so the next bit fell into the read position --- the
byte moved past a fixed tap. The receiver instead holds the byte still and
writes bit `i` directly with `setBit`/`clearBit (rxShift s) (fromEnum i)`,
addressing the register by index. Either would work for either direction;
the [design doc][baudgen] even offers both in one breath --- "shift the
sampled bit into the MSB and shift right, *or* index by `i`." Indexing
reads a hair more clearly on the receive side, where the phase `RxData i`
already carries the index in hand, so writing bit `i` is a direct
transcription of "this is data bit `i`." It is the [CRC][crc]'s and the
[transmitter's][tx] shift-register motif a third time --- the eSPI CRC
shifted *left*, MSB-first, feeding a polynomial division; the transmitter
shifted *right*, LSB-first, feeding the wire; the receiver indexes,
LSB-first, filling the byte --- the same eight bits handled three ways by
three standards' conventions, a shift or an index apart.

[^unreachable]: Both `captureSample`'s `_ -> s` and `decideBit`'s `RxIdle
-> (s, (Nothing, False))` carry the comment `-- actually unreachable`, and
both are the same totality tax the [transmitter][tx] paid for `TxIdle ->
s`. Haskell sanctions partial functions --- `head`, `fromJust`, and their
kin are non-exhaustive by design --- so it treats totality as an opt-in
diagnostic (`-Wincomplete-patterns`) rather than a law, and Tamal opts in
via the `-Wall` its cabal turns on. A `case` that omits a constructor is a
warning; escalated with `-Werror=incomplete-patterns` it is a build
failure. For gateware that escalation is the right call, because silicon
has no `PatternMatchFail` to throw --- an unhandled case would lower to a
don't-care, a silent wrong answer in the netlist rather than a catchable
exception. So the unreachable arms are written out, returning the safe
do-nothing, to keep every `case` total and the compiler satisfied that
there are no holes even in the corners control never reaches. The word
"unreachable" is a claim about the *caller* --- `captureSample` is only
called at counts that could be 7/8/9, `decideBit` only from a resolving
tick --- that the *type* cannot express, so the wildcard stands in for the
proof the compiler cannot check.

[^strobe]: One could make the receiver's byte output Moore instead --- hold
the completed byte in a state field and expose a separate "valid for one
cycle" flag, or present the byte on a register that updates only at frame
end --- and some UART IP does exactly that, offering a *held* received byte
plus a separate strobe. Tamal chooses the Mealy strobe: `Just byte` on
exactly the resolving cycle and `Nothing` otherwise, the byte carried
*inside* the strobe rather than held beside it. It is cheaper (no output
register, no held-byte field) and it composes cleanly with the shell's
load FSM, which will consume `rxByte` strobes as they fire. The cost is
the one-cycle contract: a consumer must latch the byte the cycle it
appears, because it is gone the next. That is a perfectly ordinary
discipline in synchronous logic --- it is the same shape as the
transmitter's "present `Just b` while `ready`" --- and it is why the output
is honestly Mealy rather than Moore: the strobe is an event, and events
read the clock-enable that times them.
