+++
title = "Tamal: The transmitter"
date = 2026-07-22T09:00:00
description = "Reading Tamal's UART transmitter end to end: the first block where mealy climbs to the top of a module, exactly as the primer promised — the two types that are its state (a sum for the frame phase, a record for everything carried tick to tick), why txStep has the exact s -> i -> (s, o) shape of a Mealy transition and yet drives a pair of Moore outputs, where the live input really does reach in (the un-tick-gated one-cycle handshake), how txAdvance walks an 8N1 frame LSB-first out of a shift register, and how a byte-exact TX->RX loopback begins to close the keystone the baud generator left open."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "uart", "state-machine"]
[extra]
math = true
+++

The [baud generator][baudgen] we read last time produces a *heartbeat* ---
a `Bool` that says *now* sixteen times a bit --- and nothing else. It had
no state machine at all, just a phase accumulator counting against a
modulus. That was the point: the smallest possible clocked block, a
`register` fed back through some arithmetic, so we could meet sequential
logic without also meeting a finite-state machine on the same day. The
post closed by promising that the next block would start *spending* that
heartbeat. This is that block.

The transmitter takes the tick and a byte and drives a wire high and low
in an 8N1 frame --- and it is where `mealy` finally climbs to the top of
a module, exactly as the [primer] said it would and the baud-generator
post repeated on its way out. Every block so far has been a warm-up for
this line:

```haskell
uartTx tick mbyte = unbundle (mealy txStep initTx (bundle (tick, mbyte)))
```

The primer told you to read a `mealy` at the top of a module as "clocked
state machine, its brain the pure function beside it." Here is the
module; here is the brain. But there is a twist the code's own comment
insists on, and it is a better lesson than a plain Mealy machine would
have been: the thing is *lifted* with `mealy`, its transition has the
textbook Mealy *shape* --- and yet its outputs are pure **Moore**. Sorting
out that apparent contradiction is most of what this post is for.

Like the CRC and the baud generator, the whole transmitter fits in a
screenful and change.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the license header and the doc-comments, here is
`src/Tamal/Uart/Tx.hs` in full:

```haskell
module Tamal.Uart.Tx
  ( uartTx
  ) where

import Clash.Prelude

data TxState = TxIdle | TxStart | TxData (Index 8) | TxStop
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data TxS = TxS
  { txState :: TxState
  , txShift :: BitVector 8
  , txCnt :: Index 16
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

uartTx ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bool ->
  Signal dom (Maybe (BitVector 8)) ->
  (Signal dom Bit, Signal dom Bool)
uartTx tick mbyte = unbundle (mealy txStep initTx (bundle (tick, mbyte)))
 where
  initTx :: TxS
  initTx = TxS TxIdle 0 0

txStep :: TxS -> (Bool, Maybe (BitVector 8)) -> (TxS, (Bit, Bool))
txStep s (tick, mbyte) = (s', (line, ready))
 where
  ready = txState s == TxIdle
  line = case txState s of
    TxIdle -> high
    TxStart -> low
    TxData _ -> lsb (txShift s)
    TxStop -> high
  s'
    | TxIdle <- txState s = case mbyte of
        Just b -> s{txState = TxStart, txShift = b, txCnt = 0}
        Nothing -> s
    | not tick = s
    | txCnt s /= maxBound = s{txCnt = txCnt s + 1}
    | otherwise = txAdvance s

txAdvance :: TxS -> TxS
txAdvance s = case txState s of
  TxStart -> s{txState = TxData 0, txCnt = 0}
  TxData i
    | i == maxBound -> s{txState = TxStop, txCnt = 0}
    | otherwise -> s{txState = TxData (i + 1), txShift = txShift s `shiftR` 1, txCnt = 0}
  TxStop -> s{txState = TxIdle, txCnt = 0}
  TxIdle -> s
```

Four things, top to bottom: two `data` declarations that *are* the state
machine's memory, a five-line `uartTx` that is nothing but the `mealy`
lift and its plumbing, the pure `txStep` that is the machine's whole
brain, and a `txAdvance` helper that walks the frame forward one bit at a
time. We will read them roughly in that order, but the two types come
first, because everything else is written in terms of them.

## One exported name

The opening beat is the CRC module's and the baud generator's, played a
third time, so I will be quick:

```haskell
module Tamal.Uart.Tx
  ( uartTx
  ) where

import Clash.Prelude
```

`module Tamal.Uart.Tx` names the module after its path on disk ---
`src/Tamal/Uart/Tx.hs`, a dot per directory --- and the parenthesised
export list is the one door in the wall. Only `uartTx` leaves the file;
`txStep`, `txAdvance`, `TxState`, and `TxS` are all sealed behind it, not
because the list declines to mention them but because everything private
is written where nothing else can name it. `import Clash.Prelude` is the
same prelude swap the [CRC post][crc] dwelt on --- the line that throws out
ordinary Haskell's furniture and moves in `Signal`, `Bit`, `BitVector`,
`register`, `mealy`, and the rest of the vocabulary that lowers to gates.
I will not re-derive either; the [CRC][crc] and [baud-generator][baudgen]
posts did that at length.

One thing is *missing* that both earlier modules had: there is no
`{-# LANGUAGE NumericUnderscores #-}` pragma, because this file never
writes a twelve-digit literal. The transmitter has no clock frequency to
name --- the frequency lives in the baud generator, upstream, folded into
the tick this module simply consumes. That absence is worth noticing,
because it is the first hint of how the UART is factored: each block owns
exactly one idea, and the transmitter's idea is not *timing*, it is
*framing*.

## The type: a byte in, a line out

Now the signature, which under the primer's reading is already half the
documentation:

```haskell
uartTx ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bool ->
  Signal dom (Maybe (BitVector 8)) ->
  (Signal dom Bit, Signal dom Bool)
```

Read it against the baud generator's and the first thing you notice is
what is *gone*. `oversampleTick` needed a `forall`, an `SNat baud`
argument, and a `KnownNat`/`KnownDomain` pile of constraints, because it
had to read a clock frequency out of the domain type and turn a baud rate
into an increment. The transmitter needs none of that. It has one
constraint, `HiddenClockResetEnable dom` --- there is a clock (and reset,
and enable) threaded implicitly, which is what `mealy`'s hidden
`register` will draw on --- and then two plain arrows in and a pair out.
No numbers in the type at all. The transmitter does not know or care what
the baud rate is; it knows only "a bit is sixteen ticks," and the ticks
arrive from elsewhere.

Take the two inputs in turn.

`Signal dom Bool` is the oversample tick --- the [baud generator's][baudgen]
output wire, plugged straight in. Under the primer's reading it is "a
`Bool` that may change on every clock tick," and we spent a whole post
establishing that this particular `Bool` is an *enable*, true on the
cycles the UART should advance and false on the rest. The transmitter is
one of the two things that enable was built to gate. Every bit-advancing
decision below is downstream of this wire being `True`.

`Signal dom (Maybe (BitVector 8))` is the send request, and the `Maybe`
is doing exactly the job the [primer] gave it. There, a step returning
`Maybe Ring` stated a hardware fact --- *at most one trace-RAM write
happens this cycle* --- a write-enable promoted to a type. Here the
direction is reversed but the idea is identical: `Just b` on some cycle
means "please transmit this byte," and `Nothing` means "I have nothing
for you." The absence lives in the type, out in the open; there is no
sentinel byte, no separate valid line to forget to check. A send request
is a byte that might not be there, and its type says so.

That leaves the result, `(Signal dom Bit, Signal dom Bool)` --- a pair of
output wires:

- `Signal dom Bit` is `line`, the serial output, one `Bit` per cycle. It
  idles high and is driven low and high through a frame. This is the
  physical wire that, on the board, runs to the USB-UART bridge and out
  to the host.
- `Signal dom Bool` is `ready`, a flag that is high exactly when the
  transmitter is idle and can accept a new byte. It is one half of a
  one-cycle handshake; the caller is meant to present `Just b` only on a
  cycle when `ready` is high.

So the whole interface is: *here is a heartbeat and maybe a byte; take
the wire I drive and a flag that says whether I'm listening.* This is the
"strobe/handshake" interface the [UART design][baudgen] chose over full
valid/ready backpressure, and it is cheap because it can afford to be ---
at 2 Mbaud a byte spans roughly five hundred `Dom100` cycles, so a
consumer draining bytes has five hundred cycles of slack and never needs
to stall the line.[^backpressure]

## Two types that are the state

Everything the transmitter remembers lives in two `data` declarations,
and they are worth reading slowly, because they are the [primer]'s
sum-and-product story told in hardware. The user of this UART never sees
either type --- they are sealed behind the export list --- but they *are*
the machine.

The first is the phase:

```haskell
data TxState = TxIdle | TxStart | TxData (Index 8) | TxStop
```

Read the `|` as "or": a `TxState` is `TxIdle` **or** `TxStart` **or**
`TxData` (carrying an `Index 8`) **or** `TxStop`, and nothing else. This
is the primer's **sum type** --- the FSM's state register written as a
type, the same shape as the engine's `Phase` from that post. Four of a
UART frame's five ideas are here by name: the line is idle, or driving
the start bit, or driving one of the data bits, or driving the stop bit.

The interesting constructor is `TxData (Index 8)`. It is a
*data-bearing* constructor, the primer's `Circle Double` pattern: the
tag `TxData` travels with a payload, and the payload's type is `Index 8`
--- a number in the range `0..7` and, by construction, *never* 8. So
`TxData` is not one state but eight, one per data bit, and the index that
distinguishes them cannot stray out of range because the type will not
let the value `8` exist in the first place. Count the inhabitants and the
whole state space is exactly `1 + 1 + 8 + 1 = 11`: idle, start, eight
data, stop. The compiler knows that number, which is why a `case` over a
`TxState` that forgets a constructor is a warning at build time --- the
primer's exhaustiveness check, standing guard over the state machine, for
free.[^index]

The `deriving` block is the primer's incantation, and by now it should
read as ordinary furniture rather than noise:

```haskell
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

`Show` so a failing test can print the state it choked on; `Eq` so
`txState s == TxIdle` a few lines down is legal; `Generic` as the
structural plumbing other machinery builds on; and `NFDataX`, the one
that is really about hardware --- Clash's way of saying *this type is
allowed to sit in a register*, its values, including "undefined at
power-up," well-defined enough to be stored in flip-flops. A `TxState`
is going into a register in a moment, so it must earn `NFDataX`, and
`deriving anyclass` is how it does.

The second type is everything the machine carries from one tick to the
next:

```haskell
data TxS = TxS
  { txState :: TxState
  , txShift :: BitVector 8
  , txCnt :: Index 16
  }
```

This is the primer's **product type** --- a record, a struct, a bundle
that holds one field *and* another *and* another. Where the engine's
state was a big `State { phase, pc, regs, ... }`, the transmitter's is a
small `TxS { txState, txShift, txCnt }`, and the record syntax hands us
three accessor functions for free: `txState :: TxS -> TxState`,
`txShift :: TxS -> BitVector 8`, `txCnt :: TxS -> Index 16`. Each field
earns its place:

- **`txState`** is the phase we just met --- where in the frame we are.
- **`txShift`** is the latched byte, held in a `BitVector 8` and shifted
  right as the frame progresses so its least-significant bit always
  presents the *next* bit to send. It is eight wires with no arithmetic
  meaning --- the CRC post's reason for `BitVector` over `Unsigned` ---
  because we only ever shift it and read its bottom bit, never add to it.
- **`txCnt`** is the position *within* the current bit, an `Index 16`
  counting the sixteen oversample ticks that make one bit-time. Again the
  width is load-bearing: `0..15`, never 16, so "have we held this bit for
  a full sixteen ticks?" is exactly "has `txCnt` reached `maxBound`?"

Add it up and the entire memory of the transmitter is a phase (four bits
would over-cover its eleven states), a byte, and a four-bit counter ---
call it fifteen bits of flip-flop. That is the whole `s` that `mealy` is
about to clock. Hold the shape of it in your head; the rest of the file
is just the pure function that turns one `TxS` into the next.

<figure class="txfsm-fig" style="margin:2rem 0">
<svg class="txfsm" viewBox="0 0 760 236" role="img" aria-labelledby="txfsm-t txfsm-d" xmlns="http://www.w3.org/2000/svg">
<title id="txfsm-t">The transmitter's TxState finite-state machine</title>
<desc id="txfsm-d">Four state nodes in a row: TxIdle (line high), TxStart (line low), TxData i (line equals data bit i), and TxStop (line high). TxIdle has a dashed self-loop labelled Nothing, meaning it stays idle when no byte is offered, and an accented arrow to TxStart labelled Just b, the input-driven handshake that is not tick-gated. TxStart advances to TxData 0 after sixteen ticks; TxData has a self-loop that increments the data-bit index and shifts the latched byte right while i is less than seven; after data bit seven it advances to TxStop; TxStop returns to TxIdle after sixteen ticks. The line value shown under each state is a Moore output, a pure function of the state.</desc>
<style>
.txfsm{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.txfsm .st{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.txfsm .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.txfsm .hs{stroke:var(--accent);stroke-width:2.5;fill:none}
.txfsm .loop{stroke:var(--fg-main);stroke-width:2;fill:none}
.txfsm .idleloop{stroke:var(--fg-dim);stroke-width:2;fill:none;stroke-dasharray:4 4}
.txfsm text{font-family:var(--sans)}
.txfsm .name{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.txfsm .lab{fill:var(--fg-main);font-size:12px}
.txfsm .labA{fill:var(--accent);font-size:12px}
.txfsm .dim{fill:var(--fg-dim);font-size:11.5px}
.txfsm .ah{fill:var(--fg-main)}
.txfsm .aha{fill:var(--accent)}
</style>
<defs>
<marker id="txfsm-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="txfsm-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<rect class="st" x="51" y="92" width="88" height="54" rx="8"/>
<rect class="st" x="241" y="92" width="88" height="54" rx="8"/>
<rect class="st" x="431" y="92" width="88" height="54" rx="8"/>
<rect class="st" x="621" y="92" width="88" height="54" rx="8"/>
<path class="idleloop" d="M82,92 C79,63 111,63 108,92" marker-end="url(#txfsm-a)"/>
<path class="loop" d="M462,92 C458,58 492,58 488,92" marker-end="url(#txfsm-a)"/>
<line class="hs" x1="139" y1="119" x2="239" y2="119" marker-end="url(#txfsm-aa)"/>
<line class="wire" x1="329" y1="119" x2="429" y2="119" marker-end="url(#txfsm-a)"/>
<line class="wire" x1="519" y1="119" x2="619" y2="119" marker-end="url(#txfsm-a)"/>
<path class="wire" d="M665,146 L665,200 L95,200 L95,148" marker-end="url(#txfsm-a)"/>
<text class="name" x="95" y="114" text-anchor="middle">TxIdle</text>
<text class="name" x="285" y="114" text-anchor="middle">TxStart</text>
<text class="name" x="475" y="114" text-anchor="middle">TxData i</text>
<text class="name" x="665" y="114" text-anchor="middle">TxStop</text>
<text class="dim" x="95" y="133" text-anchor="middle">line high</text>
<text class="dim" x="285" y="133" text-anchor="middle">line low</text>
<text class="dim" x="475" y="133" text-anchor="middle">line = bit i</text>
<text class="dim" x="665" y="133" text-anchor="middle">line high</text>
<text class="dim" x="95" y="55" text-anchor="middle">Nothing</text>
<text class="lab" x="475" y="50" text-anchor="middle">i &lt; 7 → i+1, shiftR</text>
<text class="labA" x="190" y="107" text-anchor="middle">Just b → latch</text>
<text class="dim" x="190" y="134" text-anchor="middle">handshake,</text>
<text class="dim" x="190" y="148" text-anchor="middle">no tick</text>
<text class="lab" x="380" y="108" text-anchor="middle">16 ticks</text>
<text class="lab" x="570" y="108" text-anchor="middle">16 ticks</text>
<text class="dim" x="380" y="217" text-anchor="middle">stop bit done, 16 ticks → idle</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The transmitter's eleven states as an FSM. Every transition but one advances on the sixteenth oversample <code>tick</code> of a bit (plain arrows); the lone accented transition — accepting a byte out of <code>TxIdle</code> on a <code>Just b</code> — is driven by the <em>input</em> and is <em>not</em> tick-gated, which is what lets the <code>ready</code> handshake resolve in a single cycle. <code>TxData (Index 8)</code> packs eight states into one constructor; its self-loop shifts the latched byte right (<code>shiftR</code>) so <code>lsb</code> walks it out LSB-first. The line value under each state is the Moore output: a pure function of the state, steady for the whole bit.</figcaption>
</figure>

## `mealy` climbs to the top

Here is the line the entire series has been walking toward:

```haskell
uartTx tick mbyte = unbundle (mealy txStep initTx (bundle (tick, mbyte)))
 where
  initTx :: TxS
  initTx = TxS TxIdle 0 0
```

The [primer] gave us the type of `mealy` and asked us to hold it:

```haskell
-- mealy :: (s -> i -> (s, o)) -> s -> (Signal dom i -> Signal dom o)
```

Read it slowly against the code. `mealy` takes two things --- a pure step
function `s -> i -> (s, o)` and an initial state `s` --- and hands back a
*function on signals*, `Signal dom i -> Signal dom o`. Feed it `txStep`
(the step) and `initTx` (the initial state) and you get a machine that
consumes a stream of inputs and produces a stream of outputs, with a real
register, clocked, holding the state between cycles. That register is the
[baud generator's][baudgen] `register` seen again --- the primer's line
that "a Mealy machine is nothing but a `register` holding the state and a
pure function computing the next one," now assembled rather than used
bare. `initTx = TxS TxIdle 0 0` is the power-up state, the idle phase with
a zeroed shift register and counter; because `Dom100` carries no reset
port, that value is set by the flip-flops' initial state at
configuration, exactly as the baud generator's `register 0` was.

But `mealy` wants a *single* input signal and yields a *single* output
signal, and our transmitter has two of each --- two inputs (`tick` and
`mbyte`) and two outputs (`line` and `ready`). That mismatch is what
`bundle` and `unbundle` are for:

- `bundle (tick, mbyte)` takes the pair of signals
  `(Signal dom Bool, Signal dom (Maybe (BitVector 8)))` and zips them
  into one signal of pairs,
  `Signal dom (Bool, Maybe (BitVector 8))` --- the single `Signal dom i`
  that `mealy` accepts, with `i = (Bool, Maybe (BitVector 8))`.
- `unbundle` does the reverse on the way out: the machine produces one
  `Signal dom (Bit, Bool)`, and `unbundle` splits it back into the pair
  `(Signal dom Bit, Signal dom Bool)` that the signature promises.

The two are witnesses to a small, obvious isomorphism: a *signal of
pairs* and a *pair of signals* carry the same information, because a
bundle of wires is just several wires side by side, watched over
time.[^bundle] They add no gates; they are pure re-pairing at the
boundary, the adapters that let a two-in, two-out interface meet
`mealy`'s one-in, one-out shape. Strip them away and the sentence is
plain: *run `txStep` as a clocked machine starting from idle.*

<figure class="mly-fig" style="margin:2rem 0">
<svg class="mly" viewBox="0 0 760 300" role="img" aria-labelledby="mly-t mly-d" xmlns="http://www.w3.org/2000/svg">
<title id="mly-t">How the mealy combinator lowers uartTx to hardware</title>
<desc id="mly-d">Two input signals, tick and mbyte, enter a bundle block that combines them into a single signal i. Inside a dashed boundary labelled the mealy combinator sit a register holding the state TxS and the pure function txStep. The register feeds the current state s into txStep; txStep computes the next state s prime, drawn as an accented feedback wire returning to the register, and an output o. The output o leaves the mealy boundary into an unbundle block that splits it back into the two output signals line and ready.</desc>
<style>
.mly{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.mly .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.mly .mbox{fill:none;stroke:var(--fg-dim);stroke-width:1.5;stroke-dasharray:6 5}
.mly .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.mly .fb{stroke:var(--accent);stroke-width:2;fill:none}
.mly text{font-family:var(--sans)}
.mly .name{fill:var(--fg-main);font-family:var(--mono);font-size:14px}
.mly .lab{fill:var(--fg-main);font-size:12px}
.mly .dim{fill:var(--fg-dim);font-size:11.5px}
.mly .mlab{fill:var(--fg-dim);font-family:var(--mono);font-size:12.5px}
.mly .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.mly .sigA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.mly .ah{fill:var(--fg-main)}
.mly .aha{fill:var(--accent)}
</style>
<defs>
<marker id="mly-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="mly-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<rect class="mbox" x="222" y="64" width="326" height="178" rx="10"/>
<text class="mlab" x="385" y="55" text-anchor="middle">mealy txStep initTx</text>
<rect class="box" x="240" y="144" width="100" height="52" rx="6"/>
<rect class="box" x="410" y="110" width="120" height="120" rx="6"/>
<rect class="box" x="235" y="250" width="120" height="36" rx="6"/>
<rect class="box" x="578" y="144" width="90" height="52" rx="6"/>
<line class="wire" x1="340" y1="170" x2="408" y2="170" marker-end="url(#mly-a)"/>
<path class="fb" d="M470,110 V84 H290 V142" marker-end="url(#mly-aa)"/>
<path class="wire" d="M355,268 H470 V232" marker-end="url(#mly-a)"/>
<line class="wire" x1="530" y1="170" x2="576" y2="170" marker-end="url(#mly-a)"/>
<line class="wire" x1="60" y1="260" x2="233" y2="260" marker-end="url(#mly-a)"/>
<line class="wire" x1="60" y1="278" x2="233" y2="278" marker-end="url(#mly-a)"/>
<line class="wire" x1="668" y1="159" x2="714" y2="159" marker-end="url(#mly-a)"/>
<line class="wire" x1="668" y1="181" x2="714" y2="181" marker-end="url(#mly-a)"/>
<text class="name" x="290" y="170" text-anchor="middle">TxS</text>
<text class="dim" x="290" y="186" text-anchor="middle">init: idle</text>
<text class="dim" x="290" y="212" text-anchor="middle">register</text>
<text class="name" x="470" y="152" text-anchor="middle">txStep</text>
<text class="dim" x="470" y="172" text-anchor="middle">pure</text>
<text class="name" x="470" y="198" text-anchor="middle" style="font-size:12px">(s,i)→(s′,o)</text>
<text class="name" x="295" y="272" text-anchor="middle">bundle</text>
<text class="name" x="623" y="174" text-anchor="middle">unbundle</text>
<text class="sig" x="374" y="162" text-anchor="middle">s</text>
<text class="sigA" x="380" y="78" text-anchor="middle">s′  (next state)</text>
<text class="sig" x="470" y="248" text-anchor="middle">i</text>
<text class="sig" x="554" y="162" text-anchor="middle">o</text>
<text class="sig" x="52" y="263" text-anchor="end">tick</text>
<text class="sig" x="52" y="281" text-anchor="end">mbyte</text>
<text class="sig" x="719" y="163" text-anchor="start">line</text>
<text class="sig" x="719" y="185" text-anchor="start">ready</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">How <code>mealy</code> lowers to hardware. The dashed box is exactly what <code>mealy txStep initTx</code> builds: a <code>register</code> holding the state <code>TxS</code> (powering up idle) wired to the pure <code>txStep</code>, with the next state <code>s′</code> fed back through the register — the accent loop that makes this sequential rather than a combinational knot. <code>bundle</code> gathers the two input signals <code>tick</code> and <code>mbyte</code> into the single <code>i</code> the combinator consumes; <code>unbundle</code> splits the single output back into <code>line</code> and <code>ready</code>. The register is the <a href="https://balbi.sh/posts/tamal-uart-baudgen/">baud generator's</a> <code>register</code> seen again — the primer's "a Mealy machine is a register plus a pure function," assembled.</figcaption>
</figure>

What makes this the milestone the primer promised is that `mealy` has
finally climbed to the *top* of a module --- it is the thing `uartTx`
returns, the outermost structure of the block, not a helper buried
inside. The CRC was a pure function; the baud generator was a `register`
and some arithmetic in a `where` block. This is the first Tamal module
whose very shape is "a state machine," and every remaining block ---
the receiver, and the engine that is the whole point of the project ---
will have this same silhouette. Learn to read it here and you can read
all of them: find the `mealy`, find the step function beside it, and the
rest is detail.

## `txStep`: the pure transition

`txStep` is the brain. It is an ordinary, pure, total function --- no
`Signal`, no clock, no simulator --- and that is exactly what the
[introduction post][intro] meant when it said the tests run in under a
second: you can hammer a function like this with thousands of inputs in
milliseconds, and only once you trust it does `mealy` make it hardware.
Here it is again, on its own:

```haskell
txStep :: TxS -> (Bool, Maybe (BitVector 8)) -> (TxS, (Bit, Bool))
txStep s (tick, mbyte) = (s', (line, ready))
 where
  ready = txState s == TxIdle
  line = case txState s of
    TxIdle -> high
    TxStart -> low
    TxData _ -> lsb (txShift s)
    TxStop -> high
  s'
    | TxIdle <- txState s = case mbyte of
        Just b -> s{txState = TxStart, txShift = b, txCnt = 0}
        Nothing -> s
    | not tick = s
    | txCnt s /= maxBound = s{txCnt = txCnt s + 1}
    | otherwise = txAdvance s
```

Look first at the *type*, because it is the primer's promise made
literal:

```haskell
txStep :: TxS -> (Bool, Maybe (BitVector 8)) -> (TxS, (Bit, Bool))
```

That is `s -> i -> (s, o)` exactly, with `s = TxS`,
`i = (Bool, Maybe (BitVector 8))`, and `o = (Bit, Bool)`. Current state
and input on the left; next state and output on the right. This is the
shape `mealy` demands and the shape the primer told you to look for. The
whole clocked machine is this one function with a register wrapped around
it, and this function is where all the behaviour lives.

The body splits cleanly into the two halves of the pair it returns: the
**outputs** `(line, ready)`, and the **next state** `s'`. Read them
separately, because they are where the Mealy-versus-Moore question is
decided.

### The outputs are Moore

Both outputs are computed from `s` alone. `ready` is literally
`txState s == TxIdle` --- a comparison against the state, nothing else in
sight. `line` is a `case` on `txState s`:

```haskell
  line = case txState s of
    TxIdle -> high      -- idle line sits high
    TxStart -> low      -- start bit
    TxData _ -> lsb (txShift s)  -- current data bit, LSB-first
    TxStop -> high      -- stop bit
```

Idle and stop drive `high`, start drives `low`, and a data bit drives
`lsb (txShift s)` --- the least-significant bit of the latched, shifted
byte. Every arm reads only the state (and `txShift`, which *is* part of
the state). Neither `tick` nor `mbyte` --- the inputs --- appears
anywhere in `line` or `ready`.

That is the definition of a **Moore** output: a function of the current
state only.[^mealymoore] And it has a consequence you can see on a scope.
Because `line` ignores the input and depends only on `txState` and
`txShift`, and because neither of those changes except at a bit boundary,
the line is *rock-steady for the whole sixteen-tick bit* --- it cannot
twitch when a byte is offered, cannot glitch when the tick fires, cannot
do anything but sit at its level until the state moves. For a wire whose
entire job is to be sampled dead-center by a receiver sixteen ticks
later, that stability is not an accident; it is the reason to make the
outputs Moore.

So where is the Mealy?

### The next state, and the one place the input reaches in

The next-state block is a chain of guards --- boolean tests tried top to
bottom, first match wins:

```haskell
  s'
    | TxIdle <- txState s = case mbyte of
        Just b -> s{txState = TxStart, txShift = b, txCnt = 0}
        Nothing -> s
    | not tick = s
    | txCnt s /= maxBound = s{txCnt = txCnt s + 1}
    | otherwise = txAdvance s
```

The first guard is a **pattern guard**: `TxIdle <- txState s` succeeds
when the state matches `TxIdle`. So the whole first arm is "if we are
idle." And notice what it does --- and does *not* do. It looks at
`mbyte`, the input. If a byte is offered (`Just b`), it latches that byte
into `txShift`, resets the counter, and moves to `TxStart`, all *this
cycle*. If nothing is offered (`Nothing`), it stays idle. Crucially,
there is no mention of `tick` in this arm: **accepting a byte is not
tick-gated.** The instant a `Just b` arrives while idle, the machine
takes it, regardless of where the oversample tick happens to be in its
cadence.

That is the one place the live input reaches into the logic, and it is
the whole reason the interface is a *handshake*. `ready` is high (because
we are idle), the caller sees it and presents `Just b`, and the byte is
absorbed on that very cycle rather than waiting up to sixteen ticks for
the next tick edge. It is a genuine same-cycle dependence of the
transition on the input --- the ingredient that makes the general `mealy`
combinator the right tool rather than its Moore-only sibling.

Here is the subtlety worth being precise about, because it is easy to
overstate. A dependence of the *next state* on the input is **not** what
makes a machine Mealy --- a Moore machine's next-state function takes the
input too; that is how any state machine responds to anything. What
distinguishes Mealy from Moore is strictly whether the *output* depends
on the input. By that test, `txStep`'s outputs are Moore, full stop.
What the un-tick-gated handshake buys is not a Mealy *output* but a
*same-cycle transition* --- the machine changes state in response to the
input without waiting for its own cadence, so that the `ready` handshake
can complete in a single cycle. The module is, honestly stated, a
**Moore machine lifted through the `mealy` combinator**, with one
input-driven, un-clocked-cadence transition out of idle.[^whymealy]

<figure class="mvm-fig" style="margin:2rem 0">
<svg class="mvm" viewBox="0 0 760 300" role="img" aria-labelledby="mvm-t mvm-d" xmlns="http://www.w3.org/2000/svg">
<title id="mvm-t">Moore versus Mealy outputs</title>
<desc id="mvm-d">Two side-by-side machines. On the left, a Moore machine: a state register feeds output logic g, whose output depends on the state only; the input arrow enters the register (next state) and does not reach the output logic. On the right, a Mealy machine: the same layout, but an accented dashed wire taps the input up into the output logic as well, so the output depends on both state and input. The caption notes that txStep's outputs, line and ready, sit on the Moore side: no input reaches them.</desc>
<style>
.mvm{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.mvm .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.mvm .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.mvm .tap{stroke:var(--accent);stroke-width:2;fill:none;stroke-dasharray:5 4}
.mvm .div{stroke:var(--fg-dim);stroke-width:1.5;stroke-dasharray:4 5;fill:none}
.mvm text{font-family:var(--sans)}
.mvm .title{fill:var(--fg-main);font-size:16px;font-weight:600}
.mvm .name{fill:var(--fg-main);font-size:13px}
.mvm .form{fill:var(--fg-main);font-family:var(--mono);font-size:12.5px}
.mvm .formA{fill:var(--accent);font-family:var(--mono);font-size:12.5px}
.mvm .dim{fill:var(--fg-dim);font-size:11.5px}
.mvm .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.mvm .note{fill:var(--accent);font-size:12px}
.mvm .ah{fill:var(--fg-main)}
.mvm .aha{fill:var(--accent)}
</style>
<defs>
<marker id="mvm-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="mvm-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<line class="div" x1="382" y1="40" x2="382" y2="266"/>
<text class="title" x="190" y="52" text-anchor="middle">Moore</text>
<rect class="box" x="55" y="95" width="95" height="50" rx="6"/>
<rect class="box" x="215" y="95" width="112" height="50" rx="6"/>
<line class="wire" x1="150" y1="120" x2="213" y2="120" marker-end="url(#mvm-a)"/>
<line class="wire" x1="327" y1="120" x2="357" y2="120" marker-end="url(#mvm-a)"/>
<line class="wire" x1="102" y1="232" x2="102" y2="147" marker-end="url(#mvm-a)"/>
<text class="name" x="102" y="116" text-anchor="middle">state</text>
<text class="dim" x="102" y="133" text-anchor="middle">register</text>
<text class="name" x="271" y="114" text-anchor="middle">output g</text>
<text class="form" x="271" y="133" text-anchor="middle">g(state)</text>
<text class="sig" x="181" y="112" text-anchor="middle">state</text>
<text class="sig" x="348" y="112" text-anchor="middle">o</text>
<text class="sig" x="102" y="248" text-anchor="middle">input</text>
<text class="note" x="190" y="283" text-anchor="middle">txStep: line, ready live here</text>
<text class="title" x="575" y="52" text-anchor="middle">Mealy</text>
<rect class="box" x="440" y="95" width="95" height="50" rx="6"/>
<rect class="box" x="600" y="95" width="112" height="50" rx="6"/>
<line class="wire" x1="535" y1="120" x2="598" y2="120" marker-end="url(#mvm-a)"/>
<line class="wire" x1="712" y1="120" x2="742" y2="120" marker-end="url(#mvm-a)"/>
<line class="wire" x1="487" y1="232" x2="487" y2="147" marker-end="url(#mvm-a)"/>
<path class="tap" d="M487,205 H656 V147" marker-end="url(#mvm-aa)"/>
<circle class="aha" cx="487" cy="205" r="3.5"/>
<text class="name" x="487" y="116" text-anchor="middle">state</text>
<text class="dim" x="487" y="133" text-anchor="middle">register</text>
<text class="name" x="656" y="114" text-anchor="middle">output g</text>
<text class="formA" x="656" y="133" text-anchor="middle">g(state, in)</text>
<text class="sig" x="566" y="112" text-anchor="middle">state</text>
<text class="sig" x="733" y="112" text-anchor="middle">o</text>
<text class="sig" x="487" y="248" text-anchor="middle">input</text>
<text class="dim" x="575" y="283" text-anchor="middle">txStep has no input → output tap</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The sole distinction between the two machine styles. In a <strong>Moore</strong> machine the output is a function of the state alone; the input reaches only the next-state logic inside the register. In a <strong>Mealy</strong> machine an extra path (accent) taps the input into the output too, so the output can change the moment the input does. By this test <code>txStep</code>'s <code>line</code> and <code>ready</code> are Moore — neither reads <code>tick</code> or <code>mbyte</code> — even though the block is lifted with the general <code>mealy</code> combinator. What the handshake adds is a same-cycle <em>next-state</em> edge, not an input-driven output.</figcaption>
</figure>

### The cadence: freeze, count, advance

Once we are past the idle arm --- that is, once the machine is actually
transmitting --- the remaining three guards are the bit-timing, and every
one of them is gated on the tick:

```haskell
    | not tick = s                        -- between ticks: hold everything
    | txCnt s /= maxBound = s{txCnt = txCnt s + 1}  -- within a bit: count
    | otherwise = txAdvance s             -- 16th tick of the bit: next phase
```

`not tick = s` is the freeze: on any cycle where the oversample tick is
low --- which is most of them, since at 2 Mbaud the [baud
generator][baudgen] fires the tick only about once every 3.125 clocks, so
the machine sits frozen roughly two cycles in every three --- the state is
returned unchanged. The transmitter does nothing between ticks. This is
the enable in action: the baud generator hands out permission to move,
and off a tick the machine simply declines to.

On a tick, the next guard asks whether the bit is done. `txCnt` counts
`0..15`; while it has not yet reached `maxBound` (15) we increment it and
stay in the same phase --- another tick of the same bit. The guard order
is doing quiet safety work here: `txCnt s + 1` is bounded arithmetic on
an `Index 16`, and adding one to `maxBound` would be an overflow, but we
only reach the increment when `txCnt s /= maxBound`, so the `+ 1` can
never run off the end. The type forbids the illegal value and the guard
order guarantees we never ask for it --- belt and suspenders, both
supplied by the primer's "numbers can live in types."

When the tick fires and `txCnt` *has* reached 15 --- the sixteenth tick of
the bit --- the `otherwise` arm calls `txAdvance`, which moves the frame
to its next phase and resets the counter to begin a new bit. Sixteen
ticks per bit, exactly, counted off by an `Index 16` rolling from 0 to
its maximum. That is how "hold each level for sixteen oversample ticks"
is written.

Step back and the shape of the whole step is visible: two Moore outputs
read straight off the state, and a next-state function that is idle-driven
by the input (the handshake) and otherwise tick-driven by the counter
(the cadence). One function, two clean halves.

<figure class="dp-fig" style="margin:2rem 0">
<svg class="dp" viewBox="0 0 760 300" role="img" aria-labelledby="dp-t dp-d" xmlns="http://www.w3.org/2000/svg">
<title id="dp-t">The internal datapath of one txStep</title>
<desc id="dp-d">A register holding the state s fans out to three consumers. The top consumer is a comparator txState s equals TxIdle producing the ready output. The middle consumer is a case on txState s producing the line output. Both read the state only and are Moore outputs. The bottom consumer is the next-state logic, which additionally takes the tick input (gating the cadence: freeze, count, advance) and the mbyte input (accent, driving the idle handshake); it produces the next state s prime, which feeds back to the register.</desc>
<style>
.dp{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.dp .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.dp .obox{fill:var(--bg-main);stroke:var(--fg-main);stroke-width:2}
.dp .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.dp .fb{stroke:var(--accent);stroke-width:2;fill:none}
.dp .in{stroke:var(--accent);stroke-width:2;fill:none}
.dp text{font-family:var(--sans)}
.dp .name{fill:var(--fg-main);font-size:12.5px}
.dp .form{fill:var(--fg-main);font-family:var(--mono);font-size:12.5px}
.dp .dim{fill:var(--fg-dim);font-size:11px}
.dp .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.dp .sigA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.dp .ah{fill:var(--fg-main)}
.dp .aha{fill:var(--accent)}
.dp .node{fill:var(--fg-main)}
</style>
<defs>
<marker id="dp-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="dp-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<rect class="box" x="40" y="123" width="105" height="54" rx="6"/>
<rect class="obox" x="290" y="52" width="205" height="42" rx="4"/>
<rect class="obox" x="290" y="129" width="205" height="42" rx="4"/>
<rect class="box" x="290" y="203" width="205" height="62" rx="6"/>
<circle class="node" cx="175" cy="150" r="3.5"/>
<line class="wire" x1="145" y1="150" x2="175" y2="150"/>
<path class="wire" d="M175,150 V73 H288" marker-end="url(#dp-a)"/>
<line class="wire" x1="175" y1="150" x2="288" y2="150" marker-end="url(#dp-a)"/>
<path class="wire" d="M175,150 V214 H288" marker-end="url(#dp-a)"/>
<line class="wire" x1="495" y1="73" x2="700" y2="73" marker-end="url(#dp-a)"/>
<line class="wire" x1="495" y1="150" x2="700" y2="150" marker-end="url(#dp-a)"/>
<line class="wire" x1="72" y1="230" x2="288" y2="230" marker-end="url(#dp-a)"/>
<line class="in" x1="72" y1="248" x2="288" y2="248" marker-end="url(#dp-aa)"/>
<path class="fb" d="M495,234 H520 V286 H28 V150 H38" marker-end="url(#dp-aa)"/>
<text class="form" x="92" y="147" text-anchor="middle">s : TxS</text>
<text class="dim" x="92" y="163" text-anchor="middle">register</text>
<text class="form" x="392" y="77" text-anchor="middle">txState s == TxIdle</text>
<text class="form" x="392" y="154" text-anchor="middle">case txState s of …</text>
<text class="name" x="392" y="226" text-anchor="middle">next-state logic</text>
<text class="dim" x="392" y="245" text-anchor="middle">handshake · count · advance</text>
<text class="sig" x="162" y="143" text-anchor="end">s</text>
<text class="sig" x="706" y="77" text-anchor="start">ready</text>
<text class="sig" x="706" y="154" text-anchor="start">line</text>
<text class="sig" x="66" y="233" text-anchor="end">tick</text>
<text class="sigA" x="66" y="251" text-anchor="end">mbyte</text>
<text class="sigA" x="505" y="228" text-anchor="start">s′</text>
<text class="dim" x="610" y="112" text-anchor="middle">state-only (Moore)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">Inside one <code>txStep</code>: the state register <code>s</code> fans out to three consumers. Two are the Moore outputs — <code>ready</code>, a bare comparison <code>txState s == TxIdle</code>, and <code>line</code>, a <code>case</code> on the state — and neither reads an input, so both are steady functions of <code>s</code>. The third is the next-state logic, the <em>only</em> place the inputs enter: <code>tick</code> gates the cadence (freeze off-tick, count to sixteen, then advance) and <code>mbyte</code> (accent) drives the un-gated idle handshake. Its result <code>s′</code> is the value the register latches for the next cycle.</figcaption>
</figure>

## `txAdvance`: walking the frame

`txAdvance` is called on exactly the ticks that end a bit, and its job is
to move the phase forward:

```haskell
txAdvance :: TxS -> TxS
txAdvance s = case txState s of
  TxStart -> s{txState = TxData 0, txCnt = 0}
  TxData i
    | i == maxBound -> s{txState = TxStop, txCnt = 0}
    | otherwise -> s{txState = TxData (i + 1), txShift = txShift s `shiftR` 1, txCnt = 0}
  TxStop -> s{txState = TxIdle, txCnt = 0}
  TxIdle -> s
```

Trace the phases in order and it is the whole 8N1 frame:

- **`TxStart -> TxData 0`.** The start bit is finished; begin the first
  data bit. Note what is *not* touched: `txShift` is left exactly as it
  was latched, so the first data bit driven is `lsb` of the original
  byte --- bit 0, the least-significant bit. UART is **LSB-first**, and
  this is where that convention is set.
- **`TxData i`, when `i` is not `maxBound`.** Move to `TxData (i + 1)`
  *and* shift the latched byte right by one:
  ``txShift = txShift s `shiftR` 1``. The right shift slides the next bit
  down into the `lsb` position, so that when `line` reads
  `lsb (txShift s)` during the next data phase, it presents bit `i + 1`.
  The byte walks out of the bottom of the shift register, one bit per
  data phase, least-significant first.
- **`TxData i`, when `i == maxBound`.** Bit 7 is done; there is no bit 8
  (the `Index 8` guaranteed that), so go to `TxStop`.
- **`TxStop -> TxIdle`.** The stop bit is finished; return to idle,
  where `ready` goes high again and the next byte can be accepted.

Every arm also resets `txCnt` to 0, because each new phase gets its own
fresh count of sixteen ticks.

Two details reward a second look. The first is that the data bits are
serialised with the *same* `txShift` register the outputs read --- there
is no separate bit index driving a multiplexer over the byte. `line`
reads the bottom bit; `txAdvance` shifts a new bottom bit into place at
each boundary; between them the byte is dealt out like cards off the
bottom of a deck. It is the CRC post's shift register seen from the other
direction: there the byte was shifted *left*, MSB-first, feeding a
polynomial division; here it is shifted *right*, LSB-first, feeding a
wire. Same primitive, opposite convention, because eSPI's CRC is defined
MSB-first and a UART frame is defined LSB-first --- two standards, two
directions, one `shiftR`/`shiftL` apart.[^lsbfirst]

The second is that final arm, `TxIdle -> s`. It can never actually run:
`txAdvance` is only ever reached from `txStep`'s `otherwise` guard, and
that guard is only reached *after* the idle case has already been handled
by the first pattern guard. If the state is `TxIdle`, control never gets
as far as `txAdvance`. So why write the arm at all? Because a `case` over
`TxState` that omits `TxIdle` is non-exhaustive, and the compiler will
say so --- the primer's exhaustiveness check again, insisting that every
constructor be handled even when the programmer can prove one is
dead.[^exhaustive]
Returning `s` unchanged is the honest way to satisfy it: if we somehow
*were* idle here, doing nothing is the only safe answer. A total function
has no holes, even the unreachable ones.

<figure class="wf-fig" style="margin:2rem 0">
<svg class="wf" viewBox="0 0 760 200" role="img" aria-labelledby="wf-t wf-d" xmlns="http://www.w3.org/2000/svg">
<title id="wf-t">The 8N1 line waveform for one transmitted byte</title>
<desc id="wf-d">The serial line over time for the byte 0x4B. It idles high, drops low for one start bit, then drives the eight data bits least-significant-bit first (1, 1, 0, 1, 0, 0, 1, 0), returns high for one stop bit, and idles high again. Each cell is held for sixteen oversample ticks. Accent dots mark the bit centers where a receiver samples.</desc>
<style>
.wf{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.wf .line{stroke:var(--fg-main);stroke-width:2.5;fill:none}
.wf .startseg{stroke:var(--accent);stroke-width:2.5;fill:none}
.wf .guide{stroke:var(--fg-dim);stroke-width:1;fill:none;stroke-dasharray:3 4}
.wf .brace{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.wf .lsb{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.wf .dot{fill:var(--accent)}
.wf text{font-family:var(--sans)}
.wf .lab{fill:var(--fg-main);font-size:13px}
.wf .dim{fill:var(--fg-dim);font-size:11px}
.wf .acc{fill:var(--accent);font-size:11px}
.wf .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
</style>
<defs>
<marker id="wf-d" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="var(--fg-dim)"/></marker>
</defs>
<text class="lab" x="382" y="30" text-anchor="middle">byte = 0x4B  ('K')</text>
<line class="guide" x1="122" y1="58" x2="122" y2="142"/>
<line class="guide" x1="174" y1="58" x2="174" y2="142"/>
<line class="guide" x1="590" y1="58" x2="590" y2="142"/>
<line class="guide" x1="642" y1="58" x2="642" y2="142"/>
<polyline class="line" points="70,70 122,70 122,130 174,130 174,70 278,70 278,130 330,130 330,70 382,70 382,130 486,130 486,70 538,70 538,130 590,130 590,70 694,70"/>
<polyline class="startseg" points="122,70 122,130 174,130 174,70"/>
<circle class="dot" cx="148" cy="130" r="2.5"/>
<circle class="dot" cx="200" cy="70" r="2.5"/>
<circle class="dot" cx="252" cy="70" r="2.5"/>
<circle class="dot" cx="304" cy="130" r="2.5"/>
<circle class="dot" cx="356" cy="70" r="2.5"/>
<circle class="dot" cx="408" cy="130" r="2.5"/>
<circle class="dot" cx="460" cy="130" r="2.5"/>
<circle class="dot" cx="512" cy="70" r="2.5"/>
<circle class="dot" cx="564" cy="130" r="2.5"/>
<circle class="dot" cx="616" cy="70" r="2.5"/>
<text class="sig" x="60" y="74" text-anchor="end">1</text>
<text class="sig" x="60" y="134" text-anchor="end">0</text>
<text class="dim" x="96" y="156" text-anchor="middle">idle</text>
<text class="acc" x="148" y="156" text-anchor="middle">start</text>
<text class="lab" x="200" y="156" text-anchor="middle" style="font-size:11px">b0</text>
<text class="lab" x="252" y="156" text-anchor="middle" style="font-size:11px">b1</text>
<text class="lab" x="304" y="156" text-anchor="middle" style="font-size:11px">b2</text>
<text class="lab" x="356" y="156" text-anchor="middle" style="font-size:11px">b3</text>
<text class="lab" x="408" y="156" text-anchor="middle" style="font-size:11px">b4</text>
<text class="lab" x="460" y="156" text-anchor="middle" style="font-size:11px">b5</text>
<text class="lab" x="512" y="156" text-anchor="middle" style="font-size:11px">b6</text>
<text class="lab" x="564" y="156" text-anchor="middle" style="font-size:11px">b7</text>
<text class="acc" x="616" y="156" text-anchor="middle">stop</text>
<text class="dim" x="668" y="156" text-anchor="middle">idle</text>
<path class="brace" d="M122,167 V173 H174 V167"/>
<text class="dim" x="148" y="187" text-anchor="middle">16 ticks</text>
<line class="lsb" x1="180" y1="172" x2="584" y2="172" marker-end="url(#wf-d)"/>
<text class="dim" x="382" y="187" text-anchor="middle">data bits, LSB-first</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The <code>line</code> waveform for one byte — ASCII <code>'K'</code>, <code>0x4B</code> = <code>0b0100_1011</code>. The frame is idle-high, one accent <strong>start</strong> bit (low), the eight data bits <strong>least-significant first</strong> (so <code>0b0100_1011</code> leaves as <code>1 1 0 1 0 0 1 0</code>), one <strong>stop</strong> bit (high), then idle. Each cell is held for sixteen oversample ticks. The accent dots mark the bit centers, where the receiver samples: because <code>line</code> is a Moore output it is dead-steady across the whole cell, so the sample always lands on solid ground. Note that the stop bit and the idle line sit at the same level — a frame is delimited by <em>counting</em>, not by a distinct symbol.</figcaption>
</figure>

The waveform above is the entire module, seen from outside: idle high,
one low start bit, eight data bits with the byte's least-significant bit
leading, one high stop bit, and back to idle. Ten frame cells, each held
for sixteen ticks, walked out by the ten states from `TxStart` through
`TxStop` --- with `TxIdle` the resting level either side, held not for
sixteen ticks but for however long it takes the next byte to arrive. If
you overlaid the receiver's sampling instants --- one at the center of
each frame cell, sixteen ticks being enough oversampling to land there
reliably --- every sample would fall squarely in the middle of a
rock-steady level. That is the Moore output paying off: nothing moves at
the sampling instant except by design.

## The one-cycle handshake

We have seen both halves of the handshake now; it is worth assembling
them into the protocol the caller actually follows, because it is the
part most likely to bite an integrator who gets it wrong.

`ready` is high **only** when `txState s == TxIdle`. The moment a byte is
accepted --- the idle arm firing on a `Just b` --- the state becomes
`TxStart`, so on the very next cycle `ready` is already low, and it stays
low through start, all eight data bits, and stop: some five hundred
`Dom100` cycles at 2 Mbaud. Only when `txAdvance` finally returns the
machine to `TxIdle` does `ready` rise again. So the contract is:

> Present `Just b` on a cycle when `ready` is high, and the byte is
> latched that cycle. Present it any other time and it is silently
> dropped.

That "silently dropped" is not sloppiness; it is enforced by the shape of
the guards. Once the machine is out of idle, *no* arm of `s'` so much as
looks at `mbyte` --- the `not tick`, counting, and `txAdvance` arms are
functions of `tick` and the state alone. A `Just b` offered mid-frame
falls through every guard and changes nothing. The byte is not queued,
not latched, not remembered; it simply has no effect, because there is no
wire by which it could have one. That is a deliberate safety property: a
caller that violates the handshake cannot *corrupt* a frame in flight,
only fail to send. The [design][baudgen] calls this out as its own line
--- "a `Just` presented mid-transmission is ignored (no corruption)" ---
and the guard structure is what makes it true by construction rather than
by vigilance.

On the drain side of the real UART, this is exactly the discipline the
trace-drain FSM will follow: present the next ring byte on `mbyte` only
while `ready` is high, and let `ready` falling be the backpressure that
paces the whole readout. One flag, one rule, and the transmitter can
never be overrun.

## The tests

The transmitter shares a test module with the rest of the UART,
`Test/Uart.hs`, and because it is sequential the tests drive it through
Clash simulation --- `sampleN`, `bundle`, `fromList` --- rather than the
pure-function style the CRC used. The [primer]'s reading of `Signal` as
"an endless stream, one sample per cycle" is what makes that tractable:
`sampleN n` is `take n` on the stream, so "simulate four hundred cycles"
is a list traversal, not a simulator invocation.

Three of the properties are the transmitter's own. The first is the one
that matters most, and it is the [baud generator][baudgen] post's
promised keystone finally starting to close:

```haskell
testProperty "TX->RX fast loopback recovers the byte" $ property $ do
  b <- forAll genByte
  let out = runFastLoop [Nothing, Just b] 400
  [b' | Just b' <- out] === [b]
```

`runFastLoop` wires the transmitter's `line` straight into the receiver's
input, shares one tick between them, and --- for speed --- runs with the
tick held permanently `True`, so each bit is sixteen cycles rather than
the sixteen *ticks* the real NCO spreads over fifty. [Hedgehog] draws a
random byte, feeds it to the transmitter as a single `Just b` while
`ready` is high, and asserts that the receiver hands back that exact
byte, bit-for-bit. No reference model, no golden waveform: the two halves
of the UART check each other. Any disagreement about bit order, frame
shape, or timing --- an MSB-first slip, an off-by-one on the stop bit ---
shows up as a byte that comes back wrong, and Hedgehog shrinks the
failing case to the smallest byte that still breaks it.

The second pins the handshake:

```haskell
testProperty "TX ignores input while busy (ready gating)" $ property $ do
  b <- forAll genByte
  let out = runFastLoop (Nothing : L.replicate 100 (Just b)) 400
  [b' | Just b' <- out] === [b]
```

Here the caller *misbehaves on purpose*: after one idle cycle it jams
`Just b` onto the input a hundred cycles in a row, straight through the
transmission. If mid-frame requests were latched, the line would be a
mess of restarted frames and the receiver would recover garbage or a
fistful of bytes. The assertion is that exactly **one** byte comes back
--- the machine accepted the first `Just b` while idle and ignored the
other ninety-nine while busy, precisely as the guards promise. This is
the "no corruption" property, stated as a test.

The third runs the same loopback through the *real* baud generator rather
than the always-true tick:

```haskell
testProperty "full UART loopback (real NCO) recovers the byte" $ property $ do
  b <- forAll genByte
  let out = runFullLoop (Nothing : L.replicate 60 (Just b)) 1200
  [b' | Just b' <- out] === [b]
```

`runFullLoop` calls the umbrella `uart (SNat @2_000_000)` --- baud
generator, transmitter, and receiver all wired together --- with the
transmitter's line looped back to the receiver's. Now a bit really is
fifty clocks wide, tiled by sixteen NCO ticks whose gaps alternate three
and four clocks, and the receiver must still sample each bit dead-center.
That it recovers the byte is the whole UART's keystone: the fractional
3.125 the [baud generator][baudgen] fought to average correctly, the
sixteen-tick bits the transmitter holds, and the center-sampling the
receiver performs, all have to agree at once or the byte comes back
wrong. The baud-generator post said we would meet the other end of its
one blunt tick-rate test "when the receiver is built"; this is that other
end, and the transmitter is the piece in the middle that turns a cadence
into a frame.

## What we read

Eighty-odd lines, one exported name, and underneath it the first real
state machine in the series: two `data` types that are its entire memory
--- a sum for the frame phase, `TxData (Index 8)` folding eight data-bit
states into one bounded constructor, and a record for the byte and the
tick counter carried alongside it --- lifted into hardware by a `mealy`
at the very top of the module, its state held in the register the [baud
generator][baudgen] showed us bare. The brain beside it, `txStep`, is a
pure `s -> i -> (s, o)` you can test in milliseconds, and reading it
closely settled the question the module's own comment raised: the outputs
`line` and `ready` are **Moore** --- functions of the state alone, so the
serial line never twitches between bit boundaries --- while the single
input-driven, un-tick-gated transition out of idle is what makes the
`ready` handshake resolve in one cycle. `txAdvance` walks the phases
start-to-stop and deals the latched byte out of a right-shifting register
LSB-first, the CRC's shift register run backwards. And a byte-exact
loopback checks the whole thing against its own other half.

The [primer] promised that spotting a `mealy` at the top of a module
would one day mean something concrete rather than looking like an
incantation. This is the module where it pays off, and the silhouette we
read here --- find the `mealy`, find the step function, split it into
outputs and next state, decide Moore or Mealy by whether the *output*
looks at the input --- is the one every remaining block wears. Next is
the receiver: the transmitter's mirror, where the line is an *input*
arriving asynchronously from a pin, where a start edge has to be *found*
rather than driven, and where the majority-vote center sampling that the
loopback test quietly depends on gets built in the open. Between the two
of them the UART closes, and the heartbeat the baud generator started
finally carries a byte end to end.

[^backpressure]: The alternative is full **valid/ready backpressure**,
where producer and consumer each raise a flag and a byte moves only on a
cycle when both are high --- the discipline you reach for when either side
might stall indefinitely. A UART transmitter at 2 Mbaud inside a 100 MHz
fabric is not that situation: once a byte is latched the line takes
roughly five hundred cycles to clock it out, so any consumer of `ready`
has enormous slack and a one-way `ready` flag suffices. The [UART
design][baudgen] records this as an explicit decision --- "at 2 Mbaud a
byte spans ~500 `Dom100` cycles, so the consumer always keeps up --- full
valid/ready backpressure is overkill" --- and it is a good example of
sizing the mechanism to the actual timing rather than reaching for the
most general handshake reflexively.

[^index]: `Index n` is the primer's counting-in-the-type feature doing
double duty. As a *state*, `TxData (Index 8)` has exactly eight
inhabitants, so the compiler's tally of `TxState`'s constructors is
exact and its exhaustiveness warnings are trustworthy. As a *value*,
`txCnt :: Index 16` and the `i` inside `TxData` can only hold legal
positions --- `0..15` and `0..7` respectively --- so the arithmetic
`txCnt s + 1` and `TxData (i + 1)` cannot silently produce an
out-of-range count the way an `Int` would; an overflow is a runtime
error in simulation and a wrapped value the type is designed to prevent
you from reaching, which is why both increments sit behind a
`/= maxBound` guard. The width in the type is not documentation; it is
the thing that makes "sixteen ticks per bit" and "eight data bits" facts
the compiler helps enforce rather than comments it cannot check.

[^mealymoore]: The two machine styles are named for the engineers who
formalised them a year apart. A **Moore machine** (Edward F. Moore,
*Gedanken-experiments on Sequential Machines*, 1956) has outputs that are
a function of the current state *only*: `output = g(state)`. A **Mealy
machine** (George H. Mealy, *A Method for Synthesizing Sequential
Circuits*, 1955) has outputs that are a function of the current state
*and* the current input: `output = g(state, input)`. Both have a
next-state function `f(state, input)` --- taking the input is how either
kind reacts to anything --- so the *sole* distinction is whether the
input can reach the output within the same cycle. The practical trade is
latency versus stability: a Mealy output can respond in the cycle its
input arrives (fewer states, quicker reaction) but inherits the input's
glitches and combinational timing; a Moore output lags by up to a state
transition but is stable and glitch-free for as long as the state holds.
The two are equivalent in power --- any Mealy machine has a Moore
equivalent, generally with more states, and vice versa --- so the choice
is engineering, not expressiveness.

[^whymealy]: Which raises a fair question: if the outputs are Moore, why
lift with `mealy` and not Clash's `moore`, whose type
`moore :: (s -> i -> s) -> (s -> o) -> s -> Signal dom i -> Signal dom o`
splits the next-state function from a separate, state-only output
function --- a perfect fit for a Moore machine? You could; the module
would be equivalent. `mealy` wins on locality: `txStep` computes the next
state and both outputs in a single `where` block that shares `txState s`,
`txShift s`, and a couple of `case`s, and returning them together as
`(s', (line, ready))` keeps that shared reading of the state in one
place. Splitting into a `moore`-shaped pair would duplicate the "look at
`txState s`" logic across two functions for the sake of a label. `mealy`
is the more general combinator, it costs nothing to use where a Moore
machine is what you have, and it keeps the brain in one piece --- so the
codebase uses it uniformly, here and in the engine, and lets the *shape*
of `txStep` (whether its outputs happen to read the input) decide what
kind of machine each block really is.

[^bundle]: `bundle` and `unbundle` come from Clash's `Bundle` class,
which witnesses the isomorphism `Signal dom (a, b) ≅ (Signal dom a,
Signal dom b)` --- and more generally between a `Signal` of some product
(a tuple, a `Vec`, a record) and the product of `Signal`s. The intuition
is pure hardware: a wire carrying a pair *is* two wires, and whether you
think of "one bus of pairs over time" or "two wires over time" is a
point of view, not a difference in the circuit. `mealy` is written to
take a single input `Signal` and produce a single output `Signal`, so
when your interface is several signals --- as almost every real block's is
--- `bundle` gathers them going in and `unbundle` scatters them coming
out. They synthesise to nothing at all; they are the type-level
bookkeeping that lets a one-in/one-out combinator serve a many-in/many-out
world.

[^lsbfirst]: The bit-ordering split between the two blocks is not
arbitrary; each follows its own standard. A UART frame is transmitted
**least-significant-bit first** --- a convention inherited from the
teletype and enshrined in every 8N1 implementation --- so the transmitter
reads `lsb` and shifts *right*, dealing bit 0, then 1, up to 7. The
[CRC][crc] the eSPI link uses is defined **most-significant-bit first**
(as eSPI and SMBus require), so that module reads `msb` and shifts
*left*. The pleasing thing is how little the code has to say about it:
the entire difference between "LSB-first serialiser" and "MSB-first CRC"
is the direction of a shift and which end you read, `shiftR`/`lsb` versus
`shiftL`/`msb`. Two wire conventions from two unrelated standards, a
single mirror-image apart in the source.

[^exhaustive]: "The compiler will say so" is worth qualifying, because how
*loudly* it says so is a knob. Left alone, GHC is silent about a
non-exhaustive `case`; the diagnostic is `-Wincomplete-patterns`, and
Tamal gets it only because its cabal turns on `-Wall` --- one of the
`ghc-options` the [introduction post][intro] flagged as load-bearing. Even
then it is by default just a **warning**: the module compiles, and in
ordinary Haskell a branch that "cannot happen" but does raises a
`PatternMatchFail` exception at *run* time rather than being caught at
compile time. So the check is real but toothless unless you escalate it,
which you can: `-Werror=incomplete-patterns` (or the pair
`-Wincomplete-patterns -Werror`), set per-module with an
`{-# OPTIONS_GHC #-}` pragma or project-wide in `ghc-options`, turns a
missing constructor into a hard build failure --- I confirmed as much on
the exact GHC this project uses, where the warning becomes
`error: [-Wincomplete-patterns, Werror=incomplete-patterns]` and the
compile stops. That is the instructive contrast with Rust, whose
recollection you may share: Rust makes `match` exhaustiveness a
*type-checker requirement*, so a non-exhaustive match is a compile error
(`E0004`) out of the box, with `_` the one explicit escape hatch. Haskell
went the other way because it has always sanctioned partial functions ---
`head`, `fromJust`, and their kin are non-exhaustive *by design* --- so it
treats totality as an opt-in diagnostic layered on top of the language
rather than a law baked into it. For gateware the strict setting is the
easy call: silicon has no exceptions to throw, so a "dead" arm that turned
out to be live would be a silent wrong answer --- a don't-care lowered into
the netlist --- not a catchable `PatternMatchFail`, which is a good argument
for compiling hardware with `-Werror=incomplete-patterns`. Writing the
`TxIdle -> s` arm keeps even the warning quiet, which is why it is there.
