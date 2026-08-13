+++
title = "Tamal: Binding to Silicon"
date = 2026-08-17T09:00:00
draft = true
description = "The last module, and the shortest: fifty-one lines that give the design an address. Tamal.Board.ArtyA7 is the thin pin-binding shell --- a topEntity whose every argument and result carries a type-level name, seven in and nine out, with io0 through io3 appearing on both sides because a BiSignalIn argument and the BiSignalOut derived from it fuse into one inout port; makeTopEntity turning those annotations into a Verilog port list; Dom100, thirty-nine lines away in Tamal.Domain, declaring the Arty's hundred-megahertz oscillator as a type so the baud generator can read the clock period back out of it; noReset = unsafeFromActiveHigh (pure False), which is the whole reset strategy of the design, because on an FPGA the bitstream loads every register's initial value and initState is where the engine already comes up; and a four-line body that wires system to espiPads in a knot each half of which the other half completes. Then the constraints file, where the pins actually are --- E3 for the clock, a Pmod for the whole eSPI bus, PULLUP TRUE on the four lanes, which is the physical fact the pad module's type-level PullUp tag has been asserting all along with nothing to check it. Below this there is no more Haskell. The series closes."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "board", "espi"]
+++

`system` is a machine with no address.

That is what the [last post][top] left. A hundred and fifteen lines that wire a
UART to a loader to two memories to an engine, and produce a serial line, a
four-lane drive, three sidebands and a blinking LED --- all of it generic in
`dom`, which is to say it never says how fast its clock runs, never names a
package pin, and has no opinion about which fabric it is going to live on. It is
a complete design and a homeless one.

This post gives it a home. `Tamal.Board.ArtyA7` is fifty-one lines, most of them
a type signature, and it does three things: it ties a real oscillator to a real
domain, it wraps `system` in [`espiPads`][io] so the four `IO` lanes become four
bidirectional package pins, and it gives every port a name that a place-and-route
tool can match against a constraints file. Below it there is Verilog, and below
that there is a bitstream, and below that there is a board.

It is also the last module. When this one is read, the stack is read.

<!-- more -->

[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[tx]: https://balbi.sh/posts/tamal-uart-tx/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[uart-top]: https://balbi.sh/posts/tamal-uart-top/
[cobs]: https://balbi.sh/posts/tamal-loader-cobs/
[loader]: https://balbi.sh/posts/tamal-loader/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[exec]: https://balbi.sh/posts/tamal-engine-exec/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[isa]: https://balbi.sh/posts/tamal-isa/
[regfile]: https://balbi.sh/posts/tamal-regfile/
[compute]: https://balbi.sh/posts/tamal-compute/
[serdes]: https://balbi.sh/posts/tamal-serdes/
[wire]: https://balbi.sh/posts/tamal-wire/
[trace]: https://balbi.sh/posts/tamal-trace/
[io]: https://balbi.sh/posts/tamal-io/
[top]: https://balbi.sh/posts/tamal-top/
[intro]: https://balbi.sh/posts/tamal-introducing/

## The one module with no door

Twenty-one posts ago the [CRC][crc] gave us an image that every module since has
been read through: a module is a wall with a door in it, and the export list is
the door. This one has no export list:

```haskell
module Tamal.Board.ArtyA7 where

import Clash.Annotations.TH
import Clash.Prelude

import Tamal.Domain (Dom100)
import Tamal.Io (espiPads)
import Tamal.Top (system)
```

`module … where`, with nothing in parentheses, exports everything. After twenty
modules of carefully rationed public surface --- the [register file][regfile]
exporting four names and hiding `regIndex`, the [ISA][isa] exporting its
constructors but not its field widths, the [pad boundary][io] exporting two
functions out of six definitions --- the last one gives up on the idea entirely.

And that is right, because at this level the export list is no longer the door.
There is only one definition in the file, `topEntity`, and its visibility to
other Haskell modules is beside the point: nothing imports a board shell. What
*does* matter is which of its arguments and results become ports on a chip, and
that is decided by something else entirely, on the last line of the file. The
wall is still there. The door has just moved out of Haskell.

## The names are the ports

Here is the signature, which is four-fifths of the module:

```haskell
topEntity ::
  "clk" ::: Clock Dom100 ->
  "uart_rx" ::: Signal Dom100 Bit ->
  "io0" ::: BiSignalIn 'PullUp Dom100 1 ->
  "io1" ::: BiSignalIn 'PullUp Dom100 1 ->
  "io2" ::: BiSignalIn 'PullUp Dom100 1 ->
  "io3" ::: BiSignalIn 'PullUp Dom100 1 ->
  "alert_n" ::: Signal Dom100 Bit ->
  ( "io0" ::: BiSignalOut 'PullUp Dom100 1
  , "io1" ::: BiSignalOut 'PullUp Dom100 1
  , "io2" ::: BiSignalOut 'PullUp Dom100 1
  , "io3" ::: BiSignalOut 'PullUp Dom100 1
  , "uart_tx" ::: Signal Dom100 Bit
  , "cs_n" ::: Signal Dom100 Bit
  , "sck" ::: Signal Dom100 Bit
  , "reset_n" ::: Signal Dom100 Bit
  , "led" ::: Signal Dom100 Bit
  )
```

`a ::: t` is a type-level annotation: a string literal attached to a type, which
at the Haskell level means nothing at all --- `"clk" ::: Clock Dom100` *is*
`Clock Dom100`, and every function that touches it behaves as though the label
were not there. The label exists for one consumer, and it is the last line of
the file:

```haskell
makeTopEntity 'topEntity
```

Template Haskell. At compile time, `makeTopEntity` reads the signature of the
named function, walks the argument and result types collecting those string
annotations, and generates the `Synthesize` annotation that tells Clash what to
call each port in the emitted Verilog.[^tt] The result is that this signature is
not documentation of the interface; it *is* the interface, and the names in it
are the names a synthesis tool will look for.

Which makes the repetition in the middle of it the most interesting thing on the
page. `io0` appears twice --- once as a `BiSignalIn` argument and once as a
`BiSignalOut` result --- and so do `io1`, `io2` and `io3`. Seven arguments, nine
results, and four of the names occur in both lists.

That is the [pad post's][io] fusion, written out where you can see it. Clash
matches a scalar `BiSignalIn` argument with the scalar `BiSignalOut` result
derived from it and emits **one** `inout` port carrying both roles, so the two
occurrences of `"io0"` are not two ports with a name collision but one port
described from both ends. Count what actually appears on the package and it is
thirteen: `clk`, `uart_rx`, `alert_n`, four `inout` lanes, `uart_tx`, `cs_n`,
`sck`, `reset_n`, `led`.

And the constraint that dominated the [pad post][io] is enforced again here, at
the very top, for the same reason and with the same shape. The lanes are four
scalar arguments and four scalar results, spelled out one at a time, because a
`Vec 4` of `BiSignal`s does not fuse --- it lowers to a plain input, keeping the
read path and silently dropping the tri-state driver. The module's own comment
repeats the warning verbatim rather than referring you to `Tamal.Io`, which is
the correct instinct: a hazard that produces a working simulation and a dead pin
is worth stating twice.

> The one abstraction this project refuses to make, it refuses to make in two
> files, in the same words.

## A hundred megahertz, written down once

`Dom100` comes from `Tamal.Domain`, which is thirty-nine lines and almost
entirely comment:

```haskell
createDomain
  vSystem
    { vName = "Dom100"
    , vPeriod = hzToPeriod 100_000_000
    }
```

`createDomain` is Template Haskell again, and what it generates is a *type* ---
`Dom100` --- together with the `KnownDomain` instance that carries its properties:
the clock period, the active edge, whether the reset is synchronous or
asynchronous and which polarity, and whether registers have defined power-up
values. `vSystem` is the stock set of answers and this record update changes two
of them: the name, and the period, which `hzToPeriod` converts from 100 MHz into
the picoseconds Clash keeps internally --- ten thousand of them, which is ten
nanoseconds.

This is where a loop the series opened in its fourth post closes. The [baud
generator][baudgen] had a problem: it needed to know the system clock frequency
in order to divide it, and it refused to be told, taking it instead from the
domain:

```haskell
KnownDomain dom => …   -- and then, inside: DomainPeriod dom
```

That post called it *reading the clock out of the type*, and observed that the
awkward `3.125` at the heart of the accumulator --- 100 MHz over 2 Mbaud over
sixteen oversamples --- was not a constant anybody wrote but a number that fell
out of two facts stated far apart. This line is the first of those two facts, and
it is the only place in the entire design where a frequency is written down. Move
Tamal to a 50 MHz part, change `hzToPeriod 100_000_000` here, and the baud
generator's accumulator increment changes to match without a single other edit.

The other domain in the file is a promise being kept quietly:

```haskell
createDomain
  vSystem
    { vName = "DomInput50"
    , vPeriod = hzToPeriod 50_000_000
    }
```

`DomInput50` is not used by the Arty. It is the 50 MHz oscillator on a Terasic
Cyclone V board, and it exists because a second board shell --- `Tamal.Board.CycloneV`
--- feeds it into an Altera PLL that multiplies it up to the same `Dom100` the
Arty gets for free.[^pll] Two boards, two oscillators, one system domain, and
`system` itself never learns which it is running on. That is the payoff of the
[last post's][top] generic `dom`, and it is why the board shells are shells: they
are the only files in the project that know what silicon is.

## The reset that is not there

Four words in the `where` clause are the design's entire reset strategy:

```haskell
 where
  noReset = unsafeFromActiveHigh (pure False)
```

`pure False` is a signal that is `False` on every cycle for ever.
`unsafeFromActiveHigh` reinterprets a `Bool` signal as a `Reset` under the
active-high convention, so this is a reset that is never asserted. Hand it to
`withClockResetEnable` and Clash, seeing that the reset network is constantly
inactive, optimises it away entirely and emits **no reset port**. Look at the
signature again: there is no `rst` among the thirteen pins.

A design with no reset sounds alarming until you ask what a reset is *for*. It
is for putting registers into a known state before the logic starts. On an FPGA
that job is already done, and done more thoroughly than a reset network could do
it: configuration loads the bitstream, and the bitstream contains an initial
value for every flip-flop and every block RAM word in the design. The part comes
out of configuration already in its initial state.[^init]

And the series has been quietly relying on that for a long time. The
[engine's][shape] `initState` is the eight-phase machine idling at `Idle` with
its pins at `hiZ` --- a *value*, not a sequence of reset assignments, and the
shape post called `softInit` "the reset a machine with no reset port performs in
its own datapath". The [memories][mem] are `blockRamPow2` over a vector of zeros.
The [pad module's][io] `alertSync` flops power up `high`, which is `ALERT#`
deasserted. The [transmitter's][tx] `initTx` idles the line high. Every one of
those is an initial value that a reset would otherwise have had to establish, and
every one of them was chosen, post by post, as though the design had no reset ---
because it does not, and this line is where that becomes true.

The `unsafe` in `unsafeFromActiveHigh` is a real warning, though not one that
applies here. It exists because turning an arbitrary `Bool` signal into a `Reset`
lets you build a reset that is itself generated by clocked logic, and releasing
such a reset asynchronously across a large fabric is a classic way to have half
your design come out of reset one cycle before the other half. Tying it to a
constant sidesteps the whole hazard: there is no release, because there was no
assertion.

## Four lines and a knot

The body:

```haskell
topEntity clk uartRx io0 io1 io2 io3 alertN =
  withClockResetEnable clk noReset enableGen
    $ let (txLine, lanesO, csO, sckO, rstO, ledOut) = system uartRx ioIn alertIn
          (d0, d1, d2, d3, csPin, sckPin, rstPin, ioIn, alertIn) =
            espiPads lanesO csO sckO rstO alertN io0 io1 io2 io3
       in (d0, d1, d2, d3, txLine, csPin, sckPin, rstPin, ledOut)
```

`withClockResetEnable clk noReset enableGen` supplies the three implicit
arguments that every `HiddenClockResetEnable dom` constraint in the project has
been carrying: the clock from the `clk` port, the reset that is never asserted,
and `enableGen`, an enable that is always on. Everything inside the `$` ---
`system`, and through it the [UART][uart-top], the [loader][loader], both
[memories][mem], the [engine][shape], and `espiPads` with its two registers ---
runs on those three.

Then two `let` bindings, and they are mutually recursive. `system` is given
`ioIn` and `alertIn`, which are results of `espiPads`. `espiPads` is given
`lanesO`, `csO`, `sckO` and `rstO`, which are results of `system`. Each binding's
inputs are the other's outputs, and the two are written in the order that makes
them read strangest.

This is the [elaboration-not-evaluation][uart-top] point one last time, and the
last instance is the most physical. What the two lines describe is not a circular
computation but the shape of a bus: the engine drives the lanes, the pads make
that drive real, the far end drives back, the pads sample it, and the engine
reads what it sampled. That is a loop in the world, so of course it is a loop in
the source.

It is also a loop with a register in it, which is what makes it legal rather than
oscillatory. Trace it: `lanesO` is a projection of `busOut`, and the [shape
post][shape] made a point of `busOut` being a *projection and not a computation*
--- the pin drives live in the engine's `State` and come out of a flip-flop.
`espiPads` turns them into a drive combinationally; `readFromBiSignal` samples
the net combinationally; and `ioIn` then enters `BusIn`, where the engine's own
state register catches it. One register, in the right place, and it is there
because the [engine post][shape] registered its pin outputs for timing reasons,
not to make this line typecheck.

The last line reorders nine values into the nine-element result tuple, and the
reordering is not cosmetic: it is what puts each value at the position whose
`:::` annotation names the port it belongs on. `d0` is the drive side of `io0`
and must land in the slot labelled `"io0"`. This is the least interesting line in
the module and among the easiest to get catastrophically wrong, which is a fair
description of pin binding in general.

<figure class="art-fig" style="margin:2rem 0">
<svg class="art" viewBox="0 0 880 340" role="img" aria-labelledby="art-t art-d" xmlns="http://www.w3.org/2000/svg">
<title id="art-t">The Arty board shell wrapping system and espiPads, with named package pins</title>
<desc id="art-d">A large box labelled topEntity, Arty A7-100T, tagged Dom100, contains two blocks: system on the left and espiPads on the right. Between them, lanesOut with the cs, sck and rst sidebands runs left to right, and ioIn with alertIn runs right to left. Entering the big box from the left are clk on pin E3 and uart underscore rx on pin A9; uart underscore tx on pin D10 leaves to the left, and led on pin H5 leaves along the bottom. On the right, espiPads emits a BiSignalOut wire and receives a BiSignalIn wire, and the two meet at a junction and continue as a single accented line into a package pin block labelled io0 to io3, inout. Below it, the cs underscore n, sck and reset underscore n sidebands leave and alert underscore n enters.</desc>
<style>
.art{max-width:880px;width:100%;height:auto;display:block;margin:0 auto}
.art .bx{fill:var(--bg-dim);stroke:var(--fg-dim);stroke-width:1.6}
.art .shell{fill:none;stroke:var(--fg-dim);stroke-width:1.8;stroke-dasharray:7 5}
.art .pad{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.6}
.art .w{stroke:var(--fg-dim);stroke-width:1.7;fill:none}
.art .wa{stroke:var(--accent);stroke-width:2.4;fill:none}
.art .m{fill:var(--fg-main);font-family:var(--mono);font-size:13.5px}
.art .ma{fill:var(--accent);font-family:var(--mono);font-size:13px}
.art .l{fill:var(--fg-dim);font-family:var(--mono);font-size:10px}
.art .la{fill:var(--accent);font-family:var(--mono);font-size:10px}
.art .s{fill:var(--fg-dim);font-family:var(--sans);font-size:11px}
.art .ah{fill:var(--fg-dim)}
.art .aha{fill:var(--accent)}
</style>
<defs>
<marker id="art-a" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="ah"/></marker>
<marker id="art-aa" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L8,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- shell -->
<rect class="shell" x="150" y="52" width="460" height="278" rx="10"/>
<text class="s" x="160" y="72">topEntity — Arty A7-100T</text>
<text class="la" x="600" y="72" text-anchor="end">Dom100</text>
<!-- inner blocks -->
<rect class="bx" x="180" y="96" width="180" height="128" rx="7"/>
<text class="m" x="270" y="166" text-anchor="middle">system</text>
<rect class="bx" x="420" y="96" width="160" height="128" rx="7"/>
<text class="m" x="500" y="166" text-anchor="middle">espiPads</text>
<!-- left pins -->
<text class="l" x="88" y="92" text-anchor="end">clk (E3)</text>
<line class="w" x1="96" y1="88" x2="144" y2="88" marker-end="url(#art-a)"/>
<text class="l" x="88" y="130" text-anchor="end">uart_rx (A9)</text>
<line class="w" x1="96" y1="126" x2="174" y2="126" marker-end="url(#art-a)"/>
<text class="l" x="88" y="194" text-anchor="end">uart_tx (D10)</text>
<line class="w" x1="180" y1="190" x2="100" y2="190" marker-end="url(#art-a)"/>
<!-- system <-> espiPads -->
<text class="l" x="390" y="88" text-anchor="middle">lanesOut, cs, sck, rst</text>
<line class="w" x1="360" y1="126" x2="414" y2="126" marker-end="url(#art-a)"/>
<line class="w" x1="420" y1="158" x2="366" y2="158" marker-end="url(#art-a)"/>
<text class="l" x="390" y="180" text-anchor="middle">ioIn</text>
<text class="l" x="390" y="194" text-anchor="middle">alertIn</text>
<!-- the fusion -->
<text class="la" x="622" y="108">BiSignalOut</text>
<path class="wa" d="M580,120 L666,120 L666,164 L580,164" />
<text class="la" x="622" y="184">BiSignalIn</text>
<line class="wa" x1="666" y1="142" x2="718" y2="142" marker-end="url(#art-aa)"/>
<circle cx="666" cy="142" r="4.5" fill="var(--accent)"/>
<rect class="pad" x="724" y="114" width="100" height="56" rx="4"/>
<text class="ma" x="774" y="139" text-anchor="middle">io0..io3</text>
<text class="la" x="774" y="157" text-anchor="middle">inout</text>
<!-- right pins -->
<line class="w" x1="580" y1="198" x2="690" y2="198" marker-end="url(#art-a)"/>
<text class="l" x="696" y="202">cs_n, sck, reset_n</text>
<line class="w" x1="690" y1="218" x2="586" y2="218" marker-end="url(#art-a)"/>
<text class="l" x="696" y="222">alert_n (K16)</text>
<!-- led -->
<line class="w" x1="270" y1="224" x2="270" y2="292" />
<line class="w" x1="270" y1="292" x2="690" y2="292" marker-end="url(#art-a)"/>
<text class="l" x="696" y="296">led (H5)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">Fifty-one lines, drawn. The shell supplies <code>Dom100</code>, a reset that is never asserted and an enable that is always on, and inside it exactly two things happen: <a href="https://balbi.sh/posts/tamal-top/"><code>system</code></a> computes and <a href="https://balbi.sh/posts/tamal-io/"><code>espiPads</code></a> makes it physical. The accented detail on the right is the whole reason the lanes are written out one at a time — a <code>BiSignalIn</code> argument and the <code>BiSignalOut</code> result derived from it are the same wire seen from two sides, and Clash fuses the pair into one <code>inout</code> port per lane. Wrap them in a <code>Vec</code> and the fusion does not happen. Pin numbers are from the Arty A7-100T constraints file, not from the Haskell.</figcaption>
</figure>

## Where the pins actually are

The Haskell says `io0`. It does not say *which ball of solder*. That lives in a
Vivado constraints file, and it is worth reading because it is the first place in
this entire series where the design touches something physical:

```tcl
set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports { clk }]
create_clock -name sys_clk -period 10.000 [get_ports { clk }]

set_property -dict { PACKAGE_PIN A9  IOSTANDARD LVCMOS33 } [get_ports { uart_rx }]
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { uart_tx }]

set_property -dict { PACKAGE_PIN G13 IOSTANDARD LVCMOS33 PULLUP TRUE } [get_ports { io0 }]
set_property -dict { PACKAGE_PIN B11 IOSTANDARD LVCMOS33 PULLUP TRUE } [get_ports { io1 }]
set_property -dict { PACKAGE_PIN A11 IOSTANDARD LVCMOS33 PULLUP TRUE } [get_ports { io2 }]
set_property -dict { PACKAGE_PIN D12 IOSTANDARD LVCMOS33 PULLUP TRUE } [get_ports { io3 }]

set_property -dict { PACKAGE_PIN D13 IOSTANDARD LVCMOS33 } [get_ports { sck }]
set_property -dict { PACKAGE_PIN B18 IOSTANDARD LVCMOS33 } [get_ports { cs_n }]
set_property -dict { PACKAGE_PIN A18 IOSTANDARD LVCMOS33 } [get_ports { reset_n }]
set_property -dict { PACKAGE_PIN K16 IOSTANDARD LVCMOS33 PULLUP TRUE } [get_ports { alert_n }]

set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports { led }]
```

Every `get_ports` name in that file is a string from the `:::` annotations three
files away. There is no compiler between them. Rename `"cs_n"` to `"csn"` in the
Haskell and this file goes on referring to a port that no longer exists, and you
find out during implementation, or --- worse --- you do not, because the tool
warned and you did not read the log.

Three things in it are worth pausing on.

The **clock**: `E3`, and `create_clock … -period 10.000`. Ten nanoseconds. That
number is `hzToPeriod 100_000_000` written a second time, in a second language,
with nothing checking that the two agree. Say 20.000 here and the design still
builds; timing analysis simply grades it against a clock the board does not
supply, and the failure --- a part that meets timing on paper and misbehaves in the
lab --- is exactly the kind that costs a weekend.

The **lanes**: `PULLUP TRUE`, on all four. That is the [pad post's][io]
`BiSignalIn 'PullUp` claim finally being made true. The type-level tag told the
*simulator* that an undriven net reads high, and the post noted that it generates
no hardware and is a promise you have to keep somewhere else. Here is somewhere
else. Two files, one physical fact, asserted in each and cross-checked by nobody
--- and if they disagree, the simulation is confident and wrong, which is the
worst way to be wrong about a tri-state bus. `alert_n` gets a pull-up too, for the
same reason: an active-low sideband nobody is driving must read deasserted, not
float into the [synchroniser][io].

And the **layout**: `io0..io3` on `G13/B11/A11/D12`, `sck`/`cs_n`/`reset_n` on
`D13/B18/A18`. Those are the two rows of a single Pmod header, and the comment in
the file explains the choice --- the whole eSPI bus on one connector, with ground
and 3V3 on the header's shared pins, so the rig reaches a device under test
through *one cable*. It is the least sophisticated decision in the entire project
and possibly the one that will matter most on a bench.

## What we read

Fifty-one lines, plus thirty-nine for the domain, and the design has an address.
`Tamal.Board.ArtyA7` has no export list because at this level the door is not the
export list --- it is the port list, and the port list is written as type-level
string annotations on `topEntity`'s arguments and results, turned into a
`Synthesize` annotation by `makeTopEntity` on the file's last line. Seven
arguments, nine results, thirteen ports, because `io0` through `io3` each appear
on both sides and each pair fuses into one `inout`. `Dom100` states the Arty's
100 MHz oscillator as a type, which is the single place a frequency is written in
the whole project and the far end of the wire the [baud generator][baudgen] read
its period out of. `noReset` is a reset that never asserts, so Clash emits no
reset port and the design comes up in the initial values the bitstream loaded ---
which every module in the series was already written to make sensible. And the
body is two mutually recursive bindings, `system` giving `espiPads` a drive and
`espiPads` giving `system` a sample, a loop in the source because it is a loop on
the board, broken by a register the [engine][shape] placed for its own reasons
long ago.

Then a constraints file says where. `E3` for the clock and ten nanoseconds for
its period; one Pmod for the whole bus; `PULLUP TRUE` on the four lanes, keeping
a promise the [pad module's][io] type made and could not enforce.

## The whole stack, from the bottom

This is the end of the descent, so it is worth looking back up it.

At the very bottom is [`crc8Update`][crc], twenty-eight lines with no clock in
them, a fold that unrolls into an LFSR. Above it the [baud generator][baudgen],
which will not be told the clock rate and reads it out of a type; the
[transmitter][tx], the first module with `mealy` at the top and a Mealy shape
wearing Moore outputs; the [receiver][rx], its mirror, with a two-flop
synchroniser, a falling-edge start detector and a three-sample majority vote; and
the [five-line umbrella][uart-top] that generates one tick and fans it into both,
making the loopback a structural certainty rather than a hope.

Then the transport. The [COBS codec][cobs], two pure step functions and one
load-bearing ordering of two cases; the [loader][loader], the first block to own
a clock, peeling a frame with a one-byte holdback and dancing with block RAM
latency across seven drain phases; and the [two memories][mem], four lines of
`blockRamPow2` and three promises, tested against an eight-line oracle because
the promises are what everything above depends on.

Then the engine, read outside-in and then inside-out. The [map first][shape] ---
eight phases, one pure `step`, seventeen state fields, eight closed boxes --- then
[one instruction per cycle][exec], then [the wire and the record][bus]. And then
the boxes, opened in the order the engine reached for them: the [instruction
set][isa] and its total decoder, proven over all 2³² words; the [sixteen
registers][regfile] behind a private index function and a hardwired zero; the
[ALU and the comparator][compute], eight operations and a taken branch; and the
[serialiser][serdes], a `(value, enable)` pair that is the whole of tri-state in
two bits.

Then the formats, kept pure on purpose: the [wire][wire], where a program becomes
a CRC-checked, COBS-stuffed, zero-delimited frame; and the [trace][trace], where a
run becomes three record shapes and a ring that drops rather than tears.

And then out. The [pads][io], where `(value, enable)` becomes `Maybe Bit` and a
`Maybe Bit` becomes a wire that floats; the [top][top], where every module in the
list above is joined and the engine finally gets a clock; and this shell, where
the whole thing acquires a package pin. Twenty-two posts, one direction: from a
byte-wide remainder to a bonded ball of solder, and every module in between read
in full.

The [introduction][intro] said the pins are the hard part. Twenty-one posts
later that has turned out to be true in a specific and slightly humbling way. The
engine could be reasoned about --- it is a pure function, and pure functions submit
to property tests by the hundred thousand. The pins could not. The one place the
project had to abandon its favourite abstraction, the one claim it could not
check with a type, and the one number it had to write down twice in two languages
are all in the last two posts. Everything above the pad boundary was a matter of
getting the thinking right. The pad boundary is a matter of getting the *world*
right, and the world does not typecheck.

What comes next is not another module, because there are none left. The gateware
is complete, it fits on an Arty, and it comes up blinking slowly and waiting for
a program. So the next post stops reading code and starts running it: a real
program loaded over a real serial link, a trigger, real eSPI packets on real
wires with a real analyser watching them, and a drained ring decoded back into
records. Everything this series has described, doing something.

[^tt]: `Clash.Annotations.TH`'s `makeTopEntity` is a Template Haskell splice that
runs at compile time on the *name* of a function --- hence the `'topEntity` quote
--- reifies its type, and walks it collecting `:::` annotations to build a
`Synthesize` annotation with a `PortName` for every argument and result. The
alternative is writing that annotation by hand, which is entirely possible and
substantially worse: it means maintaining a second, parallel description of the
interface that the compiler will not check against the first, so adding an
argument and forgetting to add its `PortName` silently shifts every port after it
by one. Deriving the annotation from the signature makes the signature the single
source of truth. What no tool checks is the step *after* that: the names it emits
have to match the names in the constraints file, and nothing in either language
knows the other file exists. Type-level strings get you as far as the edge of
Haskell and then stop, which is a fair summary of the whole board-shell layer.

[^init]: An FPGA's registers are not undefined at power-up the way an ASIC's are.
Configuration writes the bitstream into the fabric, and the bitstream includes an
initial value for every flip-flop, LUT-RAM location and block RAM word in the
design; on Xilinx parts a global set/reset pulse at the end of configuration
drives each register to its `INIT` attribute. Clash models this with the domain's
`vInitBehavior`, which `vSystem` sets to `Defined` --- meaning `register`'s
initial-value argument and the contents of `blockRamPow2`'s seed vector are real
promises about cycle zero, not simulation conveniences. That is what makes an
explicit reset network optional rather than mandatory, and why omitting it is a
legitimate choice here and would be a bug on an ASIC, where the same code
describes registers that come up holding whatever the silicon felt like. The
trade is that a design with no reset port cannot be restarted without
reconfiguring the part, which for a bench rig is not a limitation: the loader's
`TRIGGER` restarts a *run*, and a run is the only thing anyone wants to restart.

[^pll]: `Tamal.Board.CycloneV` is the same fifty lines with one difference: the
Terasic C5G's oscillator is 50 MHz, so the shell instantiates an Altera PLL ---
`alteraPllSync clk50 (unsafeFromActiveHigh (pure False))` --- which returns both a
100 MHz `Clock Dom100` and a `Reset Dom100` derived from the PLL's lock signal.
That reset holds the design until the multiplied clock is stable and then
releases, which is behaviourally the same as the Arty's power-up `init` and
strictly safer, because it waits for a clock that is actually running rather than
assuming one. The eSPI bus lands on the board's 2×20 GPIO header instead of a
Pmod, and the four lanes are four scalar `inout`s for exactly the same reason.
It is not scheduled for a post because it does not yet meet timing, and a board
post about a design that does not close is a post about a build log. When it
closes it will be short.
