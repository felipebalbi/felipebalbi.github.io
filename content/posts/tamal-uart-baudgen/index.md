+++
title = "Tamal: The baud generator"
date = 2026-07-21T09:00:00
description = "Reading Tamal's baud generator end to end: why 100 MHz over a 16x oversample is the fractional 3.125 that no divider hits and no sane design reaches for a PLL, how a numerically-controlled oscillator turns a register, an adder, a comparator and a mux into an enable rather than a second clock, why subtracting the modulus instead of resetting keeps exactly 16 ticks per bit with no long-term drift, and how a one-line register feedback becomes the heartbeat the whole UART is gated on."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "uart"]
[extra]
math = true
+++

The [CRC unit][crc] we read last time was pure combinational logic --- a
truth table wearing a Haskell type, the same output for the same input,
no memory, no clock. It was the gentlest possible first block precisely
because time never entered into it. The [primer] closed by promising
that the next rung up was a block *with a clock inside it*, where
`Signal` stops being a footnote and becomes the whole substance of the
file. That block is the UART, Tamal's link to the host, and this is
where we start cashing the promise.

The UART is not one module but three small ones --- a baud generator, a
transmitter, and a receiver --- so the series takes them one at a time,
in the order they were built and the order they lean on one another.
First the baud generator: the smallest of the three, with no state
machine at all, just a clocked oscillator that produces the *heartbeat*
every other part of the UART marches to. It is also where we meet
`register`, the lone flip-flop that `mealy` --- the machine from the
primer --- is built out of. `mealy` itself waits for the transmitter;
here we meet its atom.

Like the CRC, the whole thing fits in a screenful.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the license header and the doc-comments, here is the module in
full:

```haskell
{-# LANGUAGE NumericUnderscores #-}

module Tamal.Uart.BaudGen
  ( oversampleTick
  ) where

import Clash.Prelude

oversampleTick ::
  forall baud dom.
  (HiddenClockResetEnable dom, KnownDomain dom, KnownNat baud) =>
  SNat baud ->
  Signal dom Bool
oversampleTick baud = tick
 where
  periodPs :: Integer
  periodPs = snatToNum (SNat @(DomainPeriod dom))
  fClk :: Unsigned 32
  fClk = fromInteger (1_000_000_000_000 `div` periodPs)
  inc :: Unsigned 32
  inc = fromInteger (16 * snatToNum baud)
  sums :: Signal dom (Unsigned 32)
  sums = (+ inc) <$> acc
  tick :: Signal dom Bool
  tick = (>= fClk) <$> sums
  acc :: Signal dom (Unsigned 32)
  acc = register 0 acc'
  acc' :: Signal dom (Unsigned 32)
  acc' = mux tick (subtract fClk <$> sums) sums
```

Roughly fifteen lines of logic under a familiar frame. The frame first,
then the problem it exists to solve, then the five wires that solve it.

## One exported name

The top of the file is the CRC module's opening beat, played again.
`module Tamal.Uart.BaudGen` names the module after its path on disk ---
`src/Tamal/Uart/BaudGen.hs`, a dot per directory --- and the parentheses
are the export list, the one door in the wall:

```haskell
module Tamal.Uart.BaudGen
  ( oversampleTick
  ) where
```

Only `oversampleTick` leaves the file --- and, as in the CRC unit, the
export list is only half the story of what stays private. There, the
real bit-twiddling lived in `step`, tucked into `crc8Update`'s `where`
block; here the entire mechanism --- `fClk`, `inc`, `sums`, `tick`,
`acc`, and `acc'` --- lives the same way, nested inside `oversampleTick`.
A `where` binding is not a name the export list politely declines to
mention. It is a name with no existence at all outside the one function
body it sits in: no other module could reach `acc`, or `step`, even if
it tried, because there is no syntax with which to spell it. The export
list guards the module's one top-level door; where the work is *written*
seals the rest by construction.

`import Clash.Prelude` is the same prelude swap the [CRC post][crc]
dwelt on --- the line that throws out ordinary Haskell's furniture and
moves in the hardware vocabulary, `Signal`, `Unsigned`, `register`,
`mux`, the versions of the everyday names that lower to gates. It is
still the line that says *compile me to hardware*; I will not re-derive
it here.

The one new piece of ceremony is the pragma up top:

```haskell
{-# LANGUAGE NumericUnderscores #-}
```

That is the switch that lets you write `1_000_000_000_000` with
underscores grouping the digits, the way you would write a hundred
billion on paper instead of squinting at a run of twelve zeros. It is
pure legibility, and you will want it the moment a clock frequency
shows up as a literal.

## The problem is 3.125

Everything strange about this module comes from one number, so it is
worth deriving before reading a line of the body.

Tamal runs on an Arty A7 at 100 MHz; the host link runs at
2 Mbaud, eight data bits, no parity, one stop bit. Divide
the two and a bit is

$$ \frac{100 \times 10^6}{2 \times 10^6} = 50 $$

exactly fifty system clocks wide. That division comes out whole, which
is a small mercy and also a trap, because it tempts you to think the
timing is easy. It is not, and the reason is the receiver.

A transmitter has it easy: it *owns* the clock, so it can hold each bit
for fifty cycles and call it a day. A receiver does not. The line
arriving from the host is asynchronous --- its bit edges fall wherever
they fall, with no relation to Tamal's clock --- and the receiver has to
*find* the middle of a bit it never scheduled. The standard cure, the
one every robust UART uses, is to **oversample**: sample the line many
times per bit, watch for the start edge, and then read each bit at its
center where it is most settled. Tamal oversamples sixteen times a bit.
Sixteen samples across a fifty-clock bit means a sample every

$$ \frac{50}{16} = 3.125 $$

clocks --- and *there* is the number. The bit period divides the clock
evenly; the thing we actually need, the 16x oversample period, does
not. You cannot count off 3.125 clocks on an integer counter. Some
ticks have to be three clocks apart and some four, arranged so the
long-run average lands exactly on 3.125. Producing that stream --- an
enable that fires, on average, every 3.125 clocks --- is the baud
generator's entire job.

## Not a divider, not a PLL

There are two obvious ways to make a 16x-oversample strobe, and the
module takes neither. Both refusals are worth understanding, because
the road not taken is where the design's philosophy shows.

The first idea is a plain **clock divider**: a counter that rolls over
every so many clocks and pulses. But a divider can only divide by whole
numbers. Divide 100 MHz by three and you get
33.3 MHz; by four, 25 MHz; the target,
16 × 2 Mbaud = 32 MHz, sits between two
integer divisors and no counter reaches it. You could special-case *this*
baud, hand-alternating threes and fours, but retune the link to some
other rate for signal-integrity debugging and the fraction turns
uglier still. A divider is the wrong tool for a fractional ratio.

The second idea is a **PLL**: ask the FPGA's clock hardware to
synthesize a real 32 MHz clock and run the UART on it. This
works, and it is exactly the move to resist, because it manufactures a
*second clock domain*. The moment the UART ticks on its own clock,
every byte crossing from the receiver into the 100 MHz
engine has to cross a clock boundary --- and clock-domain crossings are
where the sneaky bugs live: two-flop synchronizers, gray-coded
pointers, an asynchronous FIFO, a pile of hardware whose only purpose
is to survive two clocks that do not agree. More gates, more failure
modes, harder to verify, all to gain a strobe you can get for free
inside the clock you already have.

So the module produces neither a divided clock nor a synthesized one.
It produces an **enable**: a boolean, one per system cycle, that is
`True` on the cycles the rest of the UART should advance and `False` on
the rest. The receiver and transmitter stay clocked by the one
100 MHz clock the whole chip runs on; they simply *do
nothing* on the cycles the enable is low.

> The baud generator hands out a cadence, not a clock --- a `Bool` that
> says *now* a few times per bit, inside the single clock the rest of
> the design already lives in.

Hold that distinction; it is the whole reason the return type is what
it is.

## The type

```haskell
oversampleTick ::
  forall baud dom.
  (HiddenClockResetEnable dom, KnownDomain dom, KnownNat baud) =>
  SNat baud ->
  Signal dom Bool
```

Read it back to front and it confirms everything the last two sections
argued. The result is `Signal dom Bool` --- under the [primer]'s reading,
"a `Bool` that may change on every clock tick in domain `dom`," a wire
carrying one yes-or-no per cycle. Not a `Clock`, not a divided
`Signal dom Bit` masquerading as timing: an enable, stated in the type.

> A `Signal dom Bool` returned as timing is the type of an *enable* ---
> the one-domain alternative to handing back a second clock.

The single argument is `SNat baud`. An `SNat` is a **singleton** --- the
lone value that inhabits the type of a type-level number, so `baud`
lives in the type *and* can be passed as an argument without losing its
type-level identity.[^singleton] The caller writes
`oversampleTick (SNat @2_000_000)`, and the `2_000_000` travels as
something the compiler can both compute with and check. This is the
primer's "numbers can live in types" seen from the other side: here a
number is the input, but it is the type-level number made touchable.

The `forall baud dom.` in front is not decoration. It pulls the two
type variables into scope inside the body, so the definitions below can
mention `dom` and `baud` by name --- which they must, because one of them
is about to read a number straight out of the type `dom`.

That leaves the three constraints before the `=>`, each buying one
capability the body spends:

- **`HiddenClockResetEnable dom`** --- there is a clock (and reset, and
  enable) for `dom`, threaded implicitly so the code needn't pass it by
  hand. This is what `register`, further down, quietly draws on: a
  flip-flop needs a clock, and this is where it comes from.
- **`KnownDomain dom`** --- the domain's static configuration, its clock
  period among it, is known at compile time. We are about to read that
  period.
- **`KnownNat baud`** --- the baud number is a known type-level natural,
  so it can be reflected down to an ordinary value with `snatToNum`.

## Reading the clock out of the type

The first two `where` bindings compute a constant most designs would
have made you pass in --- and get wrong:

```haskell
periodPs = snatToNum (SNat @(DomainPeriod dom))
fClk     = fromInteger (1_000_000_000_000 `div` periodPs)
```

`DomainPeriod dom` is a type-level number: the clock period, in
picoseconds, baked into the definition of the domain itself. For
Tamal's `Dom100` it is 10,000 --- ten nanoseconds, a hundred megahertz.
`SNat @(DomainPeriod dom)` conjures the singleton for that type-level
number and `snatToNum` reflects it down to an `Integer`, so `periodPs`
is 10,000 computed by the compiler, not typed by a human.

`fClk` then turns a period into a frequency the obvious way: there are
$10^{12}$ picoseconds in a second, so the number of cycles per second
is $10^{12}$ divided by the period. For `Dom100`,
$10^{12} / 10^{4} = 10^{8}$, one hundred million --- `fClk = 100_000_000`,
the clock frequency in hertz, and the modulus the oscillator will count
against.

The point is what did *not* happen. Nobody passed the clock frequency
in as a parameter. It was read off the clock domain, at compile time,
from the same type that governs the actual flip-flops. There is no
second knob to keep in sync with the first, no way for a `100_000_000`
in one place to drift from a `Dom100` in another:

> The frequency isn't configuration you can get wrong; it's reflected
> out of the domain type, so the number and the clock can never
> disagree.

It is the primer's "the compiler is a wire-width checker," pointed at
time instead of width.

The third constant is the phase increment:

```haskell
inc = fromInteger (16 * snatToNum baud)
```

`snatToNum baud` reflects the baud rate down to a value, and
$16 \times 2{,}000{,}000 = 32{,}000{,}000$ is the target oversample rate in
hertz. Both `inc` and `fClk` are ordinary `Unsigned 32` --- thirty-two
plain wires with no arithmetic surprises --- and both are fixed at
compile time. All the interesting behaviour is in how they are used.

## The accumulator

Five bindings remain, and together they are a **numerically-controlled
oscillator** --- the name the module's own comment gives it.[^nco] The
idea is a single running total, the *phase accumulator*, that gains
`inc` every clock and fires a tick whenever it laps the modulus `fClk`.
Here it is, wire by wire:

```haskell
sums = (+ inc) <$> acc
tick = (>= fClk) <$> sums
acc  = register 0 acc'
acc' = mux tick (subtract fClk <$> sums) sums
```

Before unpacking the operators, read those four lines with the hardware
ceremony stripped away --- as ordinary arithmetic on *this cycle's*
numbers:

```text
sums = acc + inc
tick = sums >= fClk
acc  = acc' from the cycle before   (0 on the first cycle)
acc' = if tick then sums - fClk else sums
```

Line for line, that is the whole oscillator: add the increment to the
running phase, flag whether the sum has reached the modulus, carry the
register forward from the previous cycle, and set the next phase to
either the wrapped value or the untouched sum. Only the third line is
not plain arithmetic --- it is the one that *remembers*, and its `=` is
really a one-cycle delay rather than an equation. The Haskell computes
exactly these four things; it just has to state them for *every* cycle
at once, which is the job of the three unfamiliar operators --- `<$>`,
`register`, and `mux`. Take them in turn.

`register 0 acc'` is the flip-flop. `register i s` is Clash's primitive
memory element --- a bank of D flip-flops --- and it does exactly one
thing: it outputs `i` on the first cycle, and on every cycle after that
it outputs whatever `s` held *the cycle before*. A one-cycle delay with
a power-up value. This is the atom the primer's `mealy` is assembled
from; a Mealy machine is nothing but a `register` holding the state and
a pure function computing the next one. Here we use the register bare,
with no machine wrapped around it.

> `register` is the flip-flop itself --- the single clock-cycle delay
> that turns a loop of wires into a circuit that remembers.

`(+ inc) <$> acc` is where `<$>` enters. `<$>` is `fmap`, and a `Signal`
is a functor, so `f <$> s` applies the pure function `f` to every
sample of the stream `s`. `(+ inc)` is a curried adder with one operand
strapped to the constant increment (the primer's partial application,
made of gates), so `sums` reads "the accumulator plus `inc`, every
cycle" --- one adder, its output a fresh stream.

> A pure function `fmap`ped over a `Signal` is a combinational gate
> smeared across all of time: `sums` is one adder, re-evaluated every
> clock.

`(>= fClk) <$> sums` is the same move with a comparator: each cycle,
ask whether the candidate sum has reached or passed the modulus.[^section]
The result, `tick`, is a `Signal dom Bool` --- and it is both the module's
output *and*, one line down, a control wire.

That control wire is `mux`. `mux sel a b` is the 2:1 multiplexer on
signals: sample by sample, it takes `a` where `sel` is `True` and `b`
where `sel` is `False`. So

```haskell
acc' = mux tick (subtract fClk <$> sums) sums
```

reads "the next accumulator is `sums` with the modulus subtracted off
on a tick cycle, and plain `sums` otherwise." (`subtract fClk` is
`\x -> x - fClk`; `subtract` flips its arguments, which is precisely
the direction --- `sums` minus `fClk` --- we want.)

Now step back and notice something about how those four lines are
*written*. `sums` is defined above `acc`, but `sums` uses `acc`; `acc`
is defined in terms of `acc'`, which is defined below it and in terms of
`sums` and `tick`, which sit above. The definitions refer to each other
in a knot, and their order on the page is irrelevant. That is because
they are not steps in a procedure --- they are wires in a schematic, and
a schematic has no first line:

> A `where` block of `Signal` equations is a netlist, not a script; the
> order is yours to pick because there is no order.

Trace the knot as a circuit and it is a tidy little loop: the register
`acc` feeds the adder `sums`; the adder feeds the comparator `tick` and
a subtractor; the `mux` chooses between the subtractor's output and the
adder's, steered by `tick`; and its choice, `acc'`, feeds back into the
register. Register, adder, comparator, subtractor, mux, and a wire home
--- a phase accumulator with a compare-and-subtract wrap.

<figure class="ncod-fig" style="margin:2rem 0">
<svg class="ncod" viewBox="0 0 760 306" role="img" aria-labelledby="ncod-t ncod-d" xmlns="http://www.w3.org/2000/svg">
<title id="ncod-t">The baud generator's datapath as a block diagram</title>
<desc id="ncod-d">A register holding the phase acc feeds an adder, where the increment inc is added to form sums. The sum fans out to a comparator that tests it against the modulus f_clk (producing the oversample tick) and to a subtractor that computes sums minus f_clk. A multiplexer, steered by tick, passes sums when there is no tick and the subtracted value when there is; its output acc' is fed back into the register, forming the accumulator loop.</desc>
<style>
.ncod{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.ncod .blk{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.ncod .op{fill:var(--bg-main);stroke:var(--fg-main);stroke-width:2}
.ncod .mux{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.ncod .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.ncod .fb{stroke:var(--accent);stroke-width:2;fill:none}
.ncod .sel{stroke:var(--accent);stroke-width:2;fill:none;stroke-dasharray:5 4}
.ncod text{font-family:var(--sans)}
.ncod .lab{fill:var(--fg-main);font-size:13px}
.ncod .dim{fill:var(--fg-dim);font-size:12px}
.ncod .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.ncod .sigA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.ncod .opsym{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.ncod .ah{fill:var(--fg-main)}
.ncod .aha{fill:var(--accent)}
</style>
<defs>
<marker id="ncod-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="ncod-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<rect class="blk" x="60" y="104" width="92" height="52" rx="6"/>
<circle class="op" cx="206" cy="130" r="20"/>
<rect class="op" x="346" y="70" width="92" height="40" rx="4"/>
<rect class="op" x="346" y="176" width="92" height="40" rx="4"/>
<polygon class="mux" points="540,106 576,130 576,170 540,194"/>
<line class="wire" x1="152" y1="130" x2="185" y2="130" marker-end="url(#ncod-a)"/>
<line class="wire" x1="206" y1="72" x2="206" y2="109" marker-end="url(#ncod-a)"/>
<line class="wire" x1="226" y1="130" x2="286" y2="130"/>
<path class="wire" d="M286,130 V90 H345" marker-end="url(#ncod-a)"/>
<path class="wire" d="M286,130 V196 H345" marker-end="url(#ncod-a)"/>
<path class="wire" d="M286,130 H539" marker-end="url(#ncod-a)"/>
<line class="wire" x1="438" y1="90" x2="662" y2="90" marker-end="url(#ncod-a)"/>
<path class="wire" d="M439,196 H512 V178 H539" marker-end="url(#ncod-a)"/>
<path class="sel" d="M556,90 V116" marker-end="url(#ncod-aa)"/>
<line class="fb" x1="576" y1="150" x2="604" y2="150"/>
<path class="fb" d="M604,150 V272 H106 V157" marker-end="url(#ncod-aa)"/>
<circle class="ah" cx="286" cy="130" r="3"/>
<circle class="aha" cx="556" cy="90" r="3"/>
<text class="lab" x="106" y="98" text-anchor="middle">register</text>
<text class="opsym" x="106" y="128" text-anchor="middle">acc</text>
<text class="dim" x="106" y="146" text-anchor="middle">init 0</text>
<text class="opsym" x="206" y="136" text-anchor="middle">+</text>
<text class="sig" x="206" y="66" text-anchor="middle">inc</text>
<text class="sig" x="168" y="122" text-anchor="middle">acc</text>
<text class="sig" x="256" y="122" text-anchor="middle">sums</text>
<text class="dim" x="512" y="122" text-anchor="middle">keep</text>
<text class="opsym" x="392" y="95" text-anchor="middle">≥ fClk</text>
<text class="sig" x="668" y="94" text-anchor="start">tick</text>
<text class="dim" x="562" y="110" text-anchor="start">sel</text>
<text class="opsym" x="392" y="201" text-anchor="middle">− fClk</text>
<text class="dim" x="524" y="172" text-anchor="middle">wrap</text>
<text class="dim" x="558" y="206" text-anchor="middle">mux</text>
<text class="sigA" x="600" y="144" text-anchor="start">acc'</text>
<text class="dim" x="336" y="290" text-anchor="middle">acc' feeds back (the loop that remembers)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The same four equations as a datapath. The register holds the phase <code>acc</code>; every clock it is summed with the constant <code>inc</code> to make <code>sums</code>. A comparator tests <code>sums</code> against the modulus <code>f_clk</code>; that boolean <em>is</em> the oversample <code>tick</code>, and it also steers the <code>mux</code>. A subtractor forms the wrapped phase <code>sums − f_clk</code>. The <code>mux</code> <em>keeps</em> <code>sums</code> when there is no tick and takes the wrapped value when there is; its output <code>acc'</code> loops back into the register: the accent path that makes this sequential logic rather than a lone combinational knot.</figcaption>
</figure>

The recursion is legal for the same reason the circuit is
well-behaved: the loop passes *through* the register. `acc` at cycle
$n$ depends on `acc'` at cycle $n-1$, never on itself within a cycle,
because `register` puts a clock edge in the path. A loop of pure signal
functions with no register in it would be a combinational cycle --- the
one thing you may not build --- and the `register 0` is exactly what
saves it. The initial value `0` is the power-up phase; `Dom100` carries
no reset port, so that `0` is set by the flip-flops' initial state
rather than by a reset pulse, and the oscillator simply starts counting
from zero the instant the chip comes up.

## Wrap, don't reset

One detail in `acc'` is the difference between a baud generator that
holds its rate forever and one that slowly slides off it. On a tick,
the accumulator is set to `sums - fClk` --- the overshoot --- and **not**
to zero.

Because the accumulator climbs by `inc` and `inc` does not divide
`fClk`, it almost never lands exactly on the modulus; it *crosses* it,
overshooting by a little. That is why the test is `>=` and not `==`:
you are catching a crossing, not a coincidence. And the leftover above
the modulus is not noise to be discarded --- it is the fractional part of
a bit-time that an integer counter has no other way to hold. Subtract
the modulus and you carry that fraction into the next interval, where it
nudges the following tick a clock earlier. Zero the accumulator instead
and you would throw the fraction away every tick, and the small errors
would pile up until the ticks visibly drifted.

Keeping the remainder is what makes the average exact. Over one
2 Mbaud bit --- fifty clocks --- the accumulator gains

$$ 50 \times 32 \times 10^6 = 1.6 \times 10^9 = 16 \times \left(100 \times 10^6\right) $$

exactly sixteen times the modulus, so it laps exactly sixteen times and
fires exactly sixteen ticks. Not sixteen on average with a wandering
phase --- sixteen, every bit, indefinitely. The individual gaps between
ticks are three clocks or four, never the fractional 3.125 any single
time, but the carried remainder does the bookkeeping so their average
is 3.125 to the clock.

> Subtract the modulus, never zero the accumulator: the carried
> overshoot is the fractional bit-time the integer hardware can't
> otherwise represent, kept honest from one tick to the next.

The jitter this leaves --- a tick landing a single 100 MHz
clock early or late, twenty nanoseconds --- is nothing by the time
sixteen of them tile a five-hundred-nanosecond bit and the receiver
reads only the center. And one more property falls out for free:
because `inc` is smaller than `fClk` ($32$ million against $100$
million), the value left after a wrap is smaller than `inc`, so the
*next* cycle's sum cannot reach the modulus a second time. A tick is
therefore always a lone one-cycle pulse, never two back to back ---
exactly the clean single-cycle enable the transmitter and receiver want
to gate on.

<figure class="nco-fig" style="margin:2rem 0">
<svg class="nco" viewBox="0 0 760 300" role="img" aria-labelledby="nco-t nco-d" xmlns="http://www.w3.org/2000/svg">
<title id="nco-t">The NCO phase accumulator as a sawtooth</title>
<desc id="nco-d">A phase value climbs by a fixed increment each system clock, forming a rising ramp. Each time the ramp reaches or passes the modulus (f_clk, drawn as a dashed line) it wraps by subtracting the modulus and emits a one-cycle oversample tick, shown as a pulse below. The ramp overshoots the modulus slightly before wrapping; that overshoot is carried forward, which is why the ticks average one every 3.125 clocks with only single-clock jitter.</desc>
<style>
.nco{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.nco .axis{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.nco .mod{stroke:var(--accent);stroke-width:2;stroke-dasharray:6 5;fill:none}
.nco .ramp{stroke:var(--fg-main);stroke-width:2.5;fill:none;stroke-linejoin:round}
.nco .dot{fill:var(--fg-main)}
.nco .pulse{stroke:var(--accent);stroke-width:2.5;fill:none}
.nco .pbase{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.nco text{font-family:var(--sans)}
.nco .lab{fill:var(--fg-main);font-size:13px}
.nco .dim{fill:var(--fg-dim);font-size:13px}
.nco .acc{fill:var(--accent);font-size:13px}
.nco .ah{fill:var(--fg-main)}
.nco .aha{fill:var(--accent)}
</style>
<defs>
<marker id="nco-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="nco-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="aha"/></marker>
</defs>
<line class="axis" x1="90" y1="45" x2="90" y2="180"/>
<line class="axis" x1="90" y1="180" x2="655" y2="180"/>
<line class="mod" x1="90" y1="80" x2="650" y2="80"/>
<text class="acc" x="96" y="74">modulus = f_clk</text>
<polyline class="ramp" points="100,148 152,116 204,84 256,52 256,152 308,120 360,88 412,56 412,156 464,124 516,92 568,60 568,160 620,128"/>
<circle class="dot" cx="100" cy="148" r="3"/>
<circle class="dot" cx="152" cy="116" r="3"/>
<circle class="dot" cx="204" cy="84" r="3"/>
<circle class="dot" cx="256" cy="52" r="3"/>
<circle class="dot" cx="308" cy="120" r="3"/>
<circle class="dot" cx="360" cy="88" r="3"/>
<circle class="dot" cx="412" cy="56" r="3"/>
<circle class="dot" cx="464" cy="124" r="3"/>
<circle class="dot" cx="516" cy="92" r="3"/>
<circle class="dot" cx="568" cy="60" r="3"/>
<circle class="dot" cx="620" cy="128" r="3"/>
<line class="axis" x1="150" y1="150" x2="150" y2="118" marker-end="url(#nco-a)"/>
<text class="dim" x="158" y="140">+ inc each clock</text>
<text class="acc" x="583" y="150">keep the overshoot</text>
<line class="pbase" x1="90" y1="225" x2="640" y2="225"/>
<path class="pulse" d="M250,225 L250,205 L262,205 L262,225"/>
<path class="pulse" d="M406,225 L406,205 L418,205 L418,225"/>
<path class="pulse" d="M562,225 L562,205 L574,205 L574,225"/>
<text class="acc" x="596" y="214">tick</text>
<text class="dim" x="334" y="245" text-anchor="middle">3</text>
<text class="dim" x="490" y="245" text-anchor="middle">3</text>
<text class="dim" x="30" y="116" transform="rotate(-90 30 116)" text-anchor="middle">phase (accumulator)</text>
<text class="dim" x="360" y="272" text-anchor="middle">system clock cycles (100 MHz)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The baud generator as a numerically-controlled oscillator. The phase <code>acc</code> gains <code>inc = 16·baud</code> every system clock; when the candidate sum <code>sums</code> reaches the modulus <code>f_clk</code> the <code>mux</code> subtracts the modulus (keeping the overshoot rather than zeroing) and a one-cycle <code>tick</code> fires. Because the overshoot is carried, the gaps run three clocks and (one in eight) four, averaging exactly <em>50/16 = 3.125</em>, so a 50-clock bit always contains sixteen ticks. It is an enable inside <code>Dom100</code>, not a second clock.</figcaption>
</figure>

## The one test

The CRC unit came with a small battery of tests; the baud generator
gets one, because there is only one thing to check --- does it tick at
the right rate?

```haskell
baudTicks :: Int -> [Bool]
baudTicks n = sampleN n (oversampleTick (SNat @2_000_000) :: Signal Dom100 Bool)

testCase "oversample tick rate is 16x baud (~32 MHz avg)" $
  let n = 10000
      c = L.length (L.filter id (baudTicks n))
   in assertBool ("tick count = " <> show c <> ", expected ~3200") (abs (c - 3200) <= 2)
```

The whole apparatus is `sampleN`. `sampleN n sig` runs the circuit for
`n` cycles and hands back a list of its `n` outputs --- and that is the
line where all the primer's talk about `Signal` being "an endless
stream, one sample per cycle" pays off. A `Signal` really is, morally,
an infinite lazy list; `sampleN n` is `take n` on it --- that is, *take
the first `n` elements from the list*. So "simulate ten
thousand clock cycles of this oscillator" is not a call to a hardware
simulator with its process handshake and its waveform dump --- it is a
list traversal, evaluated by plain Haskell.

> Simulating ten thousand cycles is a `take 10000` on a lazy list ---
> that, and nothing more elaborate, is why the whole suite finishes
> before you blink.

The assertion just counts the `True`s. Over ten thousand cycles the
oscillator should fire $10{,}000 \times \tfrac{32}{100} = 3200$ times,
and the test allows $\pm 2$ for the partial teeth at the ends of the
window, where the count can miss catching a tick that straddles the
boundary. It is a blunt check, but it pins the one number that matters:
the tick rate is what it claims to be.

The subtler guarantee --- that the ticks land *evenly enough* for a
receiver to sample a transmitter's bits dead-center --- is not tested
here in isolation. It is tested two posts from now, by the full-UART
loopback, where a real fifty-clock bit must contain sixteen real ticks
or the receiver reads the wrong thing. This oscillator is one end of
that keystone test; we will meet the other end when the receiver is
built.

## What we read

Fifteen lines of logic, one exported name, and no state machine at all:
a phase accumulator --- a `register` fed back through an adder, a
comparator, a subtractor, and a `mux` --- that reads its own clock
frequency out of the domain type, climbs by a compile-time increment,
and wraps by subtracting the modulus so it keeps its overshoot and
fires exactly sixteen ticks per bit with no long-term drift, no PLL,
and no second clock domain. What it hands the rest of the UART is an
*enable*, not a *clock*: a `Bool` that says *now* sixteen times a bit,
inside the one 100 MHz clock the whole design already runs
on.

The [primer] promised that `Signal` would stop being a footnote in the
block posts, and this is the block where it does. `register`, `<$>`
over a `Signal`, `mux`, and a feedback loop tied through a flip-flop are
the sequential vocabulary the rest of the series is built from --- the
CRC's combinational world, now with a clock edge in it.

Next we start spending the heartbeat. The transmitter takes this tick
and a byte and drives a line high and low in an 8N1 frame --- and it is
where `mealy` finally climbs to the top of a module, exactly as the
primer said it would, lifting a pure `TxS -> … -> (TxS, …)` step into a
clocked machine. The oscillator counts; the transmitter is the first
thing to march to it.

[^singleton]: A **singleton** is the bridge between a type-level number
and a value you can pass around. `SNat n` is a type with exactly one
inhabitant, the value that stands for the type-level natural `n`, so
writing `SNat @2_000_000` hands a function a runtime token that still
carries `2000000` in its type. `snatToNum` walks it back down to an
ordinary `Integer` (or any `Num`), while the `KnownNat` constraint is
the compiler's proof that the number is statically known. It is the
round trip the primer's "numbers can live in types" implies but does
not spell out: up into the type to be checked, back down to a wire to
be used.

[^nco]: A **numerically-controlled oscillator** (NCO) --- the engine of
**direct digital synthesis** (DDS) --- comes from radio and signal
processing, where it synthesizes an arbitrary frequency from a single
reference clock by accumulating a *phase word* each cycle and using the
high bits as an angle into a sine table. Strip away the sine lookup and
keep only the accumulator and its overflow and you are left with a
fractional clock divider --- precisely this module. In a textbook DDS the
modulus is a power of two, so the "wrap" is just the natural carry-out
of a fixed-width adder and the output frequency is the tidy fraction
`inc / 2^N` of the clock; here the modulus is `fClk`, an arbitrary
number rather than a power of two, so the wrap is an explicit
compare-and-subtract --- but the principle, phase accumulates and the
overflow is your tick, is the same one that clocks software-defined
radios, benchtop function generators, and any FPGA that needs a
frequency its PLL cannot land on exactly.

[^section]: `(>= fClk)` is an operator **section** --- an infix operator in
parentheses with one operand already supplied, standing for a function
still waiting for the other. The side you fill is the side that stays
put: `(>= fClk)` is the *right* section `\x -> x >= fClk`, "has `x`
reached the modulus?", whereas `(fClk >=)` is the *left* section
`\x -> fClk >= x`, "is the modulus at least `x`?" --- the opposite
comparison, which would fire the tick on the wrong side of the
threshold. Which operand you park where is load-bearing in general; the
`(+ inc)` a line earlier got away with either order *only* because
addition commutes, so there `(+ inc)` and `(inc +)` genuinely denote the
same function. Order-sensitive operators grant no such reprieve --- `>=`,
`-`, and `div` all care about the side --- which is, incidentally, why the
wrap spells its subtraction `subtract fClk` rather than `(- fClk)`: the
latter would parse as unary negation, not "subtract `fClk`."
