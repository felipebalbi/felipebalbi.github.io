+++
title = "Tamal: Everything, Wired"
date = 2026-08-14T09:00:00
draft = false
description = "Every module in the series has been read; none of them have been joined. Tamal.Top joins them in a hundred and fifteen lines: one function, system, that takes a UART line and four sampled IO bits and gives back a serial line, a four-lane drive, three sidebands and a status LED. The moment the engine gets its clock is stepM, three lines re-associating step's (State, BusOut, Maybe Ring) into the pair mealy demands, with a property test proving the adapter adds nothing at all. system's signature is deliberately free of BiSignal, so the whole integration is cosim-testable and the awkward part stays a hundred and four lines wide. Then the confluence: the UART at two megabaud, the loader FSM, the instruction and ring block RAMs, and mealy stepM initState at the centre, with three feedback knots --- the program counter into the instruction memory, the ring write into the trace memory, the trace memory back into the loader's drain. ringWrite projects a Ring record onto the memory's bare tuple, keeping the promise that a memory never learns what a trace record is. The LED is a latch, a counter and a truth table. And the tests run the whole machine for the first time: a program serialised onto a wire, triggered, and drained back as a REVISION word and a HALT terminator with every field zero."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "top", "espi"]
+++

The [pads exist][io] now, and they are wired to nothing.

That is the odd position the series has arrived in. We have read a CRC unit, a
baud generator, a transmitter, a receiver and the umbrella over them; a COBS
codec and the loader that streams it; two block RAMs; an engine, in eight
instalments, from its eight-phase map down to the two bits of a lane; two pure
format models for the bytes that go in and the words that come out; and, last
week, the tri-state buffers that turn a `(value, enable)` pair into a pin that
floats. Every piece is open. Not one of them is connected to another.

`Tamal.Top` connects them. It is a hundred and fifteen lines, and it contains
exactly one function that matters --- `system`, which takes a serial line and
some sampled bits and hands back a serial line, a bus drive and a blinking LED
--- and four small pure helpers that exist so the parts of the wiring that could
have been logic are not hidden inside it. Reading it is less like reading a
module and more like reading a table of contents, because every import is a post.

<!-- more -->

[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[uart-top]: https://balbi.sh/posts/tamal-uart-top/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[loader]: https://balbi.sh/posts/tamal-loader/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[exec]: https://balbi.sh/posts/tamal-engine-exec/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[isa]: https://balbi.sh/posts/tamal-isa/
[serdes]: https://balbi.sh/posts/tamal-serdes/
[wire]: https://balbi.sh/posts/tamal-wire/
[trace]: https://balbi.sh/posts/tamal-trace/
[io]: https://balbi.sh/posts/tamal-io/
[intro]: https://balbi.sh/posts/tamal-introducing/

## Six names, and the series behind them

```haskell
module Tamal.Top
  ( system
  , stepM
  , ringWrite
  , RigState (..)
  , rigState
  , ledPattern
  ) where

import Clash.Prelude

import Tamal.Bus.Serdes (Lanes)
import Tamal.Engine (BusIn (..), BusOut (..), Ring (..), State, initState, step)
import Tamal.Loader (LoaderIn (..), LoaderOut (..), loader)
import Tamal.Mem (instrRam, ringRam)
import Tamal.Params (RW)
import Tamal.Uart (uart)
```

Six exports, and the asymmetry between them is the design. One of the six is the
machine. The other five are *pure functions the machine happens to use*, hoisted
to the door so they can be tested without a clock: `stepM`, an adapter;
`ringWrite`, a projection; and `RigState` with `rigState` and `ledPattern`, a
three-state enum and the two total functions over it that decide how an LED
blinks. Nothing in that list computes anything the design needs at speed. They
are there because a wiring module that quietly grew logic inside it would be a
wiring module you could not check, and the author would rather export four small
things than hide them.

The imports are the more striking half. Six modules, and every one of them has a
post: [`Serdes`][serdes] for the `Lanes` type, [`Engine`][shape] for the state
machine and the four record types that plug into it, [`Loader`][loader] for the
frame FSM, [`Mem`][mem] for the two block RAMs, [`Params`][mem] for the `RW`
address width, [`Uart`][uart-top] for the serial umbrella. The [shape
post][shape] opened with eight closed boxes and called that *the exact inversion
of the leaves-first UART* --- a map drawn before any of its territories had been
walked. This import list is the same map with every box now open, and it is the
first module in the project whose header you cannot read without having read the
project.

## The moment the engine gets its clock

The engine's keystone type has been sitting still since the [shape post][shape]:

```haskell
step :: State -> BusIn -> (State, BusOut, Maybe Ring)
```

A pure function. Give it a state and this cycle's inputs; get back the next
state, the pins to drive, and maybe a trace word. Eight posts have described
what it computes, and in all eight it has never once run in time --- it is a
transition, not a machine, and a transition needs something to iterate it.

That something is `mealy`, which the [primer][primer] introduced and the
[transmitter][uart-top] first put at the top of a module. Its type wants a
transition of a particular shape:

```haskell
mealy :: (HiddenClockResetEnable dom, NFDataX s) =>
  (s -> i -> (s, o)) -> s -> Signal dom i -> Signal dom o
```

A pair. State and output, two elements. `step` returns three, because the
[shape post][shape] argued at length that splitting the output into pins and
trace-word was worth doing --- `BusOut` is *what the wires carry right now* and
`Maybe Ring` is *a thing to remember*, and conflating them would have been a
smaller type and a worse one. So the two shapes do not quite meet, and three
lines bridge them:

```haskell
stepM :: State -> BusIn -> (State, (BusOut, Maybe Ring))
stepM s i = (s', (bo, mr))
 where
  (s', bo, mr) = step s i
```

One pair of parentheses moved. `(State, BusOut, Maybe Ring)` becomes
`(State, (BusOut, Maybe Ring))`, and now the second element is a single `o` and
`mealy` will take it. That is the entire function, and it is worth stopping on
precisely *because* it is nothing, because the line that uses it is the most
consequential in the file:

```haskell
(busOut, maybeRing) = unbundle (mealy stepM initState busInS)
```

Read that slowly. `mealy stepM initState` takes the pure transition and gives
back a function from a `Signal` of inputs to a `Signal` of outputs: it allocates
the register that holds `State`, initialises it to [`initState`][shape], and
arranges for the transition to be applied once per clock edge for the rest of the
design's life. Eight posts of arithmetic acquire a clock in one application.
`unbundle` then splits the paired output back into two signals, so the rest of
the module can use `busOut` and `maybeRing` separately --- the pairing existed
only to satisfy `mealy`, and it is undone the moment it has.

> `step` describes what happens next. `mealy stepM initState` is what makes next
> happen.

The [loader][loader] was called *the first block that owns a clock*, and it
earned the phrase: it had a `mealy` at its top and eighteen fields of state
turning underneath. The engine is bigger than the loader by every measure ---
seventeen state fields, eight phases, thirty-six opcodes --- and it does not own
a clock at all. It never did. It borrows one here, in the top, on one line, and
that is the whole reason the [shape post's][shape] property tests could hammer
`step` with random inputs and no simulation: a function you can call is a
function you can test a hundred thousand times a second.

`stepM` gets a test of its own, and the test is a statement that the adapter is
not a place where behaviour can hide:

```haskell
testProperty "stepM = step re-associated" $ H.property $ do
  i <- H.forAll genBusIn
  let (s', bo, mr) = step initState i
  stepM initState i H.=== (s', (bo, mr))
```

For a random `BusIn` --- random instruction word, four random sampled IO bits, a
random `ALERT#`, a random start flag --- `stepM` must return exactly what `step`
returned, only re-parenthesised. It looks like testing that a tuple is a tuple.
It is really a guard on a *seam*: `stepM` is the last thing that touches the
engine's output before the clock does, and a swapped element or a dropped `Maybe`
here would be invisible in every engine test and fatal on the board.

## No BiSignal, on purpose

Now the signature that the whole module is arranged around:

```haskell
system ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bit ->            -- uart RX line
  Signal dom (Vec 4 Bit) ->    -- ioIn
  Signal dom Bit ->            -- alertIn
  ( Signal dom Bit             -- uart TX line
  , Signal dom Lanes           -- lanesOut
  , Signal dom Bit             -- csOut
  , Signal dom Bit             -- sckOut
  , Signal dom Bit             -- rstOut
  , Signal dom Bit             -- led
  )
```

Three in, six out, and not a `BiSignal` anywhere. The module's own doc comment
says why in half a sentence --- *no `BiSignal`, so the whole integration is
cosim-testable* --- and the [previous post][io] is the long version of that
half-sentence.

Recall the shape of the trouble. A bidirectional net's value is a function of
every driver attached to it, so a driver derived from a read of the same net is a
value defined in terms of itself, and Clash's simulator does not resolve it --- it
diverges. The [pad post's][io] test harnesses had to be split into two
single-driver directions to say anything at all about four lanes and one
synchroniser. That is a tolerable amount of ceremony for a hundred and four lines.
It would be an intolerable amount for a test that wants to serialise a program
onto a UART, watch a loader parse it, watch an engine execute it, and read the
result back out.

So the boundary is drawn to keep the hard part small. `system` speaks entirely in
ordinary unidirectional `Signal`s: `ioIn` arrives already sampled, four plain
`Bit`s, and `lanesOut` leaves as a plain `Lanes`, four `(value, enable)` pairs
that are still just numbers. Everything bidirectional lives *outside*, in
[`espiPads`][io], and the board shell is what puts the two together. The result is
a partition with a pleasing property: the part of the design that cannot be
simulated conveniently is a hundred and four lines with nine tests, and the part
that is the actual machine --- fifteen hundred lines of UART, loader, memories and
engine --- can be driven end to end with a list of `Bit`s.

> Draw the boundary where the tooling gets hard, not where the diagram looks
> tidy.

There is a second thing hiding in that signature, quieter than the first: it is
generic in `dom`. `system` does not name a clock frequency, a board, or a
domain. It says `HiddenClockResetEnable dom` and lets whoever instantiates it
supply the clock --- which is what makes one `system` serve two board shells,
and what leaves the whole question of *which silicon* for the post after this
one.

## The confluence

The body is thirty lines, and it is the entire design:

```haskell
system rxLine ioIn alertIn = (txLine, lanesO, csO, sckO, rstO, ledOut)
 where
  -- UART @ 2MBaud
  (rxByte, _rxErr, txLine, txReady) = uart (SNat @2_000_000) rxLine txByteL

  -- Loader FSM
  lOut = loader (LoaderIn <$> rxByte <*> txReady <*> halted <*> ringPtrO <*> ringData)
  txByteL = txByte <$> lOut
  instrWrL = instrWr <$> lOut
  ringAddrL = ringAddr <$> lOut
  startO = startOut <$> lOut

  -- Memories
  instrWord = instrRam pcO instrWrL
  ringData = ringRam ringAddrL (ringWrite <$> maybeRing)

  -- Engine
  (busOut, maybeRing) = unbundle (mealy stepM initState busInS)
  busInS = BusIn <$> instrWord <*> ioIn <*> alertIn <*> startO
  pcO = pcOut <$> busOut
  lanesO = lanesOut <$> busOut
  …
```

Four comments, four stages, and the four stages are the series in order.

The **UART** comes first, and it is the [umbrella][uart-top] read whole: `uart
(SNat @2_000_000) rxLine txByteL` returns four things, and the design uses three
of them. `rxByte` is a `Maybe (BitVector 8)` strobing once per received byte,
`txLine` is the outgoing wire, `txReady` is the transmitter's back-pressure. The
baud rate is passed as a type-level number in the [`SNat`][uart-top] the umbrella
demanded, and everything the [baud generator][baudgen] did with a fractional
3.125 follows from it and from whatever `dom` turns out to be.

And `_rxErr` is dropped. The [UART top post][uart-top] noted that the framing
error had no consumer yet and left it unwired, and here at the very top --- the
last place it could have found one --- it still has none. That underscore is the
project being honest about an unfinished edge rather than inventing a use for it.

The **loader** is next, and its input is built by *applying a constructor across
signals*:

```haskell
lOut = loader (LoaderIn <$> rxByte <*> txReady <*> halted <*> ringPtrO <*> ringData)
```

`LoaderIn` is an ordinary five-field record. `<$>` and `<*>` lift its constructor
over `Signal`, so what comes out is a `Signal dom LoaderIn` --- one record per
cycle, its five fields taken from five separate signals at the same
instant.[^applicative] The four outputs come back the same way, each a field
projection mapped over `lOut`: `txByte <$> lOut` for the byte to transmit,
`instrWr <$> lOut` for a write into the instruction memory, `ringAddr <$> lOut`
for the drain's read address, `startOut <$> lOut` for the trigger. The
[loader's][loader] three lives --- receive a frame, hold it, drain the ring ---
are all behind that one call.

The **memories** are two lines, and both are the [mem post's][mem] four-line leaf
instantiated:

```haskell
instrWord = instrRam pcO instrWrL
ringData = ringRam ringAddrL (ringWrite <$> maybeRing)
```

`instrRam` is read by the engine's program counter and written by the loader.
`ringRam` is read by the loader's drain and written by the engine's trace
emitter. Each memory has exactly one reader and one writer, and in both cases
they are *different modules pointing in opposite directions* --- which is the
shape a rig wants: the host writes programs and reads results; the engine reads
programs and writes results.

And the **engine** closes it. `busInS` assembles a `BusIn` from four sources ---
`instrWord` from the instruction memory, `ioIn` and `alertIn` from the caller
(and thence from [the pads][io]), `startO` from the loader --- and `mealy stepM
initState` runs it. `busOut` is then fanned out one field at a time: `pcOut` back
to the memory, `lanesOut`/`csOut`/`sckOut`/`rstOut` out to the caller,
`haltedOut` to the loader and the LED, `ringPtrOut` to the loader so the drain
knows how far to sweep.

Three of those wires are *loops*.

<figure class="tp-fig" style="margin:2rem 0">
<svg class="tp" viewBox="0 0 920 400" role="img" aria-labelledby="tp-t tp-d" xmlns="http://www.w3.org/2000/svg">
<title id="tp-t">The whole Tamal design as system wires it, with its three feedback loops</title>
<desc id="tp-d">A dashed boundary labelled system encloses six blocks. Along the top row, left to right: uart, loader, instrRam, and an accented block labelled mealy stepM initState. The rxLine enters uart from outside the boundary and txLine leaves it. Between uart and loader, rxByte runs right and txByte runs left. The loader sends instrWr to instrRam, which sends instrWord to the engine. A feedback wire labelled pcO runs from the engine's top, left along a corridor above the row, and down into instrRam. Below, a ringRam block sits under the loader, exchanging ringAddr downward and ringData upward with it; a wire labelled ringWrite runs from the engine's bottom, left, and down into ringRam's right side. A ledPattern block sits below the engine, fed by a wire labelled halted, and drives led out through the boundary. On the right, lanesOut and the cs, sck and rst sidebands leave the engine through the boundary, and ioIn with alertIn enter it.</desc>
<style>
.tp{max-width:920px;width:100%;height:auto;display:block;margin:0 auto}
.tp .bx{fill:var(--bg-dim);stroke:var(--fg-dim);stroke-width:1.6}
.tp .bxa{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.6}
.tp .bnd{fill:none;stroke:var(--fg-dim);stroke-width:1.6;stroke-dasharray:7 5}
.tp .w{stroke:var(--fg-dim);stroke-width:1.7;fill:none}
.tp .wa{stroke:var(--accent);stroke-width:2.2;fill:none}
.tp .m{fill:var(--fg-main);font-family:var(--mono);font-size:13px}
.tp .ma{fill:var(--accent);font-family:var(--mono);font-size:13px}
.tp .l{fill:var(--fg-dim);font-family:var(--mono);font-size:9.5px}
.tp .la{fill:var(--accent);font-family:var(--mono);font-size:9.5px}
.tp .s{fill:var(--fg-dim);font-family:var(--sans);font-size:11px}
.tp .ah{fill:var(--fg-dim)}
.tp .aha{fill:var(--accent)}
</style>
<defs>
<marker id="tp-a" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="ah"/></marker>
<marker id="tp-aa" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- system boundary -->
<rect class="bnd" x="52" y="12" width="756" height="372" rx="10"/>
<text class="s" x="60" y="30">system</text>
<!-- blocks -->
<rect class="bx" x="76" y="68" width="118" height="56" rx="7"/>
<text class="m" x="135" y="100" text-anchor="middle">uart</text>
<rect class="bx" x="256" y="68" width="118" height="56" rx="7"/>
<text class="m" x="315" y="100" text-anchor="middle">loader</text>
<rect class="bx" x="436" y="68" width="118" height="56" rx="7"/>
<text class="m" x="495" y="100" text-anchor="middle">instrRam</text>
<rect class="bxa" x="628" y="68" width="150" height="56" rx="7"/>
<text class="ma" x="703" y="92" text-anchor="middle">mealy stepM</text>
<text class="ma" x="703" y="110" text-anchor="middle">initState</text>
<rect class="bx" x="256" y="210" width="118" height="56" rx="7"/>
<text class="m" x="315" y="242" text-anchor="middle">ringRam</text>
<rect class="bx" x="628" y="310" width="150" height="56" rx="7"/>
<text class="m" x="703" y="342" text-anchor="middle">ledPattern</text>
<!-- serial line in and out -->
<text class="l" x="8" y="78">rxLine</text>
<line class="w" x1="14" y1="88" x2="70" y2="88" marker-end="url(#tp-a)"/>
<text class="l" x="8" y="128">txLine</text>
<line class="w" x1="76" y1="112" x2="20" y2="112" marker-end="url(#tp-a)"/>
<!-- uart and loader -->
<line class="w" x1="194" y1="84" x2="250" y2="84" marker-end="url(#tp-a)"/>
<text class="l" x="225" y="74" text-anchor="middle">rxByte</text>
<line class="w" x1="256" y1="110" x2="200" y2="110" marker-end="url(#tp-a)"/>
<text class="l" x="225" y="128" text-anchor="middle">txByte</text>
<!-- loader to instrRam -->
<line class="w" x1="374" y1="96" x2="430" y2="96" marker-end="url(#tp-a)"/>
<text class="l" x="405" y="86" text-anchor="middle">instrWr</text>
<!-- instrRam to engine -->
<line class="w" x1="554" y1="96" x2="622" y2="96" marker-end="url(#tp-a)"/>
<text class="l" x="588" y="86" text-anchor="middle">instrWord</text>
<!-- pcO feedback, above -->
<path class="wa" d="M703,68 L703,32 L495,32 L495,62" marker-end="url(#tp-aa)"/>
<text class="la" x="599" y="24" text-anchor="middle">pcO</text>
<!-- ringWrite feedback, below -->
<path class="wa" d="M703,124 L703,172 L412,172 L412,238 L380,238" marker-end="url(#tp-aa)"/>
<text class="la" x="557" y="164" text-anchor="middle">ringWrite</text>
<!-- loader and ringRam -->
<line class="w" x1="292" y1="124" x2="292" y2="204" marker-end="url(#tp-a)"/>
<text class="l" x="284" y="172" text-anchor="end">ringAddr</text>
<line class="wa" x1="338" y1="210" x2="338" y2="130" marker-end="url(#tp-aa)"/>
<text class="la" x="346" y="172">ringData</text>
<!-- engine to led -->
<line class="w" x1="748" y1="124" x2="748" y2="304" marker-end="url(#tp-a)"/>
<text class="l" x="756" y="228">halted</text>
<line class="w" x1="778" y1="338" x2="826" y2="338" marker-end="url(#tp-a)"/>
<text class="l" x="832" y="342">led</text>
<!-- pins right -->
<line class="w" x1="778" y1="84" x2="826" y2="84" marker-end="url(#tp-a)"/>
<text class="l" x="832" y="80">lanesOut</text>
<text class="l" x="832" y="96">cs, sck, rst</text>
<line class="w" x1="826" y1="118" x2="782" y2="118" marker-end="url(#tp-a)"/>
<text class="l" x="832" y="122">ioIn, alertIn</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The whole design, as <code>system</code> wires it. The dashed boundary is the signature: everything inside speaks in ordinary unidirectional <code>Signal</code>s, and the four bidirectional <code>IO</code> lanes are somebody else's problem — <code>ioIn</code> arrives already sampled and <code>lanesOut</code> leaves as four <code>(value, enable)</code> pairs. The three accented wires are the feedback loops: <code>pcO</code> into the instruction memory whose answer comes back a cycle later, <code>ringWrite</code> into the trace memory, and <code>ringData</code> back out of it into the loader's drain. Each of the three is a definition that mentions a name defined further down the <code>where</code> block, and each is legal because these are nets, not values.</figcaption>
</figure>

Those loops are the reason this module reads strangely on a first pass. `pcO` is
defined from `busOut`, which comes from `busInS`, which contains `instrWord`,
which comes from `instrRam pcO` --- `pcO` appears on both sides. `ringData` feeds
the loader, whose `lOut` produces `ringAddrL`, which the memory needs to produce
`ringData`. `halted` is read by the loader four lines above the line that
defines it.

None of that is a problem, and the [UART top post][uart-top] already explained
why: Clash **elaborates**, it does not evaluate. A `where`-bound name in a
hardware description is the name of a *net*, and a netlist has no notion of
"before". Writing `instrWord = instrRam pcO instrWrL` above the line that defines
`pcO` states that the memory's address port is connected to the engine's `pcOut`
port, which is a fact about wires and is as true read upwards as downwards. What
would be a problem is a loop with no register in it, and there is none: the
instruction memory's [one-cycle read latency][mem] breaks the fetch loop, the
engine's own state register breaks the trace loop, and the [`Fetch`
phase][shape] exists precisely to spend the cycle that latency costs.[^knot]

## One line that keeps a promise

The projection between the engine and the trace memory is a single line, and it
is the smallest thing in the file with an argument behind it:

```haskell
ringWrite :: Maybe Ring -> Maybe (Unsigned RW, BitVector 32)
ringWrite = fmap (\(Ring a d) -> (a, d))
```

The engine emits `Maybe Ring`, a record with named fields `rAddr` and `rData`.
The memory's write port takes `Maybe (Unsigned RW, BitVector 32)`, a bare tuple.
`ringWrite` unwraps one into the other, and `fmap` carries it through the `Maybe`
so a cycle with nothing to write stays a cycle with nothing to write.

The [mem post][mem] spent a section on why the write port is a bare tuple, and
gave the reason as an aphorism: *a memory that imports the engine is a memory
that knows what a trace record is*. `ringRam` is deliberately ignorant. It stores
thirty-two-bit words at addresses and has never heard of `Ring`, or `Capture`, or
the [two-bit tag][trace] that tells a host which record shape follows. Keeping it
that way requires somebody, somewhere, to do the unwrapping --- and this is the
somebody. The adapter lives in the top, which is the one module that is *supposed*
to know about everything, and so the ignorance the memory was designed to have is
paid for in one line by the module that can afford it.

Its tests are two, and they are exactly the two cases: `ringWrite Nothing` is
`Nothing`, and `ringWrite (Just (Ring a d))` is `Just (a, d)` for random `a` and
`d`. Trivial --- and the sort of trivial that catches a swapped pair.

## A status LED, made a truth table

The last eight lines are the user interface, and they are the only part of the
design a person looks at directly:

```haskell
data RigState = Waiting | Running | Done

rigState :: Bool -> Bool -> RigState
rigState _ True = Done
rigState True False = Running
rigState False False = Waiting

ledPattern :: RigState -> Unsigned 26 -> Bit
ledPattern Waiting c = msb c
ledPattern Running c = msb (c `shiftL` 3)
ledPattern Done _ = high
```

And in `system`, the three lines that give them time:

```haskell
running = register False (mux startO (pure True) (mux halted (pure False) running))
ledCnt = register (0 :: Unsigned 26) (ledCnt + 1)
ledOut = ledPattern <$> (rigState <$> running <*> halted) <*> ledCnt
```

Split that in half and the split is the point. The *stateful* part is two
registers: a one-bit `running` latch that sets on the loader's trigger and clears
when the engine halts, and a free-running twenty-six-bit counter that does
nothing but increment forever. The *decision* part is two pure total functions,
exported and tested, that never touch a clock.

`rigState` is a truth table with three rows and a wildcard, and the wildcard is a
priority: `halted` wins. A rig that has run and stopped shows `Done` whether or
not the latch is still set, so the terminal state is genuinely terminal. Its
tests are the truth table, written out --- both halted cases, the running case,
the idle case --- which is what a three-line function deserves and rarely gets.

`ledPattern` is the blink, and it says a great deal in three lines. `msb c` is the
top bit of the counter, which at 100 MHz toggles every 2²⁵ cycles: about a third
of a second on and a third off, a slow, patient pulse.[^blink] ``msb (c `shiftL`
3)`` reads bit 22 instead --- shifting left by three brings a lower bit
into the top position --- so `Running` blinks **eight times faster** on exactly
the same counter, no second divider, no second register. And `Done` is `high` with
the counter ignored: solid on, and the underscore in `ledPattern Done _` is the
whole statement that a finished rig does not blink.

Its tests check the two rates against the same count, which is the sharp way to
do it:

```haskell
ledPattern Running 0x400000 @?= high
ledPattern Waiting 0x400000 @?= low   -- same count => Running is faster
```

At count 2²², `Running` is on and `Waiting` is still off. One assertion, and the
relationship --- *faster* --- is pinned rather than the absolute rates. That is the
property that actually matters to a person squinting at a board: not that the LED
blinks at 1.5 Hz, but that a running rig looks visibly different from a waiting
one.

There is a general point here worth naming, because it is why these functions are
exported at all. A status indicator is the least testable part of most designs ---
it is inherently about what something *looks like* over time, and it usually ends
up as three lines of ad-hoc logic buried in a top-level module where nothing can
reach it. Splitting it into a state derivation and a pattern function turns the
question "does the LED do the right thing" into two questions with yes-or-no
answers, and moves the only untestable part --- how fast a third of a second feels
--- into a constant.

## The tests: the first time the whole thing runs

Everything so far has been checked in pieces. `Test.Top` is the first time the
pieces run together, and the harness it needs is instructive.

```haskell
cyclesPerBit :: Int
cyclesPerBit = 50
```

100 MHz over 2 Mbaud is fifty cycles a bit --- the same arithmetic the [baud
generator][baudgen] did with a fractional accumulator, done here in whole
numbers because the test bench drives the line rather than recovering it. From
that, `serialize` turns a list of bytes into a list of `Bit` samples: fifty low
for the start bit, fifty per data bit LSB-first, and then *two* bit-times high.

That last detail is an admission, and the comment makes it:

```haskell
-- one idle bit-time between bytes (a realistic transmitter's inter-byte gap ---
-- the RX needs it to resync; truly back-to-back bytes drop on the falling-edge
-- resync).
```

A stop bit and one idle bit before the next start. That is what a real UART
transmitter emits and what a real host sends, so the bench is not cheating --- but
it is recording that the [receiver's][rx] falling-edge resynchronisation wants
the gap, and that a stream with literally none would lose bytes. The kind of
thing you learn when the pieces meet.

The decoder in the other direction is the nicer trick:

```haskell
deserialize samples =
  [ b | Just b <- sampleN (L.length samples)
          (fst (uartRx (oversampleTick (SNat @2_000_000)) (fromList …))) ]
```

It does not reimplement UART framing. It runs the captured `txLine` back through
**the real [`uartRx`][rx]**, fed by the real [`oversampleTick`][baudgen], and
collects the bytes that strobe out. The [receiver post][rx] closed on a byte-exact
TX-to-RX loopback and called it the keystone; this is that keystone used as
laboratory equipment. The transmitter under test and the receiver reading it are
the two halves the loopback already proved agree, so a decoding failure here is a
system failure and not a bench artefact.

`runSystem` then drives the machine, and its one subtlety is at the front:

```haskell
leadN = cyclesPerBit
```

Fifty idle-high cycles before the real stream, so that the domain's cycle-zero
reset settles while the line is idle rather than in the middle of the first start
bit. It is the [same `sampleN` idiom][mem] the memory tests needed, applied to a
whole design.

And then the payoff. Two test cases, and the first is the smallest complete run
the rig can perform:

```haskell
testCase "cosim: load [HALT], trigger -> drain = REVISION + HALT terminator"
  $ loadRunDrain [encode (Halt 0)] 20000
  @?= Right [0x0001_0000, 0xC000_0000]
```

Follow what that sentence asks for. [`encode (Halt 0)`][isa] produces one
thirty-two-bit instruction word. [`encodeControl (LoadProgram …)`][wire] wraps it
in a CRC, COBS-stuffs it and appends a zero delimiter; `encodeControl Trigger`
adds a second frame. `serialize` lays both out as ten thousand-odd line samples.
Then `system` runs: the [UART][uart-top] receives the bytes, the [loader][loader]
peels the frames and writes the word into the [instruction memory][mem], the
trigger raises `startOut`, the [engine][shape] leaves `Idle`, stamps its REVISION
preamble, fetches, executes a `HALT`, and pushes a terminator record. The
[loader][loader] sees `haltedOut`, drains the [ring][mem], re-frames the words,
and clocks them out of the transmitter. `deserialize` reads them back with the
real receiver and [`decodeResult`][wire] unwraps the frame.

What comes out is two words. `0x0001_0000` is the REVISION preamble the [shape
post][shape] said `Preamble` stamps. And `0xC000_0000` is a `HALT` record, which
you can read straight off the [trace post's][trace] field ruler: the top two bits
are the tag `0b11`, and every other field --- seventeen reserved zeros, a
three-bit reason, the trap flag, the overflow flag, the status byte --- is zero. A
clean stop, no trap, no dropped records, status nought. Exactly what `Halt 0`
should leave behind.

That single assertion exercises the [wire format][wire], the [COBS codec][wire],
the [CRC][crc], the [baud generator][baudgen], the [transmitter and
receiver][rx], the [loader's][loader] three lives, both [memories][mem], the
[instruction decoder][isa], the [engine's][shape] phase machine, the [trace
records][trace] and the [ring discipline][trace]. Fourteen posts, one `@?=`.

The second case adds the pins:

```haskell
let prog = [encode CsAssert, encode (PutByteImm 0xA5), encode CsDeassert, encode (Halt 0)]
…
assertBool "cs_n asserts low" (low `L.elem` cs)
assertBool "sck toggles" (low `L.elem` sck && high `L.elem` sck)
decodeResult (deserialize tx) @?= Right [0x0001_0000, 0xC000_0000]
```

A real, if minimal, eSPI transaction: assert chip select, put a byte, deassert,
halt. The assertions are deliberately weak --- `CS#` goes low at some point, `SCK`
takes both values at some point --- because the [bus post][bus] already pinned the
five-cycle beat and the rising edge at the 2→3 boundary with sharp unit tests
against `step`. What this test adds is not precision but *reach*: it shows that a
byte typed into a serial port at one end of the design comes out the other end as
motion on the eSPI wires, and that the trace still drains cleanly afterwards. The
sharp tests prove the waveform; this one proves the waveform is connected to
anything.

## What we read

A hundred and fifteen lines, and the project stops being a collection of modules.
`Tamal.Top` exports one machine and four pure helpers, and imports six modules
that are six posts. `stepM` re-associates the [engine's][shape] three-element
output into the pair `mealy` wants, and that one application --- `mealy stepM
initState` --- is where eight posts of pure transition acquire a clock, a register
and a life; a property test holds the adapter to being nothing but parentheses.
`system` is the whole design over plain `Signal`s, with `BiSignal` deliberately
excluded so the [pad post's][io] simulation knot stays confined to a hundred and
four lines while everything larger stays drivable from a list of bits. Its body is
four stages --- [UART][uart-top], [loader][loader], [memories][mem],
[engine][shape] --- and three feedback loops, each written as a definition that
mentions a later name, each legal because [these are nets][uart-top] and each
broken by a register that was placed for other reasons a long time ago.
`ringWrite` unwraps a `Ring` into a bare tuple so the [memory][mem] can go on not
knowing what a trace record is. And the LED is a latch, a counter, and two total
functions that turn "is it working" into a truth table.

Then the tests run the whole thing, which nothing before now could. A program is
[framed][wire], serialised bit by bit onto a wire, received, [parsed][loader],
stored, triggered, [executed][exec], [traced][trace], drained, re-framed, and read
back with the project's own [receiver][rx] --- and it comes back as a REVISION
word and a `HALT` terminator with every field zero. The [introduction][intro]
described a rig that loads a program over a serial link and reports what happened
on the bus. Two hundred lines of test say it does.

What is still missing is small and absolute. `system` is generic in its domain: it
never says how fast its clock runs, never names a pin, and its four `IO` lanes are
plain signals that no bonded pad has ever seen. It is a machine with no address.
The next post gives it one --- fifty-one lines that tie a real oscillator to a real
domain, wrap `system` in [`espiPads`][io], and name every port on the package so
the place-and-route tool knows which ball of solder is `IO[0]`. Below that there
is no more Haskell.

[^applicative]: `LoaderIn <$> rxByte <*> txReady <*> halted <*> ringPtrO <*>
ringData` looks like effectful code and is nothing of the sort. `Signal dom` is an
applicative functor over *time*: `<$>` maps a pure function across every cycle of
a signal at once, and `<*>` applies a signal of functions to a signal of arguments
cycle by cycle. Feed a five-argument constructor into that machinery and what
comes back is `Signal dom LoaderIn` --- a record assembled fresh every cycle from
whatever its five sources carry at that moment. In hardware it is not an
assembly at all: a record is a bundle of wires, so the expression describes five
groups of wires being routed into one named bundle, and it costs exactly nothing.
The field projections on the other side (`txByte <$> lOut` and friends) are the
same move reversed --- selecting a sub-bundle out of a wider one. The [primer's][primer]
framing of `Signal` as *a stream you map over, not a value you inspect* is what
makes this style read naturally: you never write down a cycle, so you never have
to say which one.

[^knot]: A `where` block in Clash is a set of simultaneous equations over nets,
not a sequence of assignments, so mutual reference between its bindings is
ordinary. What is *not* ordinary --- and what the compiler will reject or the
simulator will hang on --- is a cycle with no state element in it, since that
describes a combinational loop: a value that must be known in order to compute
itself. All three loops here are broken by registers that were placed for
independent reasons. The fetch loop `pcO → instrRam → instrWord → engine → pcO`
crosses the instruction memory's [one-cycle read latency][mem], which is a
register inside the block RAM primitive and the reason the [engine][shape] has a
`Fetch` phase at all: it spends a cycle doing nothing so the word has time to
arrive. The trace loop `maybeRing → ringRam → ringData → loader → ringAddr →
ringRam` crosses the ring memory's read latency and the loader's own state. And
`halted` and `startO`, which appear above their definitions, are both projections
of registered outputs --- `haltedOut` from the engine's state register, `startOut`
from the loader's. The design was never arranged to make this module typecheck;
it typechecks because a machine that talks to memories has registers in all the
right places anyway.

[^blink]: `ledCnt` is an `Unsigned 26` incrementing once per clock, so its top bit
is high for 2²⁵ cycles and low for 2²⁵ cycles. At 100 MHz that is 33.5 million
cycles, about 336 ms each way --- roughly a 1.5 Hz blink, which is
about as slow as an LED can flash and still read as *waiting* rather than *broken*.
``msb (c `shiftL` 3)`` observes bit 22 instead, one eighth of the period: about 12
Hz, fast enough to read as activity and slow enough not to look solid. The whole
thing costs one twenty-six-bit counter, because both rates are taps on the same
count rather than two dividers --- which is also why the two patterns stay in phase
with each other, and why the test can compare them at a single value of the
counter.
