+++
title = "Tamal: Out to the Pins"
date = 2026-08-13T09:00:00
draft = false
description = "The first module of the impure shell, and the line the whole serdes post was written toward: toDrive (o, oe) = if oe == 1 then Just o else Nothing, the (value, enable) pair collapsing into a Maybe Bit that writeToBiSignal turns into a real tri-state driver, Just o driving the pad and Nothing letting go of it. Tamal.Io is a hundred and four lines with two names on its door --- espiPads and alertSync --- and it is where a description of a drive stops being numbers and becomes a wire. The four IO lanes arrive as four scalar BiSignalIn arguments and leave as four scalar BiSignalOut results, deliberately not a Vec, because Clash fuses a scalar in with the scalar out derived from it into one inout port per lane and a Vec of BiSignals is an opaque bundle whose drive is silently dropped --- a read path with no driver, a pad that can only listen, verified from the emitted Verilog. Then readFromBiSignal taps each net back into ioIn, the three sidebands pass straight through, and alertSync catches the one asynchronous wire in the design with two flops that both power up high. The tests earn their own reading: espiPads driving and reading the same net ties a simulation knot that diverges, so the harnesses split it into two single-driver directions."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "io", "espi"]
+++

The [serdes post][serdes] ended with a promise and an apology. It had just
finished arguing that a `(value, enable)` pair is the whole of tri-state ---
three wire states carried in two bits --- and then it admitted that none of it
was real yet. Every `Lanes` the engine computes is *still just numbers*, it
said; a description of a drive, not a drive. Something has to carry those pairs
the last step out.

This is that something. `Tamal.Io` is a hundred and four lines, and it contains
the single line the last eight posts have been walking toward:

```haskell
toDrive (o, oe) = if oe == 1 then Just o else Nothing
```

That is the crossing. On the left of it, the engine's arithmetic --- two `Bit`s
in a tuple, computed by pure functions, checked by property tests, meaning
nothing to any wire. On the right, a `Maybe Bit`, which Clash knows how to turn
into an FPGA's output-enable and a pad that either drives or floats. Everything
the [introduction][intro] meant when it said *the pins are the hard part*
happens in this file, and the file is short enough to read in one sitting.

<!-- more -->

[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[uart-top]: https://balbi.sh/posts/tamal-uart-top/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[isa]: https://balbi.sh/posts/tamal-isa/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[serdes]: https://balbi.sh/posts/tamal-serdes/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## Two names on the door

The door is the shortest of the whole series bar one, and the imports beneath it
are shorter still:

```haskell
module Tamal.Io
  ( espiPads
  , alertSync
  ) where

import Clash.Prelude
import Tamal.Bus.Serdes (Lanes)
```

Two functions, no types. The [CRC's][crc] wall-with-a-door still holds, and this
wall has exactly two openings: the pad boundary itself, and the synchroniser
that guards the one asynchronous wire in the design. Everything else in the file
is a `where`-bound helper.

The import list is the more interesting half. `Clash.Prelude`, the [one line
that turns a Haskell file into a hardware description][crc], and then a single
name from a single sibling: `Lanes`, from [`Tamal.Bus.Serdes`][serdes]. That is
the whole dependency graph. `Tamal.Io` has never heard of the engine. It does
not import `Tamal.Engine`, does not know what a `BusOut` is, has no idea a
`phase` field exists or that a `PUT` is different from a `TAR`. It takes a
`Signal dom Lanes` --- four `(value, enable)` pairs per cycle --- and asks no
questions about where they came from.

That is the same discipline the [memories][mem] were held to, and for the same
reason. A `blockRamPow2` that imported the engine would be a memory that knows
what a trace record is; a pad boundary that imported the engine would be a pad
that knows what an instruction is. Neither needs to. The [shape post][shape]
observed that the engine's `busOut` is a *projection* and not a computation ---
the pins are read out of registers that were already settled --- and this module
is the consumer that projection was written for. It receives a settled drive and
makes it physical. Nothing more.

> A pad that imports the engine is a pad that knows what an instruction is.

## The line that makes it physical

Read `toDrive` again, because it is doing something more delicate than it looks:

```haskell
toDrive (o, oe) =
  if oe == 1
    then Just o
    else Nothing
```

The type is `Lane -> Maybe Bit`, which is `(Bit, Bit) -> Maybe Bit`. Two bits in,
one optional bit out. Count the inhabitants and the shape of the move becomes
clear. The input type has four values: `(0,0)`, `(1,0)`, `(0,1)`, `(1,1)`. The
output type has three: `Nothing`, `Just 0`, `Just 1`. `toDrive` is the function
that collapses four onto three, and it collapses exactly the pair the serdes post
said was redundant --- `(0,0)` and `(1,0)`, released with the value ignored ---
onto the single `Nothing`.

The [serdes post][serdes] called that redundancy *not waste, but the shape of the
thing*, and it was right about the encoding. Two bits are how you carry three
states through a register file, a `Vec`, a `bitCoerce`d record, all the machinery
that wants fixed widths. But the moment those bits reach a pad, the redundancy is
a liability: a pad has no use for the value of a lane it is not driving. `Maybe
Bit` is the type with no redundancy in it. Three states, three constructors, and
no way to write down a released lane that also carries a value, because there is
no such thing.

> The engine says the enable *may* veto the value. The pad type says the veto has
> already happened.

So `toDrive` is not a conversion so much as a *normalisation* --- it takes the
engine's convenient encoding and hands the synthesiser the canonical one. And
`Just`/`Nothing` is exactly the vocabulary Clash's bidirectional primitive
expects:

```haskell
writeToBiSignal ::
  (BitPack a, NFDataX a, …) =>
  BiSignalIn ds d (BitSize a) -> Signal d (Maybe a) -> BiSignalOut ds d (BitSize a)
```

`Just x` means *drive `x` onto this net this cycle*; `Nothing` means *let go*.
Which is why the body of `drive` is one line with nothing clever in it:

```haskell
drive :: Index 4 -> BiSignalIn 'PullUp dom 1 -> BiSignalOut 'PullUp dom 1
drive i padIn = writeToBiSignal padIn (toDrive <$> (laneSigs !! i))
```

`laneSigs = unbundle lanesOut` splits the one `Signal dom (Vec 4 Lane)` the
engine produces into four separate `Signal dom Lane`s, one per wire.[^unbundle]
`laneSigs !! i` picks lane `i`; `toDrive <$>` maps the collapse across time, cycle
by cycle; `writeToBiSignal` attaches the result to the net as a driver. Three
combinators and the `(value, enable)` pair has become a tri-state buffer.
[Post 3's][bus] `beatLanes` decided *what* to drive; `serializeX1` and `tarBeat`
and `hiZ` [built the drive][serdes]; this line performs it.

## A pad has two ends

The signature of `espiPads` is the longest thing in the file, and worth reading
as a shape before reading it as a list. Five plain `Signal` inputs, four pad
inputs, and a nine-element tuple out:

```haskell
espiPads ::
  forall dom.
  (HiddenClockResetEnable dom) =>
  Signal dom Lanes ->                 -- engine lanesOut
  Signal dom Bit ->                   -- csOut
  Signal dom Bit ->                   -- sckOut
  Signal dom Bit ->                   -- rstOut
  Signal dom Bit ->                   -- ALERT# (raw, async, active-low)
  BiSignalIn 'PullUp dom 1 ->         -- IO0 pad (read side)
  BiSignalIn 'PullUp dom 1 ->         -- IO1 pad (read side)
  BiSignalIn 'PullUp dom 1 ->         -- IO2 pad (read side)
  BiSignalIn 'PullUp dom 1 ->         -- IO3 pad (read side)
  ( BiSignalOut 'PullUp dom 1         -- IO0 pad (drive side)
  , BiSignalOut 'PullUp dom 1         -- IO1
  , BiSignalOut 'PullUp dom 1         -- IO2
  , BiSignalOut 'PullUp dom 1         -- IO3
  , Signal dom Bit                    -- CS#    pin out
  , Signal dom Bit                    -- SCK    pin out
  , Signal dom Bit                    -- RESET# pin out
  , Signal dom (Vec 4 Bit)            -- ioIn    -> BusIn.ioIn
  , Signal dom Bit                    -- alertIn -> BusIn.alertIn
  )
```

Four of those inputs and four of those outputs are the same four wires.

That is the thing to understand about `BiSignalIn` and `BiSignalOut`: they are
not two ports, they are two *views* of one port. A unidirectional signal has a
direction baked into where it appears --- an argument is an input, a result is an
output. A bidirectional wire has no such luxury, because at different moments it
is both. Clash's answer is to split the wire into its two roles and make you name
each: `BiSignalIn` is the net as something you may read, `BiSignalOut` is the net
as something you may drive. Hand `espiPads` the read view of `IO0` and it hands
you back the drive view of `IO0` --- and when Clash lowers the design to Verilog,
it recognises that the result was derived from the argument and fuses the pair
into one `inout` port.[^inout]

The `'PullUp` in `BiSignalIn 'PullUp dom 1` is a type-level tag for what the net
does when *nobody* drives it, and here that is the physically honest answer for
an eSPI bus: idle high. Every eSPI lane has a pull-up on the board, so a fully
released net floats up rather than drifting undefined --- which is why the
[turnaround][serdes] drives high before letting go, settling the line to the
level the pull-ups would have chosen anyway.[^pullup] The `1` is the width in
bits: each lane is one wire, so each pad is a `BiSignal … 1`. And the whole thing
carries `HiddenClockResetEnable dom` --- not because the pads need a clock, but
because `alertSync`, further down, holds two registers.

The last two results are the return path, and they are the fields the [shape
post][shape] introduced without saying where they came from:

```haskell
data BusIn = BusIn
  { instrWord :: BitVector 32
  , ioIn :: Vec 4 Bit
  , alertIn :: Bit
  , startIn :: Bool
  }
```

`ioIn` is *the four IO lanes sampled off the pads*, that post said, and `alertIn`
*the synchronised alert pin*. This module is the pads and the synchroniser. The
loop the [bus post][bus] ran --- drive a beat, sample the far end, shift the bit
in --- closes here, through eight signal wires and four bidirectional ones.

## Scalar, not Vec

The file's longest comment is a warning, and it is the kind of warning that only
gets written after somebody has lost an afternoon:

```haskell
-- Per-lane scalar BiSignals, deliberately not a Vec. […] Clash fuses a scalar
-- BiSignalIn argument with the scalar BiSignalOut result derived from it into
-- one inout port per lane --- but if the lanes are routed through a Vec of
-- BiSignals (zipWith/map/:> over BiSignalIn/BiSignalOut), Clash treats the
-- vector as an opaque bundle and drops the drive: the inout ports get a read
-- path but no tri-state driver (the write collapses to a dead net).
```

Look at what this forbids, because the forbidden version is the version a Haskell
programmer writes first. Four lanes, four identical operations, a `Vec 4` already
in hand from `unbundle` --- of course you write `zipWith writeToBiSignal pads
laneSigs` and get a `Vec 4 (BiSignalOut …)` back. It is one line instead of four,
it is obviously correct, and every other four-lane operation in the project is
written exactly that way. `serializeX1` maps over eight bits. `beatLanes` returns
a whole `Vec 4`. The [ISA][isa] packs and unpacks by field. Tamal is a project
that trusts `Vec`.

And the `Vec` version compiles, simulates correctly, and passes its tests. It
produces silicon that cannot drive a pin.

The failure is entirely in the lowering. Clash's `inout` support is a
*pattern-matching* feature, not a type-level one: the compiler looks for a
scalar `BiSignalIn` argument in the top entity's signature and a scalar
`BiSignalOut` result that was derived from it, and when it finds that pair it
emits one `inout` port and wires the tri-state driver to it. Wrap the pair in a
`Vec` and the pair is no longer visible --- the argument is now one opaque
aggregate and the result another, and the derivation relating them is buried
inside a bundle the fuser does not open. So it does the only safe thing it knows:
it keeps the read path, because reading an input is always valid, and it drops
the write, because it cannot see which port the write belongs to. The result is a
port that can be sampled and never driven. A pad that can only listen.

> The type system will not save you here. The `Vec` version has the same type as
> the scalar version and does something different in Verilog.

Which is why the fix is not a fix but a *shape*: the lanes stay scalar end to
end, four arguments in and four results out, spelled once each, and the tuple
that returns them is nine elements long because collapsing it would undo the
whole point.

```haskell
espiPads lanesOut csOut sckOut rstOut alert pad0 pad1 pad2 pad3 =
  ( drive 0 pad0
  , drive 1 pad1
  , drive 2 pad2
  , drive 3 pad3
  , …
```

Four calls, hand-written, with the index and the pad matched up by eye. It reads
like a regression rather than a design, and in a sense it is: this is the one
place in Tamal where the natural Haskell abstraction has to be *declined* because
the backend cannot see through it. The comment even records how the constraint
was established --- *verified from the emitted Verilog: each `io0`..`io3` must
carry its own tri-state driver* --- which is the only way such a thing can be
established. No test in the Haskell world distinguishes the two versions. You
have to go read the output.

This is the [introduction's][intro] thesis arriving as a concrete scar. The pins
are the hard part not because tri-state logic is conceptually difficult --- it is
two bits, and the serdes post spent five thousand words showing how simple ---
but because the pins are where your abstraction meets somebody else's compiler,
and the meeting has rules that live outside your type system. The engine got
eight posts of increasingly confident reasoning. The pad boundary gets a
hand-unrolled loop and a comment saying *check the Verilog*.

## Reading the net back

The drive side is four scalars. The read side, one line down, is a `Vec` again:

```haskell
ioIn =
  bundle
    ( readFromBiSignal pad0
        :> readFromBiSignal pad1
        :> readFromBiSignal pad2
        :> readFromBiSignal pad3
        :> Nil
    )
```

That looks like it should trip the same wire, and it does not --- for a reason
worth being precise about. The `Vec` here is not a vector of `BiSignal`s. Each
`readFromBiSignal padK` has already turned a pad into an ordinary `Signal dom
Bit`, an everyday unidirectional value, and *those* are what get consed together
and `bundle`d into a `Signal dom (Vec 4 Bit)`. The four pads are still touched
one at a time, by name; only their sampled results are gathered. The rule is
narrower than "no `Vec`s near pads" --- it is that the fusible pair, the
`BiSignalIn` argument and the `BiSignalOut` derived from it, must both be scalar
and visible. Once a pad has been read into a plain `Signal`, ordinary Haskell
resumes.

So the asymmetry in the source --- four hand-written `drive` calls, then a
four-element `:>` chain --- is not inconsistency. It is the boundary of the
constraint, drawn exactly where the constraint ends.

What comes out is `Signal dom (Vec 4 Bit)`, which is `BusIn.ioIn`, which is where
the [bus post's][bus] `sampleGet` was reading from all along:

```haskell
PendGet{} -> t{shifter = shifter t `shiftL` 1 .|. zeroExtend (pack (ioIn inp !! (1 :: Index 4)))}
```

Index `1`. `IO[1]`, the eSPI response lane, sampled at the rising edge, shifted
into the accumulator a bit at a time. That expression has been in the series
since the bus post, and this is the first time we can point at the wire it names:
`readFromBiSignal pad1`, the second of four, reading whatever the target is
driving while all four of our own enables are low.

Note also that the read is *combinational*. `readFromBiSignal` puts no register
in the path; what `ioIn` carries this cycle is the net's level this cycle. The
engine samples it inside `stepBusBeat`, at a phase of its own choosing, and the
[bus post][bus] chose phase 3 of the five-cycle beat --- comfortably after the
rising edge at the 2→3 boundary, comfortably before the next. The pad boundary
does not schedule anything. It presents a level; the engine decides when that
level means something.

## Three wires that pass straight through

Three of the nine results are pure pass-through:

```haskell
  , csOut
  , sckOut
  , rstOut
```

No transformation at all. The value that arrives is the value that leaves. And
that is correct, because the [shape post][shape] made a point of it: `csN`, `sck`
and `rstN` are *fields of the engine's `State`*, written by the transition
function and read out by `busOut`, which means they emerge from the engine
already registered --- settled outputs of flip-flops, not combinational functions
of this cycle's inputs. There is nothing left to do to them. Adding a register
here would only delay them by a cycle relative to the lanes, which are equally
registered, and skew the bus.

So why route them through `espiPads` at all, if the function does nothing to
them? Because the module's job is to be *the pad boundary*, singular. It is the
one place in the design where the outside world is named. A board shell that
wired `CS#` straight from `system` and `IO[0]` through `espiPads` would have the
pin list living in two places, and the next person to add a sideband would have
to guess which. Routing all seven eSPI pins through one function costs three
lines of nothing and buys a single answer to *where do the pins leave*. It is the
same argument the [UART top][uart-top] made for a module with no behaviour of its
own: the value is in the wiring being in one place.

## The one asynchronous wire

Everything else in Tamal is synchronous. The engine, the loader, the memories,
the serialiser: one clock, one domain, every signal generated inside it. There is
exactly one exception, and it is the only wire in the design that arrives from
outside with no relationship to our clock at all:

```haskell
alertSync ::
  (HiddenClockResetEnable dom) =>
  Signal dom Bit ->
  Signal dom Bit
alertSync alert = alert''
 where
  alert' = register high alert
  alert'' = register high alert'
```

Two registers in series, both initialised `high`. The [receiver][rx] built the
same thing for the same reason and spent a long section on why: an asynchronous
input will eventually violate a flip-flop's setup or hold window, that flop can
go **metastable** --- its output hovering at neither level for an unbounded
time --- and the second flop exists to give the first a whole clock period to
settle before anything downstream is allowed to look. It does not make
metastability impossible. It makes it improbable enough to ignore.[^mtbf]

Two details are specific to this wire rather than to synchronisers in general.

The first is `high`. Both flops power up at one, and one is *deasserted* ---
`ALERT#` is active-low, as the trailing hash says. The [receiver][rx] made the
same choice for the idle-high UART line, and the argument is identical: a
synchroniser that powers up at zero powers up believing the world is asserting
something. Here that would be worse than a spurious byte. [`WAIT_ON`][bus] blocks
the engine until the alert asserts, so a synchroniser that came out of reset low
would release a waiting engine on a signal that was never sent. Initialising the
flops to the wire's resting state is what makes the first two cycles of a run
*quiet* instead of a lie.

The second is that this synchroniser is unconditional. The [receiver's][rx] flops
were also clocked every cycle --- deliberately not gated by the oversample tick,
because a metastable strike does not wait for the tick --- and the same holds
here with nothing to gate it against. Two flops, every cycle, output lagging the
raw pin by exactly two. That lag is the cost, and for this signal it is free:
`ALERT#` is a request for attention with no timing requirement finer than the
engine's own beat, so two cycles at 100 MHz is twenty nanoseconds of latency on a
signal that exists to be waited on.

Worth noticing where the synchroniser *is*, too. It is in the pad module, not in
the engine and not in the board shell. That is the same placement argument the
[receiver][rx] made about `rxLine`: a wire is asynchronous because it comes from
a pin, so it should be synchronised at the pin, and every consumer downstream can
then treat it as ordinary. `BusIn.alertIn` is a clean synchronous bit because
`espiPads` cleaned it. The engine never learns that one of its inputs came from
outside the clock domain.

> Synchronise a wire where it enters, once, and no one downstream has to know it
> was ever dangerous.

## One lane, drawn

Four lanes, and they are four copies of one picture:

<figure class="iop-fig" style="margin:2rem 0">
<svg class="iop" viewBox="0 0 760 344" role="img" aria-labelledby="iop-t iop-d" xmlns="http://www.w3.org/2000/svg">
<title id="iop-t">One IO lane crossing from the engine's value-and-enable pair to a bidirectional pad</title>
<desc id="iop-d">A left-to-right path. A box labelled lanesOut index i, holding the pair o and oe, feeds a box labelled toDrive. From toDrive an accented wire labelled Just o or Nothing runs into a tri-state buffer drawn as a triangle, labelled writeToBiSignal, whose output-enable stub above it is also accented and labelled oe. The buffer output runs right to a junction dot and then to a square port labelled inout io underscore i. A pull-up box labelled PullUp, idle high, drops onto the junction from above. From the same junction a wire runs downward into a box labelled readFromBiSignal, and below that into a box labelled ioIn index i, annotated as feeding BusIn dot ioIn. The accented path is the enable path: it runs from the pair through toDrive to the buffer's output enable.</desc>
<style>
.iop{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.iop .bx{fill:var(--bg-dim);stroke:var(--fg-dim);stroke-width:1.6;rx:7}
.iop .bxa{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.4;rx:7}
.iop .pad{fill:var(--bg-main);stroke:var(--fg-dim);stroke-width:2}
.iop .buf{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.4}
.iop .w{stroke:var(--fg-dim);stroke-width:1.8;fill:none}
.iop .wa{stroke:var(--accent);stroke-width:2.4;fill:none}
.iop .m{fill:var(--fg-main);font-family:var(--mono);font-size:13.5px}
.iop .d{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.iop .a{fill:var(--accent);font-family:var(--mono);font-size:12px}
.iop .s{fill:var(--fg-dim);font-family:var(--sans);font-size:11.5px}
.iop .ah{fill:var(--fg-dim)}
.iop .aha{fill:var(--accent)}
</style>
<defs>
<marker id="iop-m" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="ah"/></marker>
<marker id="iop-ma" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- engine side -->
<rect class="bx" x="14" y="62" width="152" height="56" rx="7"/>
<text class="m" x="90" y="86" text-anchor="middle">lanesOut !! i</text>
<text class="d" x="90" y="105" text-anchor="middle">(o, oe)</text>
<line class="w" x1="166" y1="90" x2="222" y2="90" marker-end="url(#iop-m)"/>
<!-- toDrive -->
<rect class="bxa" x="232" y="62" width="120" height="56" rx="7"/>
<text class="m" x="292" y="96" text-anchor="middle">toDrive</text>
<!-- toDrive -> buffer, accented -->
<line class="wa" x1="352" y1="90" x2="426" y2="90" marker-end="url(#iop-ma)"/>
<text class="a" x="392" y="78" text-anchor="middle">Maybe Bit</text>
<!-- tri-state buffer -->
<path class="buf" d="M432,58 L432,122 L502,90 Z"/>
<line class="wa" x1="466" y1="42" x2="466" y2="73"/>
<text class="a" x="466" y="34" text-anchor="middle">oe</text>
<text class="s" x="467" y="142" text-anchor="middle">writeToBiSignal</text>
<!-- buffer -> pad -->
<line class="w" x1="502" y1="90" x2="612" y2="90" marker-end="url(#iop-m)"/>
<circle cx="556" cy="90" r="4.5" fill="var(--fg-dim)"/>
<!-- pull-up -->
<rect class="bx" x="500" y="10" width="112" height="34" rx="7"/>
<text class="d" x="556" y="32" text-anchor="middle">'PullUp</text>
<line class="w" x1="556" y1="44" x2="556" y2="86"/>
<!-- pad -->
<rect class="pad" x="618" y="64" width="128" height="52"/>
<text class="m" x="682" y="95" text-anchor="middle">inout io_i</text>
<!-- read tap -->
<line class="w" x1="556" y1="90" x2="556" y2="178" marker-end="url(#iop-m)"/>
<rect class="bx" x="462" y="186" width="188" height="52" rx="7"/>
<text class="m" x="556" y="217" text-anchor="middle">readFromBiSignal</text>
<line class="w" x1="556" y1="238" x2="556" y2="270" marker-end="url(#iop-m)"/>
<rect class="bx" x="462" y="278" width="188" height="52" rx="7"/>
<text class="m" x="556" y="309" text-anchor="middle">ioIn !! i</text>
<line class="w" x1="462" y1="304" x2="304" y2="304" marker-end="url(#iop-m)"/>
<text class="s" x="298" y="308" text-anchor="end">to BusIn.ioIn</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">One of the four <code>IO</code> lanes, from the engine's arithmetic to the bonded pin. The accented path is the enable: <code>toDrive</code> collapses <code>(o, oe)</code> into a <code>Maybe Bit</code>, and <code>writeToBiSignal</code> turns <code>Just o</code> into a driven level and <code>Nothing</code> into a released output. When nobody drives, the <code>'PullUp</code> holds the net at one. The same net is tapped combinationally by <code>readFromBiSignal</code> and returned as <code>ioIn !! i</code>, which is where the engine's <code>sampleGet</code> reads <code>IO[1]</code>. Clash fuses the scalar <code>BiSignalIn</code> argument and the scalar <code>BiSignalOut</code> result into the single <code>inout</code> port on the right — which is precisely why the four lanes may not be bundled into a <code>Vec</code>.</figcaption>
</figure>

Four copies of this, plus three wires that pass through and one that gets caught
by two flops, and the file is done.

## The tests, and the knot

The test module is twice the length of the module it tests, and about half of it
is comment explaining why the harnesses are shaped the way they are. That is
unusual for this project, and the explanation is worth the detour, because it is
about a hazard that only exists once signals become bidirectional.

Here is the problem. The obvious test for a pad boundary is a loopback: hand
`espiPads` a net, let it drive that net, read the same net back through the same
`espiPads`, and check that what you drove is what you sampled. The comment says
what happens if you try:

```haskell
-- NB: feeding espiPads's own drive-side 'outs' back into the 'padsIn' it also
-- reads makes the Clash BiSignal loopback diverge (self-drive + self-read knot).
```

It diverges --- hangs, rather than failing. And it diverges for a reason that is
not a bug: a bidirectional net's value is a function of *every* driver on it, so
to compute what the net carries, the simulator must first know what each driver
is putting there. If one of those drivers is derived from a read of the same net,
the value depends on itself. When the lane is driven the knot happens to untie,
because `Just o` does not consult the net. When the lane is tri-stated it does
not: the net's level is *only* the other drivers, so `Nothing` leaves the
simulator resolving a value defined in terms of itself, and Haskell's laziness
turns that into a loop rather than an error.

Which is a fair model of the world, if you think about it. A real net whose
driver is a combinational function of its own level is a real oscillator. The
simulator is not being difficult; it is refusing to invent a fixed point that
hardware would not have.

So the harnesses split the loopback into its two directions, each with a single
driver:

```haskell
idleNet :: Vec 4 (BiSignalIn 'PullUp Dom100 1)
idleNet = repeat (veryUnsafeToBiSignalIn (mempty :: BiSignalOut 'PullUp Dom100 1))
```

`idleNet` is a net nobody drives --- `mempty` is the empty set of drivers --- so
it reads as the pull-up's idle high. It is the stub you hand `espiPads` when you
want it to *not depend* on its read side. `simSample` gives `espiPads` a
DUT-driven net and makes it a pure consumer; `simDrive` lets `espiPads` drive
while handing it `idleNet` to read, and routes its drive-side results to a
*fresh* reader. Two linear paths instead of one circular one, and the physical
justification is one line of comment: *the engine never drives and samples one
lane in the same cycle*. The thing the harness cannot simulate is a thing the
design never does.

There is a second shim, and it is a small joke at the module's expense:

```haskell
espiPadsVec :: … Vec 4 (BiSignalIn 'PullUp dom 1) -> (Vec 4 (BiSignalOut 'PullUp dom 1), …)
espiPadsVec lanes cs sck rst alert pads = (d0 :> d1 :> d2 :> d3 :> Nil, …)
 where
  (d0, d1, d2, d3, csO, sckO, rstO, ioIn, alertO) =
    espiPads lanes cs sck rst alert (pads !! 0) (pads !! 1) (pads !! 2) (pads !! 3)
```

The tests wrap the scalar-per-lane interface back up into the `Vec` interface it
was so carefully not given --- because, as its comment notes, *bundling
`BiSignal`s in a `Vec` is fine in simulation; only Clash synthesis needs the
scalar-per-lane form for `inout` fusion*. The constraint we spent a section on is
a **backend** constraint, and the test bench, which never goes near the backend,
is free to ignore it. That is worth stating plainly, because it is exactly what
makes the scar dangerous: the version that breaks silicon behaves identically
everywhere a Haskell programmer can see.

The assertions themselves are short. Two oracles, one per direction:

```haskell
sampleOracle = map (maybe 1 id)
driveOracle  = map (\(o, oe) -> if oe == 1 then o else 1)
```

Read them together and they are the pull-up written twice. `sampleOracle` says a
lane reads the DUT's value when the DUT drives and `1` when it does not.
`driveOracle` says a lane carries `o` when our enable is high and `1` when it is
not. Both fall back to one, and both times the one is the pull-up. The
[Hedgehog][hedgehog] properties then sweep random drive patterns against each
model, and two `testCase`s pin the interesting corners: drive nothing and
everything reads high; drive lane zero only and read `0 :> 1 :> 1 :> 1 :> Nil`,
one driven low and three floating up.

One case reaches back a post and welds the two modules together:

```haskell
testCase "io: x1 beat0 drives IO[0] only, IO[1..3] hi-Z (independent OE)"
  $ let byte = 0b0111_1111 :: BitVector 8   -- MSB (IO[0]) = 0
        lane0 = serializeX1 byte !! (0 :: Index 8)
     in L.head (simDrive [lane0]) @?= (0 :> 1 :> 1 :> 1 :> Nil)
```

That is not a made-up `Lanes` value --- it is [`serializeX1`][serdes]'s beat zero,
the real drive the engine emits for the first bit of a `PUT`. The byte
`0b0111_1111` is chosen so the MSB is the *only* zero, so beat zero drives a low
onto `IO[0]` while the other three lanes are released and pull up to one. The
expected vector, `0 :> 1 :> 1 :> 1`, therefore distinguishes a driven zero from a
floating one by position alone. Change any part of the chain --- the serialiser's
bit order, `toDrive`'s enable test, the per-lane indexing in `drive` --- and the
zero lands on the wrong lane or does not land at all. It is the smallest test in
the file and the only one that checks the two modules agree about which wire is
which.

The synchroniser gets three of its own, and the first is the clearest statement
of what it does:

```haskell
simAlert [1, 1, 0, 0, 0, 1, 1] @?= [1, 1, 1, 1, 0, 0, 0]
```

The input drops on sample two; the output drops on sample four. Two cycles,
exactly, and the leading ones are the init-high flops showing through before the
pin's own value has walked the pipeline. A property test then holds the general
form against a three-line reference model --- `refAlert xs = take (length xs) (1
: 1 : xs)`, which is *the input with two idle-high samples in front of it* --- and
a second `testCase` checks that a single-cycle assertion survives the trip rather
than being swallowed. That last one matters: a synchroniser is allowed to delay a
pulse but not to eat it, and a one-cycle `ALERT#` that vanished on the way in
would strand a [`WAIT_ON`][bus] forever.

Even the sideband pass-through is tested, which sounds like testing that `id` is
`id` --- and functionally it is:

```haskell
simSide xs H.=== xs
```

But the property is not really about the function. It is about the *wiring*:
that `CS#`, `SCK` and `RESET#` come out in that order, uncrossed, unregistered
and unmolested. Swap two of the nine result positions in `espiPads` and this is
the test that goes red.

## What we read

A hundred and four lines, and the series has crossed out of arithmetic. `Tamal.Io`
imports nothing but `Clash.Prelude` and the `Lanes` type, and it does four things.
It turns each lane's `(value, enable)` pair into a `Maybe Bit` with `toDrive` ---
four states collapsed onto three, the redundancy of a released-but-valued lane
finally made unwritable --- and hands that to `writeToBiSignal`, which is the
tri-state driver the [serdes post][serdes] promised the pair would one day meet.
It taps each net back with `readFromBiSignal` and bundles the four into
`BusIn.ioIn`, the vector [`sampleGet`][bus] has been indexing since post three.
It passes `CS#`, `SCK` and `RESET#` through untouched, because they left the
engine [already registered][shape] and the only value in routing them here is
that the pin list then lives in one place. And it catches `ALERT#`, the single
asynchronous wire in the design, with two flops that both power up deasserted, so
a [`WAIT_ON`][bus] is never released by a signal nobody sent.

The scar in the middle of it is the one the [introduction][intro] predicted.
Four lanes that every instinct in the project says should be a `Vec 4` have to be
four scalar arguments and four scalar results, because Clash fuses `inout` ports
by matching a scalar `BiSignalIn` with the scalar `BiSignalOut` derived from it,
and a `Vec` of `BiSignal`s hides the match --- silently keeping the read path and
dropping the driver. The types do not distinguish the two versions. The
simulation does not distinguish them; the test bench cheerfully wraps the scalar
form back into a `Vec` because in simulation the `Vec` is fine. Only the emitted
Verilog distinguishes them, which is why the comment says to go and look. Eight
posts of the engine could be reasoned about from the source alone. The first post
of the shell cannot.

The pads exist now, and they are wired to nothing. `espiPads` takes a
`Signal dom Lanes` and produces four `inout` ports, but the design that computes
those lanes --- the engine, the loader that feeds it a program, the two block RAMs
it reads and writes, the UART that carries frames in and out --- is still a pile
of modules that have never been connected to one another. Every piece has been
read; none of them have been joined. The next post joins them: one function that
takes a UART line and the four sampled bits and gives back a serial line, a
drive, three sidebands and a status LED, with the [engine's pure `step`][shape]
lifted into a clocked signal at the centre of it and every module in the series
hanging off the result. Everything, wired.

[^inout]: In Verilog a port is `input`, `output`, or `inout`, and only the third
can be both driven and read by the module that declares it. Clash has no
`inout` in its type system --- `BiSignalIn` and `BiSignalOut` are ordinary
Haskell types --- so the mapping is done structurally at lowering time: the
compiler looks for a top-entity argument of type `BiSignalIn ds dom n` and a
result of type `BiSignalOut ds dom n` that was produced from that argument, and
emits a single `inout` port carrying both roles, with the `Maybe` given to
`writeToBiSignal` becoming the value and output-enable of a tri-state driver on
that port. `Nothing` lowers to the driver disabled, which in Verilog is an
assignment of `1'bz`. The recognition is syntactic in the sense that matters:
it depends on the shape the pair appears in, not on the types alone. Route the
pair through a `Vec` --- `zipWith writeToBiSignal pads lanes`, or even a `map`
over the results --- and the argument becomes one opaque aggregate and the
result another, with nothing at the port boundary relating lane `k` of one to
lane `k` of the other. Clash keeps what it can prove, the read, and discards
what it cannot place, the write. The emitted module still has four `inout`
ports and still compiles; each one is simply never driven, so the bus sits at
its pull-ups forever and every `GET` returns ones. Nothing in Haskell reports
this. The verification step recorded in the source comment --- read the Verilog,
confirm each of `io0`..`io3` carries its own tri-state driver --- is the whole
of the available evidence, which is why it is written down.

[^pullup]: `BiSignalIn`'s first parameter is a `BiSignalDefault`, a type-level
tag saying what the *simulator* should believe an undriven net carries:
`'PullUp` for one, `'PullDown` for zero, `'Floating` for undefined. It
generates no hardware --- a real pull-up is a resistor on the board or a pad
attribute in the constraints file, not a gate in your netlist --- so the tag is
a claim about the physical world that you are responsible for making true
elsewhere. `'PullUp` is the correct claim for eSPI: the bus idles high, every
lane has a pull-up, and a released net rises rather than drifting. It is also
what makes the test oracles read the way they do, both falling back to `1`
whenever nothing drives. Get the tag wrong and simulation quietly disagrees
with the board about what a floating wire means --- and since the whole point of
a tri-state bus is that wires spend most of their time floating, the
disagreement would not be a corner case.

[^mtbf]: The [receiver post][rx] worked this through at length, so only the
shape is worth repeating. A flip-flop sampled while its input is changing can
enter a metastable state, in which its output sits between the two valid levels
for an unbounded --- though exponentially improbable --- time. You cannot prevent
the first flop from going metastable, because the input is asynchronous and
asynchronous means *will eventually violate setup and hold*. What a second flop
buys is a full clock period of settling time before any logic downstream is
allowed to look, which pushes the mean time between failures from seconds to
geological. Two flops is the standard dose; safety-critical designs add a
third. `ALERT#` gets two, and the choice to run them unconditionally --- no
enable, every cycle --- matters for the same reason it mattered in the receiver:
a strike arrives when the outside world decides, not when your logic is ready
for it. The cost is a fixed two-cycle lag, and for a signal whose consumer is a
blocking wait, twenty nanoseconds is not a cost at all.

[^unbundle]: `unbundle :: Signal dom (Vec n a) -> Vec n (Signal dom a)` turns a
signal of vectors into a vector of signals, and `bundle` goes the other way.
Both are identities on the hardware --- a `Vec 4 Lane` was always four separate
pairs of wires, and neither combinator adds a gate --- so the pair exists purely
to let you choose which shape is convenient at each point in the source. Here
the choice is forced by what comes next: `writeToBiSignal` wants a
`Signal dom (Maybe Bit)` per pad, one pad at a time, so `unbundle`
splits the engine's single lane signal into four the moment it arrives.
`readFromBiSignal` then produces four separate `Signal dom Bit`s, and `bundle`
regathers them because `BusIn.ioIn` is a `Vec 4 Bit`. The module unbundles on
the way out and bundles on the way back, and both moves are free. The
[primer's][primer] point about `Signal` being a stream you map over rather than
a value you inspect is what makes this legal: `toDrive <$> laneSig` applies a
pure function at every cycle at once, which is exactly what a combinational
circuit is.
