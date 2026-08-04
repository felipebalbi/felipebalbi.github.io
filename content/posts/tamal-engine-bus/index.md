+++
title = "Tamal: The Wire and the Record"
date = 2026-08-04T09:00:00
draft = false
description = "Following the four instructions that armed themselves and stopped, across the seam into the phases that finish them: SCK as the fabric clock divided by five, three cycles low and two high, the rising edge at the two-to-three boundary where a driven bit is latched and a sampled bit is read; stepBusBeat as a three-way guard that ticks a beat forward, starts the next, or completes; beatLanes choosing among releasing the bus for a GET, driving a turnaround, and serialising a byte for a PUT --- three directions off one pending field; the sample that shifts IO1 into an accumulator MSB-first at phase three; completion, where a GET finally writes its register, folds the byte into the CRC residue the CRC post built, and pushes a CAPTURE into the trace ring; the ring itself, drop-on-full with a sticky overflow bit and a terminator slot at the top a record can never overwrite; MARK's second word, spilled a cycle later in TraceEmit; WAIT_ON counting down against an active-low alert; and HALT, the terminator that carries a status and, on a trap, a reason and safe pins --- four record shapes, built inline with bitCoerce but mirroring Tamal.Trace's reference encoder, that a run leaves behind for the host to read."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "espi"]
+++

The [last post][exec] ended four instructions mid-gesture. `PUT`, `GET`,
`TAR`, `MARK`, and `WAIT_ON` each spent their one cycle in `Exec` not
finishing but *arming* --- a shifter loaded, a beat count set, a write
recorded in `pending`, a phase changed --- and then the cycle ended and
they handed off to a longer phase. This post is the other side of that
handoff: `BusBeat`, `TraceEmit`, and `WaitAlert`, the three phases where
the arming is spent. It is where the engine finally touches the wire.

Everything until now has been decision. The [shape][shape] was the map;
`Exec` was the choice of what to do. Here the engine *does* it: it drives
and samples two-and-a-half wires on a clock it makes itself, and records
what happened into a ring the host will later read back. Two subjects,
then --- the wire and the record --- and they meet in the one phase that
runs the bus.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[exec]: https://balbi.sh/posts/tamal-engine-exec/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## SCK is the fabric clock, divided by five

The [introduction][intro] made a point of insisting that Tamal is not a
throughput problem but a *timing-alignment* problem --- that the engine
does not need to be fast, it needs to be precise about *when* it drives,
samples, and lets go. All of that precision lives in one small counter
and the function that reads it:

```haskell
sckOf :: Index 5 -> Bit
sckOf = boolToBit <$> (>= 3)
```

`SCK`, the serial clock, is the fabric clock divided by five. The
[shape][shape] post named the field that does the dividing: `busPhase ::
Index 5`, a counter that walks `0, 1, 2, 3, 4` and wraps, one step per
100 MHz fabric cycle. `sckOf` turns that counter into a clock level with
a single comparison --- `(>= 3)` is true for phases three and four, false
for zero, one, two, and `boolToBit` maps that to a `Bit`.[^sckof] So
`SCK` is **low across phases {0, 1, 2}** and **high across phases {3, 4}**:
three cycles low, two cycles high, five to a beat, one bit of eSPI per
beat.

The duty cycle is lopsided --- three-fifths low --- and that is a
deliberate, honest compromise. A clean fifty-percent clock at this divisor
would need a hardware clocking primitive; a plain counter cannot split
five evenly. Rather than spend an MMCM to make the clock pretty, the
engine takes the asymmetry, which at 20 MHz an eSPI target does not care
about: what it cares about is that the edges are where the spec says, and
they are. The rising edge --- the one that matters --- falls at the **two-
to-three boundary**, and both directions of the transfer are pinned to
it.

<figure class="sck-fig" style="margin:2rem 0">
<svg class="sck" viewBox="0 0 760 232" role="img" aria-labelledby="sck-t sck-d" xmlns="http://www.w3.org/2000/svg">
<title id="sck-t">SCK as the fabric clock divided by five, with the drive and sample points</title>
<desc id="sck-d">Two bus beats are shown side by side, each five fabric cycles wide, the cycles numbered by busPhase zero through four. SCK is low across phases zero, one and two, and high across phases three and four, so each beat is three cycles low then two high. The rising edge falls at the two-to-three boundary. A driven PUT bit is placed at phase zero at the start of each beat; a GET bit is sampled from IO1 at phase three, on the rising edge.</desc>
<style>
.sck{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.sck .grid{stroke:var(--fg-dim);stroke-width:1;opacity:.32}
.sck .beat{stroke:var(--fg-main);stroke-width:1.5}
.sck .sckw{stroke:var(--accent);stroke-width:2.5;fill:none}
.sck .edge{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:4 4}
.sck .rlbl{fill:var(--fg-main);font-family:var(--mono);font-size:12.5px}
.sck .ph{fill:var(--fg-main);font-family:var(--mono);font-size:12px}
.sck .beatlbl{fill:var(--fg-dim);font-family:var(--sans);font-size:12.5px}
.sck .ev{fill:var(--fg-dim);font-family:var(--sans);font-size:11px}
.sck .evA{fill:var(--accent);font-family:var(--sans);font-size:11px}
.sck .node{fill:var(--accent)}
</style>
<!-- beat labels -->
<text class="beatlbl" x="290" y="24" text-anchor="middle">beat 1 (bit 7)</text>
<text class="beatlbl" x="590" y="24" text-anchor="middle">beat 2 (bit 6)</text>
<!-- gridlines -->
<line class="grid" x1="200" y1="38" x2="200" y2="150"/>
<line class="grid" x1="260" y1="38" x2="260" y2="150"/>
<line class="grid" x1="320" y1="38" x2="320" y2="150"/>
<line class="grid" x1="380" y1="38" x2="380" y2="150"/>
<line class="grid" x1="500" y1="38" x2="500" y2="150"/>
<line class="grid" x1="560" y1="38" x2="560" y2="150"/>
<line class="grid" x1="620" y1="38" x2="620" y2="150"/>
<line class="grid" x1="680" y1="38" x2="680" y2="150"/>
<!-- beat boundaries -->
<line class="beat" x1="140" y1="34" x2="140" y2="205"/>
<line class="beat" x1="440" y1="34" x2="440" y2="205"/>
<line class="beat" x1="740" y1="34" x2="740" y2="205"/>
<!-- row labels -->
<text class="rlbl" x="128" y="60" text-anchor="end">busPhase</text>
<text class="rlbl" x="128" y="115" text-anchor="end">SCK</text>
<!-- busPhase numbers -->
<text class="ph" x="170" y="60" text-anchor="middle">0</text>
<text class="ph" x="230" y="60" text-anchor="middle">1</text>
<text class="ph" x="290" y="60" text-anchor="middle">2</text>
<text class="ph" x="350" y="60" text-anchor="middle">3</text>
<text class="ph" x="410" y="60" text-anchor="middle">4</text>
<text class="ph" x="470" y="60" text-anchor="middle">0</text>
<text class="ph" x="530" y="60" text-anchor="middle">1</text>
<text class="ph" x="590" y="60" text-anchor="middle">2</text>
<text class="ph" x="650" y="60" text-anchor="middle">3</text>
<text class="ph" x="710" y="60" text-anchor="middle">4</text>
<!-- SCK waveform: low {0,1,2}, high {3,4} -->
<path class="sckw" d="M140,132 H320 V90 H440 V132 H620 V90 H740"/>
<!-- rising-edge markers -->
<circle class="node" cx="320" cy="90" r="4"/>
<circle class="node" cx="620" cy="90" r="4"/>
<line class="edge" x1="320" y1="94" x2="320" y2="166"/>
<line class="edge" x1="620" y1="94" x2="620" y2="166"/>
<!-- events -->
<text class="ev" x="170" y="182" text-anchor="middle">drive @ φ0</text>
<text class="ev" x="470" y="182" text-anchor="middle">drive @ φ0</text>
<text class="evA" x="320" y="182" text-anchor="middle">sample @ φ3</text>
<text class="evA" x="620" y="182" text-anchor="middle">sample @ φ3</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)"><code>SCK</code> is the fabric clock ÷ 5. Each beat is <code>busPhase</code> 0–4: three cycles low, two high, one eSPI bit. A <code>PUT</code> places its bit on <code>IO0</code> at the start of the beat (φ0); a <code>GET</code> samples <code>IO1</code> at the rising edge (the 2→3 boundary, φ3). Eight beats make a byte, forty fabric cycles. The waveform is the whole of the engine's timing discipline --- a counter, a comparison, and one edge everything is pinned to.</figcaption>
</figure>

## One beat, five cycles

`stepBusBeat` runs once per fabric cycle while a transfer is in flight,
and its whole logic is a three-way guard --- am I mid-beat, between
beats, or done?

```haskell
stepBusBeat :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepBusBeat s inp
  | busPhase s < 4 = (tick s, busOut (tick s), Nothing) -- mid-beat: advance phase
  | beatIx s + 1 < beatTot s = (nextBeat s, busOut (nextBeat s), Nothing) -- more beats
  | otherwise = complete s
```

Read the three arms as the life of a beat. While `busPhase < 4` the beat
is still running, so `tick` advances it one phase. Once `busPhase` reaches
`4` the beat is over, and the question becomes whether there is another:
if `beatIx + 1 < beatTot` --- more bits to go --- `nextBeat` starts the
next one; otherwise the transfer is finished and `complete` closes it out.
`beatIx` is the bit we are on, `beatTot` the total the [`Exec`
setup][exec] wrote (eight for a byte). The whole of a `PUT`, a `GET`, or a
turnaround is this guard, iterated: five cycles a beat, `beatTot` beats,
then done.

`tick` is where a single fabric cycle of a beat happens:

```haskell
  tick t =
    let p = busPhase t + 1
        t1 = t{busPhase = p, sck = sckOf p}
     in if p == 3 then sampleGet t1 else t1
```

It steps `busPhase` up by one and sets `sck` to `sckOf` of the new phase
--- the waveform, generated one cycle at a time. And it carries the one
event that has to happen on the rising edge: `if p == 3 then sampleGet`.
Phase three is the first high phase, the tick that crosses the two-to-
three boundary, and that is exactly when a `GET` must read the wire. So
`tick` samples there and nowhere else.

Between beats, `nextBeat` resets the phase counter and sets up the next
bit:

```haskell
  nextBeat t =
    let bi = beatIx t + 1
     in t{busPhase = 0, beatIx = bi, sck = 0, lanes = beatLanes t bi}
```

`busPhase` back to zero, `beatIx` up by one, `SCK` back to idle-low, and
--- the important part --- `lanes = beatLanes t bi`, the drive for the new
bit placed *now*, at the start of the beat, at phase zero. That is the
"drive @ φ0" the diagram marks: each bit's value goes onto the lanes as
its beat begins, and holds for the whole five cycles while `SCK` pulses
once beneath it.

## What each beat drives

`beatLanes` is the function that decides what the lanes carry, and it is a
three-way case on the one thing that distinguishes a `PUT` from a `GET`
from a turnaround --- the `pending` field:

```haskell
beatLanes :: State -> Unsigned 4 -> Lanes
beatLanes t bi = case pending t of
  PendGet{} -> hiZ
  PendTar -> tarBeat bi
  _ -> serializeX1 (shifter t) !! bi
```

Three directions, one field. A `GET` has `pending = PendGet …`, so its
lanes are `hiZ` --- all drivers released, the bus handed to the other side
to drive, because on a read *we* are the one sampling. A turnaround has
`PendTar`, so its lanes are `tarBeat bi` --- the turnaround waveform for
bit `bi`, which drives the first clock and releases the rest, the
handover choreography an eSPI bus does between command and response.
Anything else --- a `PUT`, whose `pending` is `PendNone` --- serialises:
`serializeX1 (shifter t) !! bi` takes the byte in the shifter and picks
out bit `bi`, MSB-first, to drive on `IO0`.

Two of those three --- `serializeX1` and `tarBeat`, along with `hiZ` and
the `Lanes` type itself --- are the [last shut box][shape] in this arc,
`Tamal.Bus.Serdes`, the serialiser. We are using it exactly as before:
`serializeX1` turns a byte into per-bit lane drives, `tarBeat` turns a
clock index into a turnaround drive, `hiZ` means "release." *How* they do
it is the final post. Here it is enough that `beatLanes` reads the
`pending` field and asks the serialiser for the right drive, and the fact
that all three eSPI directions fall out of one three-way case is the
whole shape of the thing.

## Sampling a GET

The other half of a transfer is reading, and it is the `sampleGet` that
`tick` fires at phase three:

```haskell
  sampleGet t = case pending t of
    PendGet{} -> t{shifter = shifter t `shiftL` 1 .|. zeroExtend (pack (ioIn inp !! (1 :: Index 4)))}
    _ -> t
```

Only a `GET` samples --- the guard is `PendGet{}`, and everything else
passes the state through untouched. When it is a `GET`, the move is the
classic serial-in shift: `shifter << 1` makes room at the bottom, and
`.|. … (ioIn inp !! 1)` drops the sampled bit of `IO1` --- the eSPI
response lane --- into that new low bit. Do that once per beat, at each
rising edge, and after eight beats the shifter holds the byte that came
back, assembled MSB-first: the first bit sampled has been shifted left
seven times to the top, the last sits at the bottom. It is the mirror of
the `PUT`'s serialise --- one drives a byte out a bit at a time, the other
shifts a byte in a bit at a time, both pinned to the same phase-three
edge.

## Completing the transfer

When the last beat's `busPhase` reaches four with no beats left,
`complete` closes the op --- and this is where a `GET` finally pays out
everything it recorded as `pending` back in `Exec`:

```haskell
complete :: State -> (State, BusOut, Maybe Ring)
complete s = case pending s of
  PendGet rd nbits crc ->
    let byte = shifter s
        crc' = if crc then crc8Update (rxCrc s) byte else rxCrc s
        capW = captureWord (pack nbits) byte
        (ptr', ovf', mw) = pushWord (ringPtr s) (ovf s) capW
        s' =
          (advance s)
            { sck = 0, busPhase = 0, beatIx = 0, pending = PendNone
            , rxCrc = crc'
            , regs = writeReg (regs s) rd (resize byte)
            , ringPtr = ptr', ovf = ovf'
            }
     in (s', busOut s', mw)
  _ ->
    let s' = (advance s){sck = 0, busPhase = 0, beatIx = 0, pending = PendNone}
     in (s', busOut s', Nothing)
```

The second arm is the easy one: a `PUT` or `TAR` has nothing to hand back,
so completion just idles `SCK`, clears the beat scratch, and `advance`s to
the next instruction. The first arm is where a `GET`'s three recorded
promises --- the `rd`, the `nbits`, the `crc` flag it wrote into `PendGet`
--- come due at once.

`byte = shifter s` is the assembled result. Three things happen to it.
It is written to the register: `writeReg (regs s) rd (resize byte)`,
the `rd` the instruction named, widened to the register's width. It is
folded into the CRC residue --- but *only if* `crc`: `if crc then
crc8Update (rxCrc s) byte else rxCrc s`. That flag is the difference
between `GET_BYTE`, which advances the [CRC][crc] so a program can check a
packet's residue, and `GET_BITS`, which is CRC-neutral and leaves the
residue alone. `crc8Update` is the [CRC block][crc] we read whole, called
here on the byte just sampled --- the residue the [last post][exec]'s
`RDSR` reads is *built* right here, one received byte at a time. And it is
recorded into the trace: `captureWord` and `pushWord`, which are the
record half of the post.

Notice what completion never does: stall. The register write, the CRC
fold, and the `advance` happen unconditionally; only the trace push can
fail, and when it does it drops the record and sets a flag rather than
holding the bus. That is the [introduction][intro]'s load-bearing rule ---
*never block the bus on trace backpressure* --- and it is enforced here,
in the order of these bindings: the bus work is done before the trace is
even attempted, so the trace can never delay it.

## The trace ring

A `GET`'s completion writes one record --- a `CAPTURE` --- and the two
functions that build and place it are the whole of how the engine writes
to the trace. First the word:

```haskell
captureWord :: BitVector 4 -> BitVector 8 -> BitVector 32
captureWord nbits byte = bitCoerce (0b00 :: BitVector 2, 0 :: BitVector 18, nbits, byte)
```

A `CAPTURE` is thirty-two bits: a two-bit **tag** `00`, eighteen zero bits
of padding, the four-bit `nbits` (how many bits this capture holds), and
the eight-bit `byte`. `bitCoerce` packs that tuple into a single word by
laying its fields end to end --- `2 + 18 + 4 + 8 = 32`, exactly full. The
tag in the top two bits is how the host, reading the ring back later,
tells one kind of record from another.

Then placement, which is the ring's one interesting decision:

```haskell
pushWord :: Unsigned RW -> Bool -> BitVector 32 -> (Unsigned RW, Bool, Maybe Ring)
pushWord ptr ov w
  | ov = (ptr, True, Nothing)
  | ptr <= termAddr - 1 = (ptr + 1, False, Just (Ring ptr w))
  | otherwise = (ptr, True, Nothing)
```

Three cases, and they are the [trace ring's][mem] whole contract. If
the overflow flag `ov` is already set, the ring has given up: drop the
word, keep the flag high, write nothing. If there is room --- `ptr <=
termAddr - 1`, at or below the last usable slot --- write the word at
`ptr` and bump the pointer. Otherwise the ring is full: set overflow,
drop the word, and --- the important part --- write *nothing*, so the
pointer never climbs onto `termAddr`.

That last slot is reserved on purpose. The [memories post][mem] noticed it
from the other side: the ring is 4096 words, `termAddr = maxBound`, and
the top slot is kept empty so a terminator always has somewhere
guaranteed to land. `pushWord` is the code that keeps the promise ---
records fill from the bottom up to `termAddr - 1` and stop, dropping
themselves with a sticky flag rather than ever clobbering the slot the
`HALT` word is going to need. **Drop on overflow, never wrap, never stall,
never touch the terminator.** A compliance transcript wants an honest
ordered prefix and a flag that says "there was more," not a silently
overwritten tail.

## MARK: a two-word record

Most records are one word and are written in the cycle that produces
them. `MARK` is the exception --- it is two words, a label and a payload
--- and it is the reason the [shape post][shape]'s lifecycle had a phase
called `TraceEmit` that did nothing but "write a MARK's second word." Back
in [`Exec`][exec], a `MARK` wrote its *first* word (the label) and set
`pending = PendMark payload`; `TraceEmit` is where the second word lands:

```haskell
stepTraceEmit :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepTraceEmit s _ = case pending s of
  PendMark payload ->
    let s' = (advance s){ringPtr = ringPtr s + 1, pending = PendNone}
     in (s', busOut s', Just (Ring (ringPtr s) payload))
  _ -> (advance s, busOut s, Nothing)
```

It spills the `payload` the `Exec` cycle stashed in `pending` to the next
ring slot, bumps the pointer, and advances. The label word it followed was
built by a sibling of `captureWord`:

```haskell
markLabelWord :: BitVector 14 -> BitVector 32
markLabelWord lbl = bitCoerce (0b10 :: BitVector 2, 0 :: BitVector 16, lbl)
```

Tag `10`, sixteen zeros, a fourteen-bit label. Then the payload word,
whole and untagged, in the cycle after. Splitting the write across two
cycles is why the [`Exec`][exec] side checked that *two* slots were free
before committing to either --- a half-written `MARK`, a label with no
payload, would be a corrupt record, so the engine writes both or neither.
The two cycles are one atomic act stretched over a phase boundary, and
`TraceEmit` is the second half of it.

## WAIT_ON: blocking on the alert

`WaitAlert` is the last of the three deferred phases, and the only one
that can spend more than a fixed number of cycles. It is where a `WAIT_ON`
sits and watches the alert line:

```haskell
stepWaitAlert :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepWaitAlert s inp = case pending s of
  PendWait rd
    | asserted -> done rd 1
    | waitTimer s == 0 -> done rd 0
    | otherwise -> (s{waitTimer = waitTimer s - 1}, busOut s, Nothing)
  _ -> (advance s, busOut s, Nothing)
 where
  b =
    if cfgAlertSource (cfg s) == AlertPin
      then alertIn inp
      else ioIn inp !! (1 :: Index 4)
  asserted = b == 0 -- ALERT#/IO[1] are both active low (§6.5)
  done rd v =
    let s' = (advance s){regs = writeReg (regs s) rd v, pending = PendNone}
     in (s', busOut s', Nothing)
```

Each cycle in `WaitAlert` asks one question in priority order. Is the
alert **asserted**? The alert is active-low --- `b == 0` --- and `b` is
chosen the same way [`GET_ALERT`][exec] chose it, the configured pin or
in-band `IO[1]`. If asserted, the wait is satisfied: `done rd 1` writes a
one into the result register and moves on. Otherwise, has the timer run
out? `waitTimer == 0` means it has, and `done rd 0` falls through with a
zero --- timed out, no alert. Otherwise, decrement `waitTimer` and wait
another cycle. The register ends up holding a one if the alert came and a
zero if the clock beat it, so the program can branch on which happened.
`WAIT_ON` armed the timer in [`Exec`][exec]; `WaitAlert` counts it down.

## HALT, and the record a run ends on

Every run ends by writing one last record, and every trap in the [last
post][exec] --- the decode error, the config trap, the reserved status
register --- ended by calling this:

```haskell
haltWith :: Bool -> BitVector 3 -> BitVector 8 -> State -> (State, BusOut, Maybe Ring)
haltWith trap reason status s =
  let s' = s{phase = Halted}
      w = bitCoerce (0b11 :: BitVector 2, 0 :: BitVector 17, reason, trap, (ovf s), status)
   in (s', busOut s', Just (Ring termAddr w))
```

`haltWith` builds the **terminator** and drops the machine into `Halted`.
The word is the third record shape: tag `11`, seventeen zeros, a three-bit
`reason`, a one-bit `trap` flag, the sticky `ovf` bit, and an eight-bit
`status`. A plain `HALT` calls it with `trap = False, reason = 0` and the
program's own status byte; a trap calls it with `trap = True`, one of the
five reasons, and --- crucially --- on a state that has been run through
`safePins` first:

```haskell
safePins :: State -> State
safePins s = s{csN = 1, sck = 0, rstN = 1, lanes = hiZ}
```

`CS#` high, `SCK` low, `RESET#` released, lanes `hiZ` --- the bus put back
to idle before the machine stops, so a program that dies mid-frame leaves
nothing driving. And the terminator goes to `Ring termAddr` --- the
reserved top slot `pushWord` guarded so carefully. That guarding pays off
exactly here: no matter how many records overflowed, the slot the `HALT`
word needs is free, so a run *always* ends with a readable terminator that
carries its overflow bit along. A truncated trace still says, in its last
word, that it was truncated.

That is the fourth and last record shape, and it completes a small
taxonomy. Every word the ring ever holds is one of four:

- **`REVISION`** at slot zero --- `[major | minor | patch]`, the version
  stamp the [shape post][shape]'s `Preamble` wrote on the first cycle.
- **`CAPTURE`**, tag `00` --- one word, a sampled byte and its bit count.
- **`MARK`**, tag `10` --- two words, a label then a payload.
- **`HALT`**, tag `11` --- the terminator, with `trap`, `reason`, `ovf`,
  and `status`.

And there is a quiet honesty in how they are built. Every one of these
words is assembled inline with `bitCoerce`, right here in the engine,
because `step` has to be synthesizable --- it emits at most one word a
cycle, and it cannot call the list-returning encoder that would be the
natural way to describe a record. So the *layouts* live twice: once here,
as `bitCoerce` tuples in `captureWord`, `markLabelWord`, and `haltWith`,
and once in `Tamal.Trace`, as an `encodeRecord` that the tests use as the
reference model. Every comment in this file that says *mirrors
Trace.encodeRecord* is a promise that the two agree --- the engine's fast
inline form and the trace module's readable canonical form, checked
against each other so the bytes the host reads back are the bytes the
format says. `Tamal.Trace` never runs on the fabric; it exists so the
engine's inline packing has something to be proven equal to.

## What we read

The wire and the record, both. On the wire: `SCK` is the fabric clock over
five, low three, high two, and every edge that matters is the rising one
at the two-to-three boundary. `stepBusBeat` is a three-way guard that
ticks a beat, starts the next, or completes; `beatLanes` reads one
`pending` field and gets all three eSPI directions --- release for a
`GET`, turn around for a `TAR`, serialise for a `PUT` --- out of the
still-shut serialiser; `sampleGet` shifts the response in MSB-first on the
rising edge; and `complete` writes the register, folds the [CRC][crc]
residue that `RDSR` will read, and pushes the capture --- bus work first,
so the trace can never stall it.

And the record: a ring that fills from the bottom, drops on overflow with
a sticky flag, and never touches the terminator slot the [memories][mem]
kept empty; a `MARK` whose two words are written across a phase boundary
or not at all; a `WAIT_ON` that counts down against an active-low alert;
and a `HALT` that ends every run with a status, a reason, and, on a trap,
a bus set safe --- four record shapes, built fast inline but pinned to
`Tamal.Trace`'s readable encoder so the two can be checked equal.

With that, the engine is whole *as a map.* The [shape][shape] drew it, the
[dispatch][exec] chose within it, and this post ran its wire and wrote its
trace. Across three posts we have used --- and never opened --- every leaf
it composes: `decode`, `dataResult`, `branchTaken`, `readReg` and
`writeReg`, `decodeConfig`, `serializeX1`, and `tarBeat`. That was the
whole wager of reading [outside in][shape]: hold the pieces as one-line
promises long enough to understand the machine that spends them, and only
then open them.

Now we open them, in the order the engine first reaches for each. And the
engine's very first act on any word --- before compute, before the bus,
before anything --- was `decode`. So that is where we go next: down into
`Tamal.Isa`, the instruction set itself, the box every road out of `Exec`
began by opening.

[^sckof]: The point-free spelling `boolToBit <$> (>= 3)` reads oddly the
first time. `(>= 3)` is a function `Index 5 -> Bool`; `<$>` over the
function "functor" is ordinary composition, so `boolToBit <$> (>= 3)` is
`\p -> boolToBit (p >= 3)` --- take a phase, test whether it is three or
four, turn the answer into a `Bit`. It is the same `fmap`-is-composition
trick the [primer] noted, used to write a one-liner that would otherwise
need a lambda. Clash lowers it to a comparator and nothing else.

[^atomic]: "Both or neither" is doing real work for a reason particular to
a streaming trace. The host reads the ring back as a flat sequence of
words and re-parses the records from their tags; a lone `MARK` label with
no payload following would desynchronise that parse, and every record
after it would be misread. A dropped `CAPTURE` costs one sample; a
half-written `MARK` would cost the whole rest of the transcript. That
asymmetry is why the `CAPTURE` push may fail one word at a time but the
`MARK` write checks for two slots up front and drops both together --- the
cost of a torn record is not one record, it is all of them.
