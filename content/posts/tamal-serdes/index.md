+++
title = "Tamal: Value and Enable"
date = 2026-08-10T09:00:00
draft = false
description = "The last shut box of the outside-in descent, opened: Tamal.Bus.Serdes, the pure single-I/O serialiser post 3's beatLanes reached into three times without looking inside, fifty-four lines with no clock and no state; the Lane type, a (value, enable) pair that is the whole tri-state story in two bits --- a data bit and the output-enable that decides whether it means anything, oe = 0 being hands-off, hi-Z --- written as a transparent type synonym rather than a newtype wall because the engine handles it as the bare tuple everywhere; Lanes, a Vec 4 of them, the drive on all four I/O wires for one beat, four wide when v1 drives one; hiZ and driveHigh, release and drive, repeat sizing the vector from the type; serializeX1, a byte unpacked MSB-first into eight beats that drive IO[0] and tri-state the rest, the exact per-bit map post 3 indexed a beat at a time; deserializeX1, the tidy pack inverse the engine never actually calls because sampleGet shifts bits in by hand, kept for the round-trip law it makes expressible; tarBeat, drive-all-high on beat zero and release after, the eSPI turnaround in one line; the x1 corner filled while dual and quad are named and reserved; and three tests that pin the two bits down --- a loopback round-trip, an MSB-first drive-and-tri-state check, and the turnaround --- closing the arc with the engine at last open entire."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "espi"]
+++

The [compute post][compute] ended on the one leaf still shut, and it named
that leaf by what it *does* rather than what it *is*: the drive on the lanes
when a `PUT` walked the beats, the turnaround when the bus changed hands, a
byte becoming MSB-first bits on `IO[0]` --- all of it, it promised, built
out of a single tiny type, a `(value, enable)` pair, the whole tri-state
story in two bits. That type and the handful of functions around it are
`Tamal.Bus.Serdes`, and they are the **last box** in the engine. This post
opens it.

It is the leaf nearest the wire and the smallest of all of them ---
fifty-four lines, no clock, no state, every function a pure combinational
map from a byte or an index to the drive on four pins for one bus beat.
[Post 3's][bus] `beatLanes` reached into it three times without once looking
inside: `hiZ` to release the bus for a `GET`, `tarBeat` to turn it around,
`serializeX1` to lay a byte out bit by bit for a `PUT`. [Post 1's][shape]
`State` carried a `Lanes` in a field and idled it to `hiZ` at reset; [post
2's][exec] `startPut` primed the first beat with `serializeX1 byte !! 0` as
it armed a transfer. We have been leaning on this module since before we
could name it. Now we read how two bits carry a whole bus.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[exec]: https://balbi.sh/posts/tamal-engine-exec/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[isa]: https://balbi.sh/posts/tamal-isa/
[regfile]: https://balbi.sh/posts/tamal-regfile/
[compute]: https://balbi.sh/posts/tamal-compute/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The last door

A Haskell file opens by naming itself and its door, and this one's door is
short:

```haskell
module Tamal.Bus.Serdes
  ( Lane
  , Lanes
  , hiZ
  , driveHigh
  , serializeX1
  , deserializeX1
  , tarBeat
  ) where

import Clash.Prelude
```

Seven names, and the shape of the list is worth a beat before the code
behind it. Two of the seven are *types* --- `Lane` and `Lanes` --- and the
other five are functions that build or transform them. The [CRC's][crc]
image of a module as a wall with a door in it holds: the export list is the
complete public surface, and everything the module can do leaves through one
of these seven names. But the more telling line is the one beneath it.
`import Clash.Prelude`, alone, with no `hiding` clause and nothing
qualified.

The [compute post][compute] made a small drama of its imports --- the ALU
hiding `And` and `Xor` from the prelude, pulling `Isa` in qualified, two
scars from two naming collisions. `Serdes` has none. Its vocabulary ---
`Lane`, `Lanes`, `hiZ`, `serializeX1`, `tarBeat` --- collides with nothing
in `Clash.Prelude` and nothing in the rest of Tamal, so it borrows no
trouble and needs no clause to keep its names clear. The lone [prelude
swap][crc] is the one line every block in the project carries, the line that
[turns a Haskell file into a hardware description][crc], and here it is the
*only* import. This is as plain as a module header gets, and the plainness
is a fair advertisement for what is inside: no cleverness, no ceremony, just
two bits and what you can do with them.

## A tuple, on purpose

The whole module rests on one line, and it is a `type` synonym:

```haskell
-- | One I/O lane's per-beat drive state: (output value, output enable).
type Lane = (Bit, Bit)
```

A `Lane` is a pair of bits: an **output value** and an **output enable**.
That is the entire type, and the choice to write it as a `type` synonym --- a
bare alias for `(Bit, Bit)` --- rather than a `newtype` wrapper is the first
decision worth reading. The [CRC post][crc] gave us the image of a module as
a wall with a door; a `newtype` is a wall of its own, a distinct type the
compiler forces every caller to construct and unwrap. A `type` synonym is
the opposite:

> If a module is a wall with a door in it, a `type` synonym is a door with
> no wall --- a nickname.

`Lane` and `(Bit, Bit)` are the *same* type; `Lane` is only a more readable
name for it. Everywhere the engine touches a lane --- pattern-matching it,
reading its value with `fst` and its enable with `snd`, building one with
`(v, e)` --- it does so with ordinary tuple machinery, no wrapper in the way.
For a type this pervasive --- carried in the `State`, produced eight at a
time by the serialiser, compared bit-for-bit in the tests --- that
transparency is exactly right: a `newtype` would buy a little safety against
confusing a lane with some other pair and charge for it in `Lane (…)` and
`unLane` noise on every use. The author declined the wall because there is
nothing behind it to hide.

So what do the two bits *mean*? This is the sentence the whole eSPI story
compresses into. A wire on a shared, bidirectional bus is not simply high or
low --- it has a **third** state, **released**, in which this end drives
nothing at all and lets the other end drive instead. That released state ---
high-impedance, *hi-Z* --- is what makes a bus bidirectional: one physical
wire that the controller drives on a `PUT` and the target drives on a `GET`,
so long as the two never drive at once. One bit cannot express three states.
It takes a second:

> The value says what to put on the wire; the enable says whether to put
> anything there at all --- and on a shared bus, the enable is the bit that
> keeps two drivers from fighting.

When `oe = 1`, the pad drives `value` onto the pin. When `oe = 0`, the pad
lets go --- the pin floats, the `value` bit is a don't-care, and whatever is
on the other end owns the wire. Two bits, four combinations, but only
*three* distinct behaviours: drive-0, drive-1, and release, which is `(0, 0)`
and `(1, 0)` both, the value ignored once the enable is low. The redundancy
is not waste; it is the shape of the thing. The value is a suggestion the
enable may veto, and on a bus where two simultaneous drivers is not a wrong
answer but a *short circuit*, the enable is the load-bearing bit.[^tristate]

## Four lanes, one beat

One lane is two bits; the bus is four lanes, so its drive state is four of
those pairs:

```haskell
-- | The drive state of all four I/O lanes for one SCK beat.
type Lanes = Vec 4 Lane
```

`Lanes` is a `Vec 4 Lane` --- a fixed-length vector of four `(Bit, Bit)`
pairs, one per I/O wire, `IO[0]` through `IO[3]`, all for a *single* bus
beat. This is the type [post 1's][shape] `State` held in its `lanes` field
and the type [post 3's][bus] `beatLanes` returned: the complete instruction
to the pins for one tick of the serial clock, four wires at once. Eight of
these, walked one at a time, drive a byte; [post 3][bus] did the walking,
indexing `serializeX1 (shifter t) !! bi` a beat per rising edge.

Four is more than x1 needs. Single-I/O drives one wire and releases the
other three, so three of the four pairs in every `PUT` beat are the same
released `(0, 0)`. The width is built for a bus this module does not yet
serialise: dual-I/O uses two wires, quad all four, and `Lanes` is sized for
the widest of them from the start. It is the same deliberate room to grow
the [ISA post][isa] found in `Reg` --- a five-bit selector naming thirty-two
registers when v1 implements sixteen --- the type describing the whole design
space while the code fills one corner. We will come back to that corner;
first, the two simplest values of the type.

Two whole-bus constants sit right under it, and between them they are the bus
at rest and the bus asserted:

```haskell
hiZ :: Lanes
hiZ = repeat (0, 0)

driveHigh :: Lanes
driveHigh = repeat (1, 1)
```

`hiZ` is all four lanes released --- every enable low, the whole bus handed
off. It is the value [post 1's][shape] `initState` powered up into, the state
[post 3's][bus] `safePins` slammed the bus to before a trap, the [`GET`][bus]
idle where the engine samples rather than drives. Across the project `hiZ`
has meant one thing, and now we can see it is not a special state at all,
only four copies of "enable off":

> `hiZ` is hands off the bus.

`driveHigh` is its mirror --- all four enables *on*, all four values one, the
bus actively pulled to logic high. It has exactly one use, the first clock of
a turnaround, and we will meet it there in a moment. Both are written with
`repeat`, which fills a `Vec` with copies of one element; neither says *how
many*, because neither has to. The count lives in the type --- `hiZ ::
Lanes` is `Vec 4`, so `repeat` makes four --- the same
[widths-live-in-the-type][primer] discipline that let `bitCoerce` pack a word
by its field sizes.[^repeat] Change `Lanes` to `Vec 2` for a narrower bus and
these two definitions do not move; the `4` they never mention becomes a `2`.

## A byte, eight beats

Now the function the module is named for. A byte goes in; the drive for all
eight of its beats comes out:

```haskell
serializeX1 :: BitVector 8 -> Vec 8 Lanes
serializeX1 b = map beat (unpack b :: Vec 8 Bit)
 where
  beat :: Bit -> Lanes
  beat bit' = (bit', 1) :> (0, 0) :> (0, 0) :> (0, 0) :> Nil
```

The [type is half the documentation][primer]: a `BitVector 8` in, a `Vec 8
Lanes` out --- one byte, mapped to eight beats, each beat a full four-lane
drive. Not one lane value but the *entire schedule* for the byte, computed in
one go, which is what a combinational block does: no loop, no clock, just a
map laying eight beats down side by side. [Post 2's][exec] `startPut` took
`serializeX1 byte !! 0` to prime the very first beat as it armed a transfer;
[post 3's][bus] `beatLanes` took `serializeX1 (shifter t) !! bi` to fetch
beat `bi` as the bus walked forward. Both were indexing into *this* vector,
the eight-beat schedule this line builds.

Two moves make it, and we have read both before. `unpack b :: Vec 8 Bit`
takes the opaque eight-wire byte and lays it out as eight individual `Bit`s
--- the same [structural, zero-cost reinterpretation][isa] the [CRC][crc]
opened its fold with, and with the same convention riding on it: index `0` of
the vector is the **most significant bit**, so the vector runs MSB-first.
That convention is not decorative. eSPI, like SPI and SMBus before it, clocks
the most significant bit onto the wire first, so beat `0` must carry bit `7`,
and `unpack`'s ordering delivers exactly that with no reversal.[^vec]

Then `map beat` turns each of those eight bits into a full `Lanes`. And
`beat` is the two-bit story spelled out one more time: for a data bit `bit'`,
it drives `IO[0]` with `(bit', 1)` --- the value is the bit, the enable is on
--- and releases `IO[1]`, `IO[2]`, `IO[3]` with three `(0, 0)`s. That is
single-I/O in one line: **one wire driven, three let go**, the data on
`IO[0]` and the rest of the bus floating. Fold the map over the eight bits
and you have the byte's whole life on the pins --- eight beats, each driving
one bit MSB-first on one wire, the other three released the entire time ---
which is precisely what the loopback and drive tests will check, and what
[post 3][bus] spent its beats emitting.

## The inverse the engine never calls

The serialiser has an inverse, and it is even shorter:

```haskell
deserializeX1 :: Vec 8 Bit -> BitVector 8
deserializeX1 = pack
```

Eight sampled bits in, one byte out, and the body is a single word: `pack`,
the exact partner of the `unpack` a few lines up. Where `unpack` scattered a
byte into MSB-first bits, `pack` gathers MSB-first bits back into a byte, and
because the two are inverse halves of the same [`bitCoerce`
reinterpretation][isa], `pack . unpack` is the identity by construction. Read
against `serializeX1`, `deserializeX1` is the undo: it takes the eight bits a
`GET` sampled off the wire, MSB-first, and assembles the byte they spell.

There is a quiet surprise here, though, and it is worth stopping on: **the
engine never calls this function.** [Post 3's][bus] `sampleGet` did the
receiving, and it did not batch eight bits and `pack` them --- it shifted each
sampled bit into an accumulator by hand, one per rising edge --- ``shifter
`shiftL` 1`` to open a slot at the bottom, an `.|.` to drop the new bit in ---
interleaved with the beat loop it lived inside. The engine assembles a
received byte incrementally because it *receives* incrementally, a bit at a
time across eight beats, with nowhere to hold a `Vec 8 Bit` in the meanwhile.
So `deserializeX1` describes the same result by a different route than the
engine actually takes.

Why write it, then, if nothing on the fabric runs it? Because it earns its
place as a *specification* rather than as code on a path. It names the
inverse, and naming it is what lets the round-trip law --- serialise a byte,
sample it back, deserialise, and get the byte --- be *stated* and a property
test check it, pinning the MSB-first convention down from both ends at once.
It is a move the project has made before with a name it never quite uses: the
[ISA's][isa] `OpcodeUnimplemented`, declared to reserve a vocabulary the
decoders never return; the [trace module's][bus] `encodeRecord`, which never
runs on the fabric and exists only so the engine's inline packing has a
readable reference to be proven equal to. `deserializeX1` is that kind of
honest surplus --- a line that is not on the critical path but makes a promise
the critical path can be checked against.[^deser]

## Drive once, then let go

The last function is the turnaround, and it is a single conditional:

```haskell
tarBeat :: Unsigned 4 -> Lanes
tarBeat i = if i == 0 then driveHigh else hiZ
```

`tarBeat` takes a beat index and answers with a drive: on beat `0` it returns
`driveHigh` --- all four lanes pulled high --- and on every beat after it
returns `hiZ` --- all four released. That is the whole of the **turnaround**,
the `TAR` the [bus post][bus] fetched as `tarBeat bi`, and it is the
choreography an eSPI bus performs whenever the wire changes hands. Between a
command the controller drove and a response the target will drive, the two
ends have to swap roles without ever both driving at once; the turnaround is
the handshake in the middle, a beat or two where the outgoing driver gives
the bus a defined push and then lets go, so the line is never left floating
and undefined at the instant of handover.[^tar]

`driveHigh`'s one use is right here: it is that defined push, the first
turnaround clock that drives the bus to a known high before the release. And
after that single beat, every later index falls through to `hiZ`.

> Drive the turnaround clock once, then let go.

The type is `Unsigned 4 -> Lanes`, and the argument is the same beat index
`beatLanes` threaded in --- but `tarBeat` reads only whether it is zero. The
function is total over all sixteen values an `Unsigned 4` can take, and
fifteen of them give the same answer; only beat `0` is special. It is a
small, blunt shape for a small, blunt job: one beat of drive, then the
[`hiZ`][bus] the rest of the module already meant by release.

## Three drives, one field

Step back from the four functions and the module snaps into a single picture
--- because [post 3's][bus] `beatLanes`, the one place all of this was used,
is a three-way case whose three arms are three of the names we have just
read:

```haskell
beatLanes t bi = case pending t of
  PendGet{} -> hiZ
  PendTar   -> tarBeat bi
  _         -> serializeX1 (shifter t) !! bi
```

Three directions off one `pending` field. A `GET` releases the bus so the
other end can drive it --- `hiZ`. A `TAR` turns it around --- `tarBeat bi`.
Anything else is a `PUT`, and it serialises --- `serializeX1 (shifter t) !!
bi`. The whole of `Serdes` exists to answer that case: `hiZ` is the release
arm, `tarBeat` the turnaround arm, `serializeX1` the drive arm, and `Lane` is
the two-bit alphabet all three answer in. Every eSPI direction the engine can
point the bus is one of these three drives, and each is this module handing
back four `(value, enable)` pairs.

<figure class="srd-fig" style="margin:2rem 0">
<svg class="srd" viewBox="0 0 760 360" role="img" aria-labelledby="srd-t srd-d" xmlns="http://www.w3.org/2000/svg">
<title id="srd-t">The three lane drives Serdes provides for one bus beat</title>
<desc id="srd-d">Three drive modes shown as rows of four cells, one cell per I/O lane IO0 through IO3, each cell holding a value-and-enable pair. The PUT row drives the data bit on IO0 with enable one and releases IO1, IO2 and IO3 with zero-zero. The GET row releases all four lanes with zero-zero and samples IO1, marked with a dashed outline and an arrow. The TAR beat-zero row drives all four lanes high with one-one. Driven cells, where the enable is one, are drawn in the accent colour.</desc>
<style>
.srd{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.srd .cd{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.srd .cz{fill:var(--bg-main);stroke:var(--fg-dim);stroke-width:1.6}
.srd .cs{fill:var(--bg-main);stroke:var(--accent);stroke-width:2;stroke-dasharray:5 3}
.srd .hd{fill:var(--fg-dim);font-family:var(--mono);font-size:13px}
.srd .md{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.srd .sub{fill:var(--fg-dim);font-family:var(--sans);font-size:11.5px}
.srd .vd{fill:var(--accent);font-family:var(--mono);font-size:13px}
.srd .vz{fill:var(--fg-dim);font-family:var(--mono);font-size:13px}
.srd .vs{fill:var(--accent);font-family:var(--mono);font-size:13px}
.srd .sw{stroke:var(--accent);stroke-width:2;fill:none}
.srd .sl{fill:var(--accent);font-family:var(--sans);font-size:11.5px}
.srd .ah{fill:var(--accent)}
</style>
<defs>
<marker id="srd-a" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="ah"/></marker>
</defs>
<!-- column headers -->
<text class="hd" x="230" y="40" text-anchor="middle">IO[0]</text>
<text class="hd" x="360" y="40" text-anchor="middle">IO[1]</text>
<text class="hd" x="490" y="40" text-anchor="middle">IO[2]</text>
<text class="hd" x="620" y="40" text-anchor="middle">IO[3]</text>
<!-- PUT -->
<text class="md" x="22" y="96">PUT</text>
<text class="sub" x="22" y="114">drive IO[0]</text>
<rect class="cd" x="172" y="77" width="116" height="46" rx="7"/>
<rect class="cz" x="302" y="77" width="116" height="46" rx="7"/>
<rect class="cz" x="432" y="77" width="116" height="46" rx="7"/>
<rect class="cz" x="562" y="77" width="116" height="46" rx="7"/>
<text class="vd" x="230" y="105" text-anchor="middle">(bit, 1)</text>
<text class="vz" x="360" y="105" text-anchor="middle">(0, 0)</text>
<text class="vz" x="490" y="105" text-anchor="middle">(0, 0)</text>
<text class="vz" x="620" y="105" text-anchor="middle">(0, 0)</text>
<!-- GET -->
<text class="md" x="22" y="201">GET</text>
<text class="sub" x="22" y="219">release all</text>
<rect class="cz" x="172" y="182" width="116" height="46" rx="7"/>
<rect class="cs" x="302" y="182" width="116" height="46" rx="7"/>
<rect class="cz" x="432" y="182" width="116" height="46" rx="7"/>
<rect class="cz" x="562" y="182" width="116" height="46" rx="7"/>
<text class="vz" x="230" y="210" text-anchor="middle">(0, 0)</text>
<text class="vs" x="360" y="210" text-anchor="middle">(0, 0)</text>
<text class="vz" x="490" y="210" text-anchor="middle">(0, 0)</text>
<text class="vz" x="620" y="210" text-anchor="middle">(0, 0)</text>
<!-- sample arrow into IO1 -->
<line class="sw" x1="360" y1="260" x2="360" y2="231" marker-end="url(#srd-a)"/>
<text class="sl" x="360" y="274" text-anchor="middle">sample IO[1]</text>
<!-- TAR -->
<text class="md" x="22" y="306">TAR</text>
<text class="sub" x="22" y="324">beat 0</text>
<rect class="cd" x="172" y="287" width="116" height="46" rx="7"/>
<rect class="cd" x="302" y="287" width="116" height="46" rx="7"/>
<rect class="cd" x="432" y="287" width="116" height="46" rx="7"/>
<rect class="cd" x="562" y="287" width="116" height="46" rx="7"/>
<text class="vd" x="230" y="315" text-anchor="middle">(1, 1)</text>
<text class="vd" x="360" y="315" text-anchor="middle">(1, 1)</text>
<text class="vd" x="490" y="315" text-anchor="middle">(1, 1)</text>
<text class="vd" x="620" y="315" text-anchor="middle">(1, 1)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The three drives <code>beatLanes</code> selects, each a full <code>Lanes</code> of four <code>(value, enable)</code> pairs. A <code>PUT</code> beat drives the data bit on <code>IO[0]</code> and releases <code>IO[1..3]</code>; a <code>GET</code> beat releases all four and samples <code>IO[1]</code>; a <code>TAR</code> beat 0 drives all four high before the release that follows. The shaded cells are the driven ones --- enable high --- and everywhere else the enable is low and the value a don't-care. One byte is eight <code>PUT</code> beats, MSB first; only <code>IO[0]</code> is ever driven in x1.</figcaption>
</figure>

The three modes are the same four lanes read three ways. Look only at the
enables: a `PUT` turns exactly one on (`IO[0]`), a `TAR` beat turns all four
on for its one clock, and a `GET` turns them all off and reads `IO[1]`
instead of driving it. The values matter only where an enable is high --- the
shaded cells --- which is the two-bit story made visual: the column that
carries data is the column whose enable let it through.

## One corner of a wider bus

The module's doc-comment states the rules the code obeys, and reading them
against what we have seen names the one corner of eSPI this file actually
serialises:

```text
x1 rules: PUT drives the data bit on IO[0], MSB first, with IO[1..3]
tri-stated; GET samples IO[1] with all engine drivers tri-stated.
Dual/quad maps land in Phase 3.
```

Every claim in that comment is now a line we have read. `PUT` drives `IO[0]`
MSB-first --- `serializeX1`'s `(bit', 1)` on lane zero, fed by `unpack`'s
MSB-first order. `IO[1..3]` tri-stated --- its three `(0, 0)`s. `GET` samples
`IO[1]` with all drivers released --- `hiZ`, and [post 3's][bus] `sampleGet`
reading lane one. The `x1` in the function's name is the promise the comment
keeps: this is **single-I/O**, one data wire in each direction, the simplest
of the eSPI width modes.

The other modes are named and deferred in a single clause --- *dual/quad maps
land in Phase 3* --- and that deferral is the [ISA post's][isa] honesty turned
on the serialiser. `Lanes` is a `Vec 4` because the bus has four data wires
and dual-I/O drives two of them, quad all four; the type is sized for the
widest mode from the first line. But `serializeX1` fills only the x1 corner,
driving `IO[0]` and no other, exactly as [`decodeConfig`][isa] accepted only
the one controller-x1-20MHz configuration and named every wider mode an error
rather than pretending to implement it. The space is drawn in full --- four
lanes, the modes that would use them --- and one corner of it is wired,
honestly, the rest marked reserved. A `serializeX2` and `serializeX4` are the
shape of the Phase 3 work; when they come they will produce the same `Vec 8
Lanes`, only with two or four enables high per beat instead of one.

## The tests

Three tests, and between them they hold down every claim the module makes. A
small helper sets them up:

```haskell
io0 :: Lanes -> Bit
io0 lanes = fst (lanes !! (0 :: Index 4))
```

`io0` reads one thing off a beat: the *value* bit of lane zero, `fst` of
`IO[0]`'s pair --- the bit `serializeX1` drives. It is the sampler the tests
use to look at what a `PUT` put on the wire.

The first test is the round trip, a [Hedgehog][hedgehog] property:

```haskell
testProperty "x1 serialize/deserialize round-trips (loopback)" $ property $ do
  b <- forAll genByte
  deserializeX1 (map io0 (serializeX1 b)) === b
```

Read it inside out. `serializeX1 b` drives the byte into eight beats; `map
io0` reads `IO[0]`'s value back out of each beat, recovering the eight driven
bits as a `Vec 8 Bit`; `deserializeX1` packs them into a byte. The law is
that this returns `b` unchanged, for every byte [Hedgehog][hedgehog] can
draw. The comment calls it *loopback*, and the name is the picture: on a real
loopback the driven `IO[0]` is wired round to the sampled `IO[1]`, so reading
`IO[0]`'s value stands in for sampling what came back. It is
serialise-then-sample-then-deserialise closing to the identity --- the
MSB-first convention proven consistent from the drive end to the receive end,
because a single reversed bit anywhere in that chain would make some byte come
back wrong and the property would shrink to it.

The second test drives one specific, well-chosen byte and checks the pins
directly:

```haskell
let beats = serializeX1 0b1000_0000     -- MSB set only
io0 (head beats) @?= 1                   -- first beat carries the MSB
io0 (last beats) @?= 0
map (\l -> snd (l !! (1 :: Index 4))) beats @?= repeat 0
```

`0b1000_0000` is the sharpest possible probe of bit order: only the most
significant bit is set. If serialisation is MSB-first, the *first* beat must
drive a one and the *last* must drive a zero --- which is exactly what `io0
(head beats) @?= 1` and `io0 (last beats) @?= 0` assert. Flip the convention
to LSB-first and the two swap, and the case goes red. The third line checks
the other half of the x1 rule: `snd (l !! 1)` is `IO[1]`'s *enable*, and
mapping it over every beat must give `repeat 0` --- lane one released on all
eight beats, never driven. One byte proves both that the bits leave in the
right order and that the idle lanes stay out of the way.

The last test pins the turnaround:

```haskell
tarBeat 0 @?= repeat (1, 1)  -- first clock drives all high
tarBeat 1 @?= repeat (0, 0)  -- then releases
tarBeat 5 @?= repeat (0, 0)
```

Beat `0` is `driveHigh`, beats `1` and `5` are `hiZ` --- the "drive once,
then let go" shape asserted on both sides of the only boundary the function
has. `tarBeat 5` is there to make the point that *every* beat past the first
releases, not merely the second; the function has one special case and
fifteen identical ones, and the test samples both kinds. Three small
assertions, and the two-bit vocabulary is checked in all three of the
directions the bus can face.

## What we read

The last box, opened --- and the smallest. `Tamal.Bus.Serdes` is one
transparent type and a handful of functions over it. `Lane` is a `(value,
enable)` pair, a `type` synonym with no wall around it because the engine
handles it as the bare tuple it is; the value says what to drive and the
enable says whether to drive at all, and on a shared bidirectional bus that
second bit --- the one that chooses between driving and releasing --- is the
whole of tri-state, three wire states carried in two bits. `Lanes` is four of
them, one per I/O wire, sized for a four-wide bus the module does not yet
fill. `hiZ` and `driveHigh` are the bus released and the bus asserted,
`repeat` taking their width from the type. `serializeX1` lays a byte out
MSB-first into eight beats, driving `IO[0]` and releasing the rest ---
single-I/O in one `map` --- and `deserializeX1` is its `pack` inverse, kept
for the round-trip law even though the engine, receiving a bit at a time,
never calls it. `tarBeat` drives one turnaround clock high and then lets go.
And [post 3's][bus] `beatLanes` was these three drives all along --- a `GET`'s
release, a `TAR`'s turnaround, a `PUT`'s serialise --- three eSPI directions
off one field, each a fistful of `(value, enable)` pairs. The tests close the
loop from drive to sample, pin the MSB-first order to a single-bit probe, and
stake down the turnaround.

And with that the descent is done. We read the [engine's map][shape] first,
then watched it [dispatch an instruction][exec] and [run its wire][bus],
holding every leaf shut --- and then we opened them, each in the order the
engine first reached for it. `decode` and the [instruction set][isa]; the
[sixteen registers][regfile] its operands named; the [compute][compute] those
registers fed, `dataResult` and `branchTaken`; and now the serialiser nearest
the wire, `serializeX1` and `tarBeat` and the two-bit `Lane` they speak. The
[CRC][crc] had its own post before this arc began, and `AW` and `RW` were read
inline with the [memories][mem]; every other black box the three outside-in
posts named has now been opened and read whole. The engine is open entire ---
map and every piece.

What is left is not inside the engine but around it. `step` is pure: it takes
a `State` and an input and returns a `State`, a `BusOut`, and maybe a trace
word, and every `Lanes` it computes is still just numbers --- four `(value,
enable)` pairs, a *description* of a drive, not a drive. Something has to
carry those pairs the last step out: take each lane's enable and its value and
turn them into a real bidirectional pad, a wire that is driven when the enable
is high, floats when it is low, and is sampled back into the engine's inputs
when the other end drives. That is the impure shell --- `Tamal.Io` and its
tri-state buffers, `Tamal.Top` wiring the engine to the clock and the pins,
the `Tamal.Board.*` layers that pin it to real silicon --- and it is where the
`(value, enable)` pair we have just called the whole story meets the FPGA
primitive that makes it physical. The [introduction][intro] warned that the
pins are the hard part; the engine has spent seven posts being precise about
*what* to drive and *when*, so that the shell can be left the narrower,
sharper job of driving it. That shell is a story for another day --- a later
batch, out to the pins.

[^tristate]: The three states a shared wire can take --- driven high, driven
low, and released --- are electrical, not logical, and the third is the one a
single logic bit cannot name. A released output is *high-impedance*: the
driver disconnects, presenting so large a resistance that it neither pulls the
line high nor low but lets another driver anywhere on the net decide the
level. A bidirectional bus is exactly a set of wires more than one device may
drive *at different times*, and its one unbreakable rule is that no two drive
at once --- two enabled outputs fighting over a line, one high and one low, is
a near short from supply to ground, the contention that overheats pads and
corrupts every bit. The enable bit is what enforces the rule: it is the wire
that goes to a pad's output-enable, `1` to drive and `0` to float, and a
protocol like eSPI is in large part a discipline for ensuring exactly one end
has its enable high on any given lane at any given beat. The `(value, enable)`
pair is that discipline in its smallest form --- and it is also, not by
coincidence, the interface of the FPGA primitive that will implement it: a
tri-state buffer takes a data input and an enable, and does precisely what
`Lane` describes. The later shell batch is where the pair meets that
primitive.

[^repeat]: `repeat :: KnownNat n => a -> Vec n a` fills a vector with copies
of one element, and the length `n` is a type-level number the compiler
supplies from context --- here from the signatures `hiZ :: Lanes` and
`driveHigh :: Lanes`, which fix `n = 4` because `Lanes = Vec 4 Lane`. There is
no count in the source because there is none to write: the type carries it,
the same way [`bitCoerce`][isa] took its field widths from the types it
coerced between and `unpack b :: Vec 8 Bit` took its `8` from the annotation.
It is the [primer's][primer] widths-live-in-the-type discipline in miniature
--- a definition that stays correct when the width changes because it never
mentioned the width in the first place.

[^vec]: `beat` builds its four-lane vector by hand with `:>` and `Nil`:
`(bit', 1) :> (0, 0) :> (0, 0) :> (0, 0) :> Nil`. `:>` is `Vec`'s cons ---
prepend an element to a shorter vector --- and `Nil` is the empty one, so the
expression conses four `Lane`s onto `Nil` and has type `Vec 4 Lane`, which is
`Lanes`. The length is not asserted anywhere; it is *counted* by the compiler
from the four `:>`s and checked against the `Lanes` the signature demands, so
a `beat` that built three lanes or five would not typecheck. It is the same
structural guarantee `repeat` gave from the other direction --- there the `4`
came from the type, here it comes from the literal --- and both meet in the
middle at `Vec 4`.

[^deser]: A function the fabric never runs might look like waste, but
`deserializeX1` is the third instance of a pattern the project uses on
purpose. The [ISA's][isa] `OpcodeUnimplemented` is a `DecodeError` the
decoders never return, declared so a future partial implementation has the
word ready. The [trace module's][bus] `encodeRecord` never executes on the
engine --- `step` packs its records inline with `bitCoerce` --- and exists only
as the readable reference that inline packing is proven equal to.
`deserializeX1` is the same kind of surplus: it is the named inverse of
`serializeX1`, and naming it is what lets the round-trip property be
*written*, so the MSB-first convention has a law guarding it from both ends.
The engine's own `sampleGet` shifts bits in one at a time because it receives
them one at a time, across eight separate rising edges, with no `Vec 8 Bit`
ever in hand; `deserializeX1` is the batch statement of the same result, kept
for the test and for the reader, not for the synthesiser.

[^tar]: The turnaround is spec §5's answer to a hazard the [SCK waveform][bus]
created. Command and response travel on the same lanes in opposite
directions, so at the seam between them the controller must stop driving and
the target must start, and for a beat or two *neither* may --- or *both*
might, the contention a shared bus must never allow. `TAR` fills that seam:
`driveHigh` gives the bus one defined, driven-high clock so the line is never
left floating at the instant of handover, and then `hiZ` releases it for the
other end to take. Driving high rather than low for that single beat matches
the bus's idle-high convention, so the release settles to the level the
pull-ups would hold anyway. It is a small courtesy with a real payoff: a
receiver watching for the first response edge sees a clean transition rather
than the ambiguous drift of a wire that changed owners mid-float.
