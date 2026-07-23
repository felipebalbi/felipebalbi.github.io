+++
title = "Tamal: Wiring Uart Through Composition"
date = 2026-07-24T09:00:00
draft = true
description = "Reading Tamal's UART top end to end: the five-line umbrella that is the first block with no behaviour of its own, where the baud number comes home to the type because the top owns the generator, where the interface is precisely the receiver's ports and the transmitter's ports set side by side with the oversample tick gone internal, where the body is not a computation but a wiring diagram written as three where-bound equations Clash elaborates structurally into a netlist, where one heartbeat is generated once and fanned into both machines so a single shared time base makes the loopback land dead-center, and where the four wires that leave the block are the seam at which the self-contained UART finally joins the loader and the engine."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "uart", "composition"]
+++

The [receiver][rx] we read yesterday closed the keystone. It wired the
transmitter's line into its own, shared one tick between them, fed a
random byte in one end and got the same byte, bit-for-bit, out the other
--- first with an always-true tick, then with the real fractional-3.125
heartbeat the [baud generator][baudgen] fought to keep honest. By the
last paragraph the UART *worked*, end to end, and I told you it was
behind us.

It was --- as behaviour. But there is a small dishonesty in having
watched the whole thing run without ever reading the file that makes it
a *single thing*. Those loopback tests called `uartTx` and `uartRx` and
shared a `tick` by hand, or they called `uart` --- and that `uart`, the
five-line module this post is about, we leaned on and never once read.
This is the shortest post in the series, because its subject is the
shortest module in the UART: the top, the umbrella, the seam. And it
earns its own post for exactly one reason --- it is the first Tamal
module with **no behaviour of its own.** The [CRC][crc] computed a
residue; the [baud generator][baudgen] counted a phase; the
[transmitter][tx] drove a wire; the [receiver][rx] recovered one.
`Tamal.Uart` does none of these. It *connects* the ones that do. Its
whole content is composition, and composition --- how three blocks that
each own one idea become one block that owns none --- is the subject.

Every post so far ended by pointing *forward*, at the next block. This
one points *inward*, at the wiring.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[tx]: https://balbi.sh/posts/tamal-uart-tx/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the license header and the doc-comment --- and there is nothing
else to minus, no helper to hide --- here is `src/Tamal/Uart.hs` in full:

```haskell
module Tamal.Uart
  ( uart
  ) where

import Clash.Prelude

import Tamal.Uart.BaudGen (oversampleTick)
import Tamal.Uart.Rx (uartRx)
import Tamal.Uart.Tx (uartTx)

uart ::
  forall baud dom.
  (HiddenClockResetEnable dom, KnownDomain dom, KnownNat baud) =>
  SNat baud ->
  Signal dom Bit ->
  Signal dom (Maybe (BitVector 8)) ->
  ( Signal dom (Maybe (BitVector 8))
  , Signal dom Bool
  , Signal dom Bit
  , Signal dom Bool
  )
uart baud rxLine txByte = (rxByte, rxErr, txLine, txReady)
 where
  tick = oversampleTick baud
  (rxByte, rxErr) = uartRx tick rxLine
  (txLine, txReady) = uartTx tick txByte
```

That is the whole module. A header with something new in it, a type
signature longer than the code it types, and a body of four lines ---
one to name the tick, two to place the machines, and a return that
bundles their outputs. No `data` declarations, because the top remembers
nothing. No `mealy`, no `register`, because it clocks nothing. No
`where`-hidden `step`, because it decides nothing. For the first time in
the series the entire module fits in a paragraph, and every line is
either a type or a wire.

## Three imports and one door

The opening beat is the [CRC][crc] module's, played a fifth time, and I
will be as quick as the fifth playing deserves:

```haskell
module Tamal.Uart
  ( uart
  ) where

import Clash.Prelude
```

`module Tamal.Uart` names the module after its path --- `src/Tamal/Uart.hs`,
the one *without* a third dot, the parent directory of
`Tamal.Uart.BaudGen`, `Tamal.Uart.Rx`, and `Tamal.Uart.Tx`. Only `uart`
leaves through the one door in the wall, and this time there is genuinely
nothing else behind it: no private `step`, no helper, no state type. The
export list has never been so nearly the whole file. `import
Clash.Prelude` is the same prelude swap every earlier post dwelt on, the
line that trades ordinary Haskell's furniture for `Signal`, `Bit`, and
the rest of the vocabulary that lowers to gates; I will not re-derive it
a fifth time either.

What *is* new sits just underneath it --- three imports, the first the
series has had cause to read:

```haskell
import Tamal.Uart.BaudGen (oversampleTick)
import Tamal.Uart.Rx (uartRx)
import Tamal.Uart.Tx (uartTx)
```

Each pulls exactly one name across from a child module --- the one name
that module's own export list let out. `oversampleTick` from the [baud
generator][baudgen], `uartRx` from the [receiver][rx], `uartTx` from the
[transmitter][tx]. These three imports are the top's *material*: it has
no primitives of its own to work with, only the three finished blocks the
last three posts built, each sealed to a single public name and each
imported by that name. And the parentheses on each import are the mirror
of the parentheses on each `module` line we read one at a time. The
export list said "only this leaves"; the import says "only this enters."
Set the four modules side by side and the doors line up exactly:
`BaudGen` lets out `oversampleTick` and the top lets it in; `Rx` lets out
`uartRx` and the top lets it in; `Tx` lets out `uartTx` and the top lets
it in. The top is the room those three doors open into.

## The type: the numbers come home

The signature is longer than the body, and every part of it is a
reunion:

```haskell
uart ::
  forall baud dom.
  (HiddenClockResetEnable dom, KnownDomain dom, KnownNat baud) =>
  SNat baud ->
  Signal dom Bit ->
  Signal dom (Maybe (BitVector 8)) ->
  ( Signal dom (Maybe (BitVector 8))
  , Signal dom Bool
  , Signal dom Bit
  , Signal dom Bool
  )
```

Under the [primer]'s reading a signature is half the
documentation[^repetitive], and this one is visibly *assembled* out of
the three signatures we already know. Begin with what the
[transmitter][tx] and [receiver][rx] made a point of *not*
having. Both of them, you will remember, shed every number from their
types: one lone `HiddenClockResetEnable dom` constraint, no `forall`,
no `SNat`, no `KnownNat`, because neither block names a baud rate ---
a bit is sixteen ticks and the ticks arrive from elsewhere. The [baud
generator][baudgen] was the opposite: it carried the whole numeric
burden --- `forall baud dom`, the `KnownDomain`/`KnownNat` pile, and
the `SNat baud` argument --- because it was the one block that turns a
baud rate into a tick.

Now look at the top's constraints and first argument: `forall baud dom`,
`(HiddenClockResetEnable dom, KnownDomain dom, KnownNat baud)`, `SNat
baud ->`. They are the baud generator's, character for character. **The
numbers come home.** The top wears the [baud generator's][baudgen] exact
type obligations because it *owns* the baud generator: it is the block
that will call `oversampleTick baud`, so it must be handed the `SNat
baud` to pass along, and must carry the `KnownNat baud` and `KnownDomain
dom` that `oversampleTick` needs to read the clock frequency out of the
domain. The transmitter and receiver got to be numberless precisely
because the top volunteered to hold the number for all three. Factoring
the timing into one small block did not delete the `SNat`; it
*concentrated* it --- out of TX and RX, into BaudGen, and back out to
whoever owns BaudGen, which is here.

Then the ports, and they are just as plainly a concatenation. Two
arguments go in:

- `Signal dom Bit` --- the RX line, the [receiver's][rx] one input, the
  asynchronous wire from a pin.
- `Signal dom (Maybe (BitVector 8))` --- the TX byte, the
  [transmitter's][tx] one input, the `Just b` send request.

And the four-tuple comes out as `(rxByte, rxErr, txLine, txReady)` ---
the receiver's two outputs followed by the transmitter's two:

- `Signal dom (Maybe (BitVector 8))` and `Signal dom Bool` --- `uartRx`'s
  byte strobe and framing-error strobe, unchanged.
- `Signal dom Bit` and `Signal dom Bool` --- `uartTx`'s line and its
  `ready` flag, unchanged.

So the top's interface is exactly `uartRx`'s external ports set beside
`uartTx`'s external ports, with the baud generator's type obligations
wrapped around the pair. And notice the one thing that is *absent*. The
tick --- `Signal dom Bool`, the [baud generator's][baudgen] output and
the first argument of *both* `uartRx` and `uartTx` --- appears nowhere in
`uart`'s type. It was an input to each machine when we read them alone;
here it has gone **internal.** That is the signature telling you, before
you read a line of the body, what composition did: it took the wire that
ran *between* the blocks and tucked it inside, leaving only the wires
that still face the world. The heartbeat is now a private matter.

## Four lines, no state

Here is the body, the only part of the file that is neither ceremony nor
type:

```haskell
uart baud rxLine txByte = (rxByte, rxErr, txLine, txReady)
 where
  tick = oversampleTick baud
  (rxByte, rxErr) = uartRx tick rxLine
  (txLine, txReady) = uartTx tick txByte
```

Read it as what it is: a wiring diagram written as equations. The head
binds the three inputs --- `baud`, `rxLine`, `txByte` --- and returns the
four-tuple. The `where` block defines the internal names the tuple is
built from. And every one of those definitions is an *instantiation*, not
a computation:

- `tick = oversampleTick baud` --- place one baud generator, hand it the
  baud, call its output `tick`.
- `(rxByte, rxErr) = uartRx tick rxLine` --- place one receiver, feed it
  the tick and the line, name its two outputs.
- `(txLine, txReady) = uartTx tick txByte` --- place one transmitter,
  feed it the tick and the byte, name its two outputs.

There is no arithmetic, no `case`, no state threaded from cycle to cycle.
Every earlier module's `where` block hid *work* --- the baud generator's
phase accumulator feeding back through a `register`, the transmitter's
`initTx` and the `mealy` lift over `txStep`, the receiver's synchronizer
and its `mealy` over `rxStep`. This `where` block hides nothing but
*names for wires*. `tick` is not a value that gets computed and returned;
it is a net, and `oversampleTick baud` is the sub-circuit driving it.
`rxByte` is not a byte; it is the wire on which the receiver will present
its strobes.

The tell is the ordering --- or the absence of it. In an imperative
reading you would object that `tick` is *used* on the second and third
lines but *defined* on the first, so the order is load-bearing: define
before use. In Haskell's `where`, and in the hardware it denotes, the
order does not matter at all. I could write the three bindings in any
sequence and the module would be identical, because they are not steps
executed in time but nets that all exist at once. `tick` drives `uartRx`
and `uartTx` the way a wire drives the pins soldered to it ---
simultaneously, continuously, with no notion of "first." The `where`
block is a net-list, and a net-list is a set, not a sequence.[^netlist]

This is what it looks like when a module's job is purely structural. It
adds no gate that computes anything and no flip-flop that remembers
anything; it adds only *connections*. Put the four modules on a bench and
this file is the wiring loom between them --- and, like a loom, it is
invisible in the behaviour and total in the structure. Nothing in the
UART works without it, and it does nothing but let the rest work.

## One heartbeat, two machines

Four lines, and only one of them carries a design decision. The other
three are forced: a receiver needs the line, a transmitter needs the
byte, the outputs are what they are. But the first line, and the way the
name it binds gets used *twice*, is a genuine choice:

```haskell
  tick = oversampleTick baud
  (rxByte, rxErr) = uartRx tick rxLine
  (txLine, txReady) = uartTx tick txByte
```

`tick` is generated **once** and fanned into **both** machines. This is
decision 2 of the [UART design][baudgen] --- build RX and TX together,
sharing one oversample tick --- and it is the whole reason the two halves
live in one module instead of two.

Nothing in the types forced it. Each machine takes its own `Signal dom
Bool` tick, so I could have called `oversampleTick baud` twice, once
for the receiver and once for the transmitter, and every signature
would still typecheck. It would even *work* --- two identical NCOs in
the same domain, same increment, same modulus, are deterministic
functions of the one clock and would count in perfect lock-step, bit
for bit. But *identical* is the tell. You would be spending a second
phase accumulator, adder, and comparator to build an exact copy of a
signal you already have, and then leaning on the two staying exact ---
a property you now maintain in *two* places, where changing one
generator's width or modulus and forgetting the other silently splits
the one clock into two that disagree[^splitbaudgen]. And even kept in
step, the duplication states a falsehood about the design: that the
transmit clock and the receive clock are two things that happen to
agree, when the whole point is that they are **one thing.** Sharing
`tick` says the true thing in a single wire.

And because they *are* one thing, the loopback the [receiver][rx] closed
rests on something firmer than luck. In a real UART the two ends of the
link are different chips with genuinely independent clocks, and the
receiver's whole apparatus --- oversample sixteen times, sample at the
center, majority-vote --- exists precisely to *tolerate* the drift
between them. Inside Tamal there is no drift to tolerate, because there
are not two clocks: the transmitter holds each bit for sixteen ticks and
the receiver centers its 7/8/9 window in the *same* sixteen ticks, off
the *same* accumulator. The center-sampling margin the [receiver][rx]
spent a whole post earning is, in loopback, slack it never has to spend
--- the sample lands dead-center by construction, because one counter is
timing both ends. The [baud generator][baudgen] promised it was handing
"the rest of the UART" an enable rather than a clock; this line is where
"the rest of the UART" turns out to be precisely two consumers, wired in
parallel across the one enable.

<figure class="uarttop-fig" style="margin:2rem 0">
<svg class="uarttop" viewBox="0 0 760 344" role="img" aria-labelledby="uarttop-t uarttop-d" xmlns="http://www.w3.org/2000/svg">
<title id="uarttop-t">The uart top module: one shared tick into a receiver and a transmitter</title>
<desc id="uarttop-d">A dashed box labelled uart is the module boundary. Inside it sit three solid boxes: oversampleTick, the baud generator, at left centre; uartRx, the receiver, at upper right; and uartTx, the transmitter, at lower right. A compile-time baud parameter enters oversampleTick from the left as a dashed arrow. oversampleTick drives a single accent wire, the tick, which branches into both uartRx and uartTx — the shared heartbeat. Two runtime inputs cross the boundary from the left: rxLine into uartRx and txByte into uartTx. Four outputs cross the boundary to the right: rxByte and rxErr from uartRx, txLine and txReady from uartTx. The tick wire never crosses the boundary; it is internal to uart.</desc>
<style>
.uarttop{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.uarttop .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.uarttop .mbox{fill:none;stroke:var(--fg-dim);stroke-width:1.5;stroke-dasharray:6 5}
.uarttop .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.uarttop .tickw{stroke:var(--accent);stroke-width:2.5;fill:none}
.uarttop .parm{stroke:var(--fg-dim);stroke-width:1.5;fill:none;stroke-dasharray:5 4}
.uarttop .node{fill:var(--accent)}
.uarttop text{font-family:var(--sans)}
.uarttop .name{fill:var(--fg-main);font-family:var(--mono);font-size:14px}
.uarttop .dim{fill:var(--fg-dim);font-size:11.5px}
.uarttop .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.uarttop .sigA{fill:var(--accent);font-family:var(--mono);font-size:12.5px}
.uarttop .mlab{fill:var(--fg-dim);font-family:var(--mono);font-size:12.5px}
.uarttop .ah{fill:var(--fg-main)}
.uarttop .aha{fill:var(--accent)}
.uarttop .ahd{fill:var(--fg-dim)}
</style>
<defs>
<marker id="ut-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="ut-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
<marker id="ut-ad" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahd"/></marker>
</defs>
<rect class="mbox" x="150" y="44" width="470" height="256" rx="10"/>
<text class="mlab" x="160" y="62" text-anchor="start">uart</text>
<rect class="box" x="170" y="150" width="150" height="52" rx="6"/>
<text class="name" x="245" y="173" text-anchor="middle">oversampleTick</text>
<text class="dim" x="245" y="190" text-anchor="middle">baud generator</text>
<rect class="box" x="400" y="70" width="140" height="56" rx="6"/>
<text class="name" x="470" y="96" text-anchor="middle">uartRx</text>
<text class="dim" x="470" y="113" text-anchor="middle">receiver</text>
<rect class="box" x="400" y="226" width="140" height="56" rx="6"/>
<text class="name" x="470" y="252" text-anchor="middle">uartTx</text>
<text class="dim" x="470" y="269" text-anchor="middle">transmitter</text>
<line class="parm" x1="78" y1="176" x2="168" y2="176" marker-end="url(#ut-ad)"/>
<text class="sig" x="40" y="172" text-anchor="start">baud</text>
<text class="dim" x="40" y="188" text-anchor="start">(SNat)</text>
<line class="wire" x1="100" y1="98" x2="398" y2="98" marker-end="url(#ut-a)"/>
<text class="sig" x="40" y="95" text-anchor="start">rxLine</text>
<text class="dim" x="40" y="110" text-anchor="start">Bit</text>
<line class="wire" x1="100" y1="254" x2="398" y2="254" marker-end="url(#ut-a)"/>
<text class="sig" x="40" y="251" text-anchor="start">txByte</text>
<text class="dim" x="40" y="267" text-anchor="start">Maybe byte</text>
<line class="tickw" x1="320" y1="176" x2="376" y2="176"/>
<line class="tickw" x1="376" y1="112" x2="376" y2="268"/>
<line class="tickw" x1="376" y1="112" x2="398" y2="112" marker-end="url(#ut-aa)"/>
<line class="tickw" x1="376" y1="268" x2="398" y2="268" marker-end="url(#ut-aa)"/>
<circle class="node" cx="376" cy="176" r="3.5"/>
<text class="sigA" x="348" y="168" text-anchor="middle">tick</text>
<line class="wire" x1="540" y1="86" x2="688" y2="86" marker-end="url(#ut-a)"/>
<text class="sig" x="694" y="90" text-anchor="start">rxByte</text>
<line class="wire" x1="540" y1="110" x2="688" y2="110" marker-end="url(#ut-a)"/>
<text class="sig" x="694" y="114" text-anchor="start">rxErr</text>
<line class="wire" x1="540" y1="242" x2="688" y2="242" marker-end="url(#ut-a)"/>
<text class="sig" x="694" y="246" text-anchor="start">txLine</text>
<line class="wire" x1="540" y1="266" x2="688" y2="266" marker-end="url(#ut-a)"/>
<text class="sig" x="694" y="270" text-anchor="start">txReady</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The whole of <code>Tamal.Uart</code> as one picture. The dashed box is the module boundary; inside it a single <code>oversampleTick</code> — the <a href="https://balbi.sh/posts/tamal-uart-baudgen/">baud generator</a> — drives one accent wire, the <code>tick</code>, that fans into both <code>uartRx</code> — the <a href="https://balbi.sh/posts/tamal-uart-rx/">receiver</a> — and <code>uartTx</code> — the <a href="https://balbi.sh/posts/tamal-uart-tx/">transmitter</a>. The compile-time <code>baud</code> enters the generator as a dashed parameter, not a runtime wire. Six signals cross the boundary: <code>rxLine</code> and <code>txByte</code> in, and <code>rxByte</code>, <code>rxErr</code>, <code>txLine</code>, <code>txReady</code> out — the receiver's two ports beside the transmitter's two, which is precisely what <code>uart</code>'s type says. The one wire that never crosses the boundary is the <code>tick</code>: generated once, shared by both machines, internal to the module. Composition in a single figure — three blocks that each own an idea, joined by a seam that owns none.</figcaption>
</figure>

## Where the four wires go

Inside `uart` the story is over and self-contained: the loopback proved
the four wires carry what they claim. The interesting question is where
that four-tuple *goes* when the UART stops being read as a closed unit
and starts being used. That happens one level up, in the board shell's
`system`, and the seam is worth seeing even though the shell is a later
post's subject:

```haskell
(rxByte, _rxErr, txLine, txReady) = uart (SNat @2_000_000) rxLine txByteL
```

Three things to read here. First, the baud finally gets a *number*: `SNat
@2_000_000`, two megabaud, the [baud generator's][baudgen] `2_000_000`
chosen at the one call site that has to choose it. `uart` itself stayed
baud-generic to the end --- the `SNat baud` in its type --- exactly as
decision 3 of the [design][baudgen] intended; the shell is where the knob
is finally turned. Everything below `uart` inherited its freedom from a
number by never naming one, and the shell names it once, here, and the
whole tree specialises.

Second, follow the wires into the rig. `rxByte` feeds a loader FSM that
fills the instruction BRAM byte by byte --- the RX-to-load path.
`txReady`, paired with the loader's own `txByteL`, drains trace words
back out --- the TX side of the same FSM. `txLine` goes to the USB-UART
pin. These are the four terminals the boring diagram in the
[introduction][intro] drew as a single `UART` box between the host and
the loader; this is that box's actual boundary, four wires wide.

Third, and most honestly: `_rxErr`. The framing-error strobe --- the
output the [receiver][rx] worked hardest for, the one with no transmitter
analogue, the flag it raises when a stop bit comes back low --- is bound
to `_rxErr` and **dropped.** Today's shell does not consume it. Section 9
of the [design][baudgen] reserved it for "abort or flag a corrupt load,"
and that consumer is not built yet, so the wire is generated, typed,
tested, and --- for now --- left hanging under the underscore that tells
GHC we meant to ignore it. It is a small, true window into how a design
grows: the receiver produces the honest signal before the shell is ready
to act on it, and the top faithfully carries it to a boundary where it
currently goes nowhere. The wire is ready for its consumer the way the
whole interface is ready for the loader that plugs in next --- built to
the right shape, waiting.

## What we read

The shortest module in the UART, read at last. Five lines --- one
exported name, four wires --- and no state at all. The type was longer
than the code and told most of the story before the code did: the baud
generator's numeric obligations **come home** to the top, because owning
the generator means owning its `SNat baud` and its `KnownNat`, and the
transmitter's and receiver's numberless freedom was bought by
concentrating every number here. The interface is the receiver's ports
and the transmitter's ports set side by side, with the one wire that ran
*between* them --- the tick --- gone internal, so the type itself shows
what composition tucks away and what it leaves facing the world.

The body was a net-list wearing the syntax of equations: not computations
run in order but wires existing at once, three sub-blocks *placed* and
connected, a `where` that hides names rather than work. Its one decision
was to generate the heartbeat **once** and fan it into both machines ---
decision 2 --- which is what makes the loopback a structural certainty
rather than a coincidence of two clocks, one time base counting the one
"sixteen" that is both the bit the transmitter holds and the center the
receiver samples. And its four output wires are the seam: one level up,
`rxByte` and `txReady` and `txLine` join a loader FSM and a pin, while
`rxErr` waits, dropped for now, for a consumer the design has promised
but not yet built.

The [receiver][rx] told you the UART was behind us, and it was right
about the behaviour --- we had watched the byte survive the round trip.
Now it is behind us as *text*, too: every file of the transport read,
down to the five-line seam that lets us say "the UART" as a single word.
And those four wires reach, for now, exactly one block --- the one
pressed flush against the UART. Not the Engine but the **loader**: the
FSM that catches `rxByte` strobes and writes them into instruction
memory, and feeds bytes back out while `txReady` is high, the very four
terminals we traced into `system` a moment ago. Most of it we will read
quickly, because it wears the silhouette we have now read four times ---
`mealy` over a pure `loaderStep`, a sum for the phase (`RxControl | Run |
Drain`, with a finer one for draining) and a record for the rest. What is
*new* is the wire it speaks. A UART hands you a bare stream of bytes, and
a bare stream is not yet a *message*: where does one frame end and the
next begin? The loader's answer is **COBS** --- Consistent Overhead Byte
Stuffing --- which stuffs every payload so the byte `0x00` never appears
inside it, freeing that one value to mean *frame boundary*; the loader
decodes the stream on the way in and encodes the drain on the way out. It
is where the byte *pipe* we finished today becomes a byte *protocol*.

The **Engine** the [introduction][intro] promised --- the stateful
difficulty spike the [design][baudgen] kept deferring --- still waits its
turn beyond the loader. But it waits. The heartbeat is generated, the
machines are wired, the bytes cross whole. Next we read the little
machine that frames them: the loader, and its COBS.

[^repetitive]: Yes, the refrain is deliberate. The [primer] made the
point and every block since has leaned on it, because a claim repeated
across a series is a claim that sticks: in Haskell, and doubly in Clash,
the type signature **is** half the documentation. Read the type before
the body and the body holds few surprises. I will keep saying it until
it needs no saying.

[^splitbaudgen]: In fairness, there is a version of two generators that
is not mere duplication. Run one at wire speed and one at 16× wire speed
--- the slow one for the [transmitter][tx], the fast one for the
[receiver][rx] --- and the transmitter genuinely simplifies: handed a
tick already at one-per-bit, it sheds its `Index 16` and advances a bit
per tick, leaving oversampling to the receiver, the only half that needs
it. But weigh it. The split spends a second oscillator --- another
accumulator, adder, and comparator --- to save a four-bit counter: more
logic, not less, though at this scale the difference vanishes into the
noise. So the choice was never cost; it is which story is cleaner, and
*one heartbeat, divided where it is needed* reads better than *two
heartbeats that must be kept in tune*. Here, reading better and
maintaining better are the same thing.

[^netlist]: This is the difference between *composition* in software and
in hardware, and [Clash] sits exactly on the seam. In an ordinary Haskell
program, `uart baud rxLine txByte` would *call* `oversampleTick`,
`uartRx`, and `uartTx` --- run them, wait for their results, thread values
through. Clash does not run them; it **elaborates** them. Each call site
becomes an *instance* of that sub-circuit stamped into the net-list, and
each `where`-bound name becomes a *net* joining the instances --- `tick` a
single wire with a fan-out of two, driving the enable input of the
receiver instance and the transmitter instance at once. That is why
naming `tick` once and using it twice costs one oscillator and not two:
it is one net, not two evaluations. It is also why the binding order is
immaterial --- a net-list is a graph, and a graph has no first line. The
top adds no register and no gate that computes; it is a pure structural
node, transparent to behaviour and total to structure, which is the
precise hardware meaning of "this module is only wiring."
