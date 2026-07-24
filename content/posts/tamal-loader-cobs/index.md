+++
title = "Tamal: The COBS codec"
date = 2026-07-25T09:00:00
draft = true
description = "Reading Tamal's streaming COBS codec end to end: how Consistent Overhead Byte Stuffing strips every 0x00 from a payload so a lone 0x00 can delimit frames, why the loader reached for it, why the two functions are pure s -> i -> (s, o) steps the loader clocks rather than mealy machines of their own, how cobsDecodeStep walks a group at a time and manufactures each implied zero lazily — only when the next code byte arrives — then quietly drops the final group's pending zero at the frame boundary, how cobsEncodeStep buffers a 254-byte group and then drives code-plus-group downstream, why testing the full-group case before the zero case is load-bearing so that a zero landing on a full group terminates a fresh empty group instead of vanishing, and how the whole thing is welded to the pure Tamal.Wire.Cobs reference by differential property tests."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "cobs", "loader"]
+++

The [transmitter][tx] and [receiver][rx] built a byte *pipe*; [yesterday][top]
we sealed the two halves into a single word --- *the UART* --- and left four
wires hanging at the loader's door. Today we walk through that door. But the
first thing behind it is not a state machine. It is an answer to a question the
pipe cannot answer for itself: a bare stream of bytes has no seams, so where
does one message end and the next begin?

I have carried my answer to that question for a little while, and I
did not find it in a textbook. I found it in [postcard-rpc][pcrpc],
[James Munns'][jm] crate for speaking to embedded targets over a
wire. I liked it enough to become one of its maintainers, and I lean
on it --- that is, I depend on it --- hard on [pico de gallo][pdg] 🌶,
where it frames every request and every response that crosses between
host and firmware.[^postcard] So when Tamal's loader needed a way to
tell one message from the next on a bare UART, I did not go looking. I
already knew what I wanted, and I reached for [**COBS**: Consistent
Overhead Byte
Stuffing](https://en.wikipedia.org/wiki/Consistent_Overhead_Byte_Stuffing).

COBS earns its keep with a property that sounds too good until you see how
plainly it is bought: it removes *every* `0x00` byte from a payload, at a cost
of at most one extra byte per 254, so that a single `0x00` can be reserved ---
unambiguously, forever --- to mean *end of frame*. The loader decodes the
inbound stream on the way in and encodes its replies on the way out, and both
directions are the subject of this post.

Like every Tamal block before it the codec fits in a screenful and change --- but
unlike the UART halves it holds no clock of its own. What follows are two *pure*
step functions; the loader is the machine that clocks them. We will spend almost
all our words on the two that matter, `cobsDecodeStep` and `cobsEncodeStep`, and
wave past the scaffolding around them, which by now is a ceremony we have read
five times.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[baudgen]: https://balbi.sh/posts/tamal-uart-baudgen/
[tx]: https://balbi.sh/posts/tamal-uart-tx/
[rx]: https://balbi.sh/posts/tamal-uart-rx/
[top]: https://balbi.sh/posts/tamal-uart-top/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa
[pcrpc]: https://github.com/jamesmunns/postcard-rpc
[jm]: https://www.linkedin.com/in/james-munns-8a42b429/
[pdg]: https://github.com/OpenDevicePartnership/pico-de-gallo

## The entire source

Minus the license header and the doc-comments --- and, this once, minus the
longer inline notes on the encoder, which we will read in place where they
belong --- here is `src/Tamal/Loader/Cobs.hs`:

```haskell
module Tamal.Loader.Cobs
  ( DecSt
  , initDec
  , cobsDecodeStep
  , EncSt
  , initEnc
  , cobsEncodeStep
  ) where

import Clash.Prelude

data DecSt = DecSt
  { dCnt :: Unsigned 8
  , dFull :: Bool
  , dPend :: Bool
  , dGot :: Bool
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

initDec :: DecSt
initDec = DecSt 0 False False False

data EncMode = EFilling | EEmitting
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

data EncSt = EncSt
  { eMode :: EncMode
  , eBuf :: Vec 254 (BitVector 8)
  , eFill :: Unsigned 8
  , eIx :: Unsigned 8
  , ePend :: Maybe (BitVector 8, Bool)
  , eFinal :: Bool
  , eLast :: Bool
  }
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)

initEnc :: EncSt
initEnc = EncSt EFilling (repeat 0) 0 0 Nothing False False

cobsDecodeStep :: DecSt -> (Maybe (BitVector 8), Bool) -> (DecSt, (Maybe (BitVector 8), Bool, Bool))
cobsDecodeStep s (mIn, frameEnd)
  | frameEnd = (initDec, (Nothing, True, not (dGot s) || dCnt s /= 0))
  | otherwise = case mIn of
      Nothing -> (s, (Nothing, False, False))
      Just b
        | dCnt s == 0 ->
            let (out, s1) =
                  if dPend s
                    then (Just 0, s{dPend = False, dGot = True})
                    else (Nothing, s{dGot = True})
                s2 = startGroup s1 b
             in (s2, (out, False, False))
        | otherwise ->
            let cnt' = dCnt s - 1
                s' =
                  s
                    { dCnt = cnt'
                    , dGot = True
                    , dPend =
                        if cnt' == 0
                          then not (dFull s)
                          else dPend s
                    }
             in (s', (Just b, False, False))
 where
  startGroup st c =
    let full = c == 255
        n = (unpack c :: Unsigned 8) - 1 -- c is 1..255 (never 0), so n is 0..254
     in if n == 0
          then st{dCnt = 0, dFull = full, dPend = not full}
          else st{dCnt = n, dFull = full}

cobsEncodeStep :: EncSt -> (Maybe (BitVector 8, Bool), Bool) -> (EncSt, (Bool, Maybe (BitVector 8), Bool))
cobsEncodeStep s (mIn, dsReady) = case eMode s of
  EFilling -> case mIn of
    Nothing -> (s, (True, Nothing, False))
    Just (b, lst)
      | eFill s == 254 ->
          (s{eMode = EEmitting, eIx = 0, ePend = Just (b, lst)}, (True, Nothing, False))
      | b == 0 ->
          (s{eMode = EEmitting, eIx = 0, eFinal = lst, eLast = lst}, (True, Nothing, False))
      | lst ->
          ((store s b){eMode = EEmitting, eIx = 0, eLast = True, eFinal = False}, (True, Nothing, False))
      | otherwise ->
          (store s b, (True, Nothing, False))
  EEmitting
    | not dsReady -> (s, (False, Nothing, False))
    | eIx s <= eFill s ->
        let out :: BitVector 8
            out = if eIx s == 0 then fromIntegral (eFill s) + 1 else eBuf s !! (eIx s - 1)
         in (s{eIx = eIx s + 1}, (False, Just out, False))
    | eFinal s ->
        (s{eFill = 0, eIx = 0, eFinal = False}, (False, Nothing, False))
    | otherwise -> case ePend s of
        Just (pb, pl)
          | pb == 0 ->
              (s{eMode = EEmitting, eFill = 0, eIx = 0, ePend = Nothing, eFinal = pl, eLast = pl}, (False, Nothing, False))
          | pl ->
              ((store s{eFill = 0} pb){eMode = EEmitting, eIx = 0, ePend = Nothing, eLast = True}, (False, Nothing, False))
          | otherwise ->
              ((store s{eFill = 0} pb){eMode = EFilling, ePend = Nothing}, (False, Nothing, False))
        Nothing
          | eLast s -> (initEnc, (False, Nothing, True))
          | otherwise -> (s{eMode = EFilling, eFill = 0}, (False, Nothing, False))
 where
  store st b = st{eBuf = replace (eFill st) b (eBuf st), eFill = eFill st + 1}
```

Six things, top to bottom: an export list that is wider than any we have seen; a
four-field `DecSt` record; a two-constructor `EncMode` sum and a seven-field
`EncSt` record; two one-line initialisers; and the two step functions that are
the whole point. We will glance at the first four and then slow right down for
the last two.

## The ritual, one more time

The opening beat is the [CRC][crc] module's, played a sixth time, so I will be
quick --- but there is one genuinely new thing in it, and it is worth a sentence.

```haskell
module Tamal.Loader.Cobs
  ( DecSt
  , initDec
  , cobsDecodeStep
  , EncSt
  , initEnc
  , cobsEncodeStep
  ) where

import Clash.Prelude
```

Every module so far has had exactly *one* name on its export list --- `crc8Update`,
`uartTx`, `uartRx`, `uart`. This one has six. The wall still has a door, but the
door is wider, and the reason is a real design fact rather than an oversight: the
two `St` types and their initialisers leave the file *on purpose*. This module is
not a self-contained machine you plug a clock into; it is a **component the
loader embeds**. The loader holds a `DecSt` and an `EncSt` inside its *own*
state record and threads them through its own `mealy`, so it must be handed the
types to store and the `initDec`/`initEnc` values to seed them. The export list
is exactly as wide as that contract requires and no wider: two opaque state
types, two seeds, two steps. `EncMode`, `dCnt`, `store`, `startGroup` --- the
genuinely internal machinery --- all stay sealed behind the wall.

`import Clash.Prelude` is the same prelude swap the [CRC post][crc] dwelt on ---
out goes ordinary Haskell's furniture, in comes the hardware vocabulary. Here it
is supplying `Vec 254`, `Unsigned 8`, `BitVector 8`, `Maybe`, and the `Vec`
operations `repeat`, `replace`, and `!!` that the encoder's buffer leans on. I
will not re-derive it; the [CRC][crc] and [primer][primer] posts did that at
length.

The two state records are the [primer][primer]'s sum-and-product story once
more, and I will gloss them in a single pass because the step functions are
written entirely in terms of their fields. `DecSt` is what the decoder carries
tick to tick:

- **`dCnt`** --- data bytes still owed in the current group; `0` means *the next
  byte is a code byte*.
- **`dFull`** --- was this group's code `255`? A full group is special: it carries
  *no* implied zero.
- **`dPend`** --- do we owe an injected `0x00` before the next code byte?
- **`dGot`** --- has any byte at all arrived this frame? (Only used to flag an empty
  frame as malformed.)

`EncSt` carries more, because encoding buffers a whole group before it can know
the group's length:

- **`eMode`** --- `EFilling` (accumulating a group) or `EEmitting` (driving
  `code ++ group` out).
- **`eBuf`**, **`eFill`** --- the up-to-254-byte group buffer and how many bytes are
  in it.
- **`eIx`** --- the emit cursor: `0` is the code byte, `1..eFill` the data.
- **`ePend`** --- a byte stashed when a full 254-group is flushed; it will start the
  next group, and it remembers whether it was the stream's last byte.
- **`eFinal`** --- is a final empty group still owed? (The last input byte was
  `0x00`.)
- **`eLast`** --- has the input stream ended?

Both initialisers are the resting state written out --- `initDec = DecSt 0 False
False False`, `initEnc = EncSt EFilling (repeat 0) 0 0 Nothing False False` ---
and there is nothing in either worth a paragraph.

The one structural thing worth saying out loud before we start: look at the
types of the two steps and notice what is **absent**.

```haskell
cobsDecodeStep :: DecSt -> (Maybe (BitVector 8), Bool) -> (DecSt, (Maybe (BitVector 8), Bool, Bool))
cobsEncodeStep :: EncSt -> (Maybe (BitVector 8, Bool), Bool) -> (EncSt, (Bool, Maybe (BitVector 8), Bool))
```

No `Signal`. No `HiddenClockResetEnable`. No `mealy`. These are `s -> i -> (s,
o)` in the flesh --- the exact shape the [transmitter][tx] taught us to read ---
but *un-lifted*, pure functions with no clock anywhere in sight. That is
deliberate, and it is the same division of labour the [CRC][crc]'s `step` had:
the codec describes *what one byte does to the state*, and someone else owns the
register that makes it sequential. That someone is the loader, which wraps both
steps in its own `mealy`. Everything below is combinational; the clock is a
floor above.

## What COBS actually does

Strip the acronym away and COBS is one idea: **turn every zero into a distance**.

Here is the whole trick. You want a byte that means "frame boundary," and `0x00`
is the natural pick --- it is what an idle line and a cleared buffer are already
full of. The trouble is that real payloads contain `0x00` too, so the delimiter
is ambiguous the moment you pick it. COBS removes the ambiguity by removing the
zeros: it chops the payload into **groups** at each zero, and prefixes every
group with a single **code byte** that says how long the group is. The zeros
themselves are never transmitted --- they are *implied* by the fact that one group
ended and the next began.

Concretely, a group of `L` non-zero bytes (with `0 ≤ L ≤ 254`) is emitted as a
code byte `L + 1` followed by the `L` bytes. Because the code is `L + 1` and `L`
tops out at 254, the code ranges over `0x01..0xFF` and is *never* `0x00`. Read
the code another way and it becomes a pointer: `L + 1` is exactly the distance
from this code byte to the next one, i.e. to where the next zero used to be. The
decoder follows the chain --- jump `code` bytes, you land on the next code byte;
the byte that was there is a zero you re-insert --- until the delimiter stops it.

<figure class="cobs-fig" style="margin:2rem 0">
<svg class="cobs" viewBox="0 0 760 244" role="img" aria-labelledby="cobs-t cobs-d" xmlns="http://www.w3.org/2000/svg">
<title id="cobs-t">How COBS turns a payload with zeros into a stream with none</title>
<desc id="cobs-d">The payload 11 22 00 33 00 sits on top, its two zero bytes highlighted in accent. Below, the COBS encoding 03 11 22 02 33 01 followed by a single 00 frame delimiter drawn dashed. Each code byte — 03, 02 and 01 — is the distance to the next code byte, shown by accent arcs labelled 3, 2 and 1; the code byte stands in for the zero that used to sit at that distance. The encoded stream contains no 0x00, so the single trailing 0x00 delimits the frame unambiguously.</desc>
<style>
.cobs{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.cobs .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.cobs .zero{fill:var(--accent);opacity:0.16;stroke:var(--accent);stroke-width:2}
.cobs .code{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.cobs .delim{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 4}
.cobs .arc{stroke:var(--accent);stroke-width:2;fill:none}
.cobs text{font-family:var(--sans)}
.cobs .val{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.cobs .valz{fill:var(--accent);font-family:var(--mono);font-size:15px}
.cobs .lab{fill:var(--fg-dim);font-size:12px}
.cobs .num{fill:var(--accent);font-size:13px;font-family:var(--mono)}
.cobs .ahA{fill:var(--accent)}
</style>
<defs>
<marker id="cobs-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahA"/></marker>
</defs>
<text class="lab" x="44" y="84" text-anchor="start">payload</text>
<rect class="box"  x="250" y="60" width="52" height="44" rx="5"/>
<rect class="box"  x="314" y="60" width="52" height="44" rx="5"/>
<rect class="zero" x="378" y="60" width="52" height="44" rx="5"/>
<rect class="box"  x="442" y="60" width="52" height="44" rx="5"/>
<rect class="zero" x="506" y="60" width="52" height="44" rx="5"/>
<text class="val"  x="276" y="88" text-anchor="middle">11</text>
<text class="val"  x="340" y="88" text-anchor="middle">22</text>
<text class="valz" x="404" y="88" text-anchor="middle">00</text>
<text class="val"  x="468" y="88" text-anchor="middle">33</text>
<text class="valz" x="532" y="88" text-anchor="middle">00</text>
<path class="arc" d="M196,152 C196,120 388,120 388,152" marker-end="url(#cobs-aa)"/>
<path class="arc" d="M388,152 C388,124 516,124 516,152" marker-end="url(#cobs-aa)"/>
<path class="arc" d="M516,152 C516,128 580,128 580,152" marker-end="url(#cobs-aa)"/>
<text class="num" x="292" y="116" text-anchor="middle">3</text>
<text class="num" x="452" y="120" text-anchor="middle">2</text>
<text class="num" x="548" y="126" text-anchor="middle">1</text>
<text class="lab" x="44" y="180" text-anchor="start">encoded</text>
<rect class="code"  x="170" y="154" width="52" height="44" rx="5"/>
<rect class="box"   x="234" y="154" width="52" height="44" rx="5"/>
<rect class="box"   x="298" y="154" width="52" height="44" rx="5"/>
<rect class="code"  x="362" y="154" width="52" height="44" rx="5"/>
<rect class="box"   x="426" y="154" width="52" height="44" rx="5"/>
<rect class="code"  x="490" y="154" width="52" height="44" rx="5"/>
<rect class="delim" x="554" y="154" width="52" height="44" rx="5"/>
<text class="val" x="196" y="182" text-anchor="middle">03</text>
<text class="val" x="260" y="182" text-anchor="middle">11</text>
<text class="val" x="324" y="182" text-anchor="middle">22</text>
<text class="val" x="388" y="182" text-anchor="middle">02</text>
<text class="val" x="452" y="182" text-anchor="middle">33</text>
<text class="val" x="516" y="182" text-anchor="middle">01</text>
<text class="valz" x="580" y="182" text-anchor="middle">00</text>
<text class="lab" x="196" y="222" text-anchor="middle">code = length + 1</text>
<text class="lab" x="580" y="222" text-anchor="middle">delimiter</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">COBS on the payload <code>11 22 00 33 00</code>. It splits at the zeros into groups <code>[11 22]</code>, <code>[33]</code>, and a trailing empty group, and prefixes each with a code byte equal to its length plus one: <code>03 11 22</code>, <code>02 33</code>, <code>01</code>. Each code is the distance (accent arcs) to the next code byte — the spot the zero used to occupy — so the decoder can re-insert every zero by following the chain. The encoded stream holds no <code>0x00</code>, which frees the single trailing <code>0x00</code> to mean, unambiguously, <em>end of frame</em>.</figcaption>
</figure>

Two details in that picture are the entire reason both step functions are more
than ten lines long, so let us name them now and meet them again in the code.

The first is the **final group**. Count the groups above: `[11 22]`, `[33]`, and
then an *empty* one, `01`. A payload with *k* zeros always encodes to *k + 1*
groups, because every zero marks a boundary *between* two groups --- and a payload
that ends in a zero, like this one, leaves an empty run after that last zero,
which becomes the trailing empty group. On decode a zero is re-inserted after
every group *but the last*; the final group is closed by the frame delimiter
instead. So the decoder owes exactly *k* zeros across *k + 1* groups --- one after
each group except the final one --- and getting that off-by-one asymmetry right is
where its trickiest line lives.

The second is the **full group**. A group holds at most 254 bytes because the
code caps at `0xFF`. What happens to a run of, say, 300 non-zero bytes with no
zero to break it? COBS emits a full group --- code `0xFF`, 254 bytes --- and then
simply *continues* the run in the next group. Crucially, that `0xFF` group is a
**continuation**: unlike every code from `0x01` to `0xFE`, it does *not* stand in
for a zero, because there was no zero --- the run was just too long. "Is this code
`0xFF`?" is therefore the question that decides whether a zero gets re-inserted,
and it is exactly the `dFull` flag in the decoder and the `eFill == 254` test in
the encoder.[^consistent]

With those two facts in hand the code reads like prose. We take the decoder
first, as promised, because it is the shorter of the two.

## `cobsDecodeStep`: decoding a byte

The decoder's job is to invert the picture: bytes of a delimiter-stripped COBS
frame go in, the original payload comes out, and any structural nonsense is
flagged. Start, as always, with the type.

```haskell
cobsDecodeStep :: DecSt -> (Maybe (BitVector 8), Bool) -> (DecSt, (Maybe (BitVector 8), Bool, Bool))
```

Read against the [primer][primer]'s `s -> i -> (s, o)`: the state is `DecSt`, the
input is `(Maybe (BitVector 8), Bool)`, and the output is `(Maybe (BitVector 8),
Bool, Bool)`. Name the parts and the contract appears. The input is a maybe-byte
--- `Just b` when the UART handed the loader a byte this cycle, `Nothing`
otherwise --- paired with a `frameEnd` flag the loader pulses the instant it sees
the `0x00` delimiter on the wire. The output triple is a maybe-decoded-byte, a
`done` pulse, and a `malformed` flag. The decoder is a valve: bytes trickle in,
decoded bytes trickle out, and two one-bit signals announce the end of the frame
and whether it held together.

The body is three guarded cases. Take them in the order the hardware does.

### Frame end: reset, and the malformed law

```haskell
  | frameEnd = (initDec, (Nothing, True, not (dGot s) || dCnt s /= 0))
```

When the loader pulses `frameEnd`, the current frame is over regardless of what
the decoder was in the middle of. The next state is `initDec` --- a hard reset, so
the following frame starts clean --- the output byte is `Nothing`, and `done` is
`True`. The interesting part is the last field, the `malformed` verdict:

> A frame is malformed if no byte ever arrived, or if a group was still owed data
> when the delimiter cut it off.

`not (dGot s)` catches the empty frame: two delimiters back to back, nothing
between them, `dGot` never set. `dCnt s /= 0` catches the truncated group: a code
byte promised `n` data bytes, the delimiter arrived before all `n` did, and the
counter never wound back to zero. Both are exactly the failures the pure
reference rejects --- an empty list, or a code byte "demanding more bytes than
remain" --- checked here in a single cheap disjunction at the one moment the whole
frame is known to be complete.

### No byte this cycle: hold

```haskell
      Nothing -> (s, (Nothing, False, False))
```

If the loader has no byte for the decoder this cycle --- the UART is mid-bit,
say --- the decoder does nothing at all: state unchanged, no output, no pulses. A
valve with nothing flowing through it. This is the overwhelmingly common case,
and it is one line.

### A byte arrives: code byte, or data byte?

Everything real happens when `Just b` arrives, and the split is the `dCnt s == 0`
guard, which is precisely the question *is the next byte a code byte?*

```haskell
      Just b
        | dCnt s == 0 ->
            let (out, s1) =
                  if dPend s
                    then (Just 0, s{dPend = False, dGot = True})
                    else (Nothing, s{dGot = True})
                s2 = startGroup s1 b
             in (s2, (out, False, False))
```

When `dCnt` is zero we are between groups, so `b` is a code byte --- but before we
interpret it, we settle a debt. If `dPend` is set, the *previous* group ended
owing an implied zero, and this is the moment to pay it: `out = Just 0`. That
timing is the subtle heart of the decoder, so let me state it plainly.

> The zero between two groups is emitted lazily --- not when the group ends, but
> when the *next* code byte arrives.

Why lazily? Because the zero only exists if there *is* a next group. A group that
turns out to be the frame's last is followed by the delimiter, not by another
code byte, and its implied zero is not part of the payload --- it is the boundary
itself. By deferring the zero until the next code byte, the decoder gets this for
free: if the next thing is a code byte, the zero was real and we emit it; if the
next thing is `frameEnd`, we reset and the pending zero evaporates, un-emitted,
exactly as it should. The `03 11 22 02 33 01` trace below shows both halves of
that bargain.

Once the debt is settled, `startGroup` interprets the code byte:

```haskell
  startGroup st c =
    let full = c == 255
        n = (unpack c :: Unsigned 8) - 1 -- c is 1..255 (never 0), so n is 0..254
     in if n == 0
          then st{dCnt = 0, dFull = full, dPend = not full}
          else st{dCnt = n, dFull = full}
```

`n = c - 1` is the group's data-byte count, undoing the encoder's `L + 1`. `full
= c == 255` records the continuation case. Then a fork on `n`:

- `n == 0` --- the code was `0x01`, an **empty group**. There is no data to wait
  for, so `dCnt` stays `0` (the very next byte is again a code byte), and we set
  `dPend = not full`. For `0x01`, `full` is `False`, so `dPend` becomes `True`:
  an empty group *is* a lone zero, and it owes one. (`0x01` is the only way `n`
  is zero; `full` is a formality here, always `False`, but writing `not full`
  keeps the rule uniform.)
- `n /= 0` --- a normal group. Arm the counter, `dCnt = n`, and remember whether
  it was full. The implied zero, if any, will be decided when the counter hits
  bottom, in the other arm.

### Inside a group: pass the byte, count down

```haskell
        | otherwise ->
            let cnt' = dCnt s - 1
                s' =
                  s
                    { dCnt = cnt'
                    , dGot = True
                    , dPend =
                        if cnt' == 0
                          then not (dFull s)
                          else dPend s
                    }
             in (s', (Just b, False, False))
```

When `dCnt` is non-zero, `b` is a data byte, and data bytes are transparent:
`out = Just b`, passed straight through unchanged (they were never touched by the
encoder --- COBS only ever *removed* the zeros, never disturbed the rest).
Decrement the counter to `cnt'`. And here is the mirror image of `startGroup`'s
pending logic: the instant the group empties, `cnt' == 0`, we set `dPend = not
(dFull s)`. A normal group just ended and owes its implied zero; a full (`0xFF`)
group ends owing nothing, because it was a continuation. That single `not dFull`
is the `0xFF`-is-special rule from the concept section, written once, doing all
its work.

### Watching it run

Take the frame from the figure, `03 11 22 02 33 01`, and feed it in byte by byte,
capped with `frameEnd`. The decoder should hand back `11 22 00 33 00`.

<figure class="cdec-fig" style="margin:2rem 0">
<svg class="cdec" viewBox="0 0 760 250" role="img" aria-labelledby="cdec-t cdec-d" xmlns="http://www.w3.org/2000/svg">
<title id="cdec-t">Decoding 03 11 22 02 33 01 back into 11 22 00 33 00</title>
<desc id="cdec-d">The top row is the COBS input 03 11 22 02 33 01 followed by a dashed 00 delimiter. The bottom row is the decoded output 11 22 00 33 00, column-aligned beneath the input. Data bytes 11, 22 and 33 drop straight down to the output. The code byte 03 arms a counter and produces no output. The code bytes 02 and 01 each manufacture an accent-highlighted zero in the output — the implied zero of the group that just ended — before re-arming. The delimiter produces done and drops the final group's still-pending zero.</desc>
<style>
.cdec{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.cdec .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.cdec .code{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.cdec .delim{fill:none;stroke:var(--accent);stroke-width:2;stroke-dasharray:5 4}
.cdec .zout{fill:var(--accent);opacity:0.16;stroke:var(--accent);stroke-width:2}
.cdec .thru{stroke:var(--fg-main);stroke-width:2;fill:none}
.cdec .mk{stroke:var(--accent);stroke-width:2;fill:none}
.cdec text{font-family:var(--sans)}
.cdec .val{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.cdec .valz{fill:var(--accent);font-family:var(--mono);font-size:15px}
.cdec .lab{fill:var(--fg-dim);font-size:12px}
.cdec .note{fill:var(--accent);font-size:11.5px}
.cdec .ah{fill:var(--fg-main)}
.cdec .ahA{fill:var(--accent)}
</style>
<defs>
<marker id="cdec-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="cdec-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahA"/></marker>
</defs>
<text class="lab" x="34" y="60" text-anchor="start">COBS in</text>
<rect class="code"  x="110" y="40" width="60" height="42" rx="5"/>
<rect class="box"   x="192" y="40" width="60" height="42" rx="5"/>
<rect class="box"   x="274" y="40" width="60" height="42" rx="5"/>
<rect class="code"  x="356" y="40" width="60" height="42" rx="5"/>
<rect class="box"   x="438" y="40" width="60" height="42" rx="5"/>
<rect class="code"  x="520" y="40" width="60" height="42" rx="5"/>
<rect class="delim" x="602" y="40" width="60" height="42" rx="5"/>
<text class="val" x="140" y="67" text-anchor="middle">03</text>
<text class="val" x="222" y="67" text-anchor="middle">11</text>
<text class="val" x="304" y="67" text-anchor="middle">22</text>
<text class="val" x="386" y="67" text-anchor="middle">02</text>
<text class="val" x="468" y="67" text-anchor="middle">33</text>
<text class="val" x="550" y="67" text-anchor="middle">01</text>
<text class="valz" x="632" y="67" text-anchor="middle">00</text>
<line class="thru" x1="222" y1="82" x2="222" y2="168" marker-end="url(#cdec-a)"/>
<line class="thru" x1="304" y1="82" x2="304" y2="168" marker-end="url(#cdec-a)"/>
<line class="thru" x1="468" y1="82" x2="468" y2="168" marker-end="url(#cdec-a)"/>
<path class="mk" d="M386,82 C386,120 386,132 386,168" marker-end="url(#cdec-aa)"/>
<path class="mk" d="M550,82 C550,120 550,132 550,168" marker-end="url(#cdec-aa)"/>
<line class="mk" x1="140" y1="82" x2="140" y2="120"/>
<text class="note" x="140" y="136" text-anchor="middle">arm 2 · no out</text>
<line class="mk" x1="632" y1="82" x2="632" y2="120"/>
<text class="note" x="632" y="132" text-anchor="middle">done ·</text>
<text class="note" x="632" y="147" text-anchor="middle">drop pending 0</text>
<text class="lab" x="34" y="196" text-anchor="start">bytes out</text>
<rect class="box"  x="192" y="172" width="60" height="42" rx="5"/>
<rect class="box"  x="274" y="172" width="60" height="42" rx="5"/>
<rect class="zout" x="356" y="172" width="60" height="42" rx="5"/>
<rect class="box"  x="438" y="172" width="60" height="42" rx="5"/>
<rect class="zout" x="520" y="172" width="60" height="42" rx="5"/>
<text class="val"  x="222" y="199" text-anchor="middle">11</text>
<text class="val"  x="304" y="199" text-anchor="middle">22</text>
<text class="valz" x="386" y="199" text-anchor="middle">00</text>
<text class="val"  x="468" y="199" text-anchor="middle">33</text>
<text class="valz" x="550" y="199" text-anchor="middle">00</text>
<text class="note" x="468" y="238" text-anchor="middle">accent zeros — manufactured when the next code byte arrives</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">One decode, column-aligned. Data bytes (<code>11</code>, <code>22</code>, <code>33</code>) fall straight through. The first code byte <code>03</code> only arms the counter — no output. Each later code byte (<code>02</code>, <code>01</code>) first pays the previous group's debt, manufacturing the accent <code>00</code>, then re-arms: the implied zero is born <em>at the next code byte</em>, not when its group ended. The delimiter raises <code>done</code> and drops the final group's still-pending zero — which is why <code>01</code>'s zero never appears, and the output ends at <code>33 00</code>, the original payload exactly.</figcaption>
</figure>

Trace it against the state:

1. **`03`** --- `dCnt` is `0`, `dPend` is `False`, so no output; `startGroup`
   reads `n = 2`, sets `dCnt = 2`.
2. **`11`** --- inside a group; emit `11`, `dCnt` falls to `1`.
3. **`22`** --- emit `22`, `dCnt` falls to `0`; the group is done, so `dPend =
   not dFull = True`. *Debt incurred.*
4. **`02`** --- `dCnt` is `0` and `dPend` is set, so emit the owed `0`; then
   `startGroup` reads `n = 1`, `dCnt = 1`. Output so far: `11 22 00`.
5. **`33`** --- emit `33`, `dCnt` falls to `0`, `dPend = True` again.
6. **`01`** --- `dPend` set, emit the owed `0`; `startGroup` reads `n = 0`, an
   empty group, `dCnt = 0`, `dPend = True`. Output: `11 22 00 33 00`.
7. **`frameEnd`** --- reset. `dGot` is `True` and `dCnt` is `0`, so `malformed`
   is `False`. The `dPend` left set by step 6 is simply discarded.

The output is `11 22 00 33 00` --- the payload we started from. Notice the whole
drama is in that last `dPend`: step 6 dutifully set it, and step 7 threw it away,
which is exactly right, because the final group's zero was the frame boundary,
not a byte. Emit it and we would have handed back `11 22 00 33 00 00`, one zero
too many. The lazy timing is what makes the difference free.

And the malformed cases fall straight out of the same law. Feed `05 11` then
`frameEnd`: the code `0x05` arms `dCnt = 4`, one data byte arrives, and the
delimiter finds `dCnt = 3 /= 0` --- truncated, malformed. Feed nothing between two
delimiters: `dGot` is `False` --- empty, malformed. Feed a lone `03`: `dGot` is
set but `dCnt = 2` still owes two bytes --- malformed. The decoder never has to
reach for a special error path; the verdict is a one-line consequence of the
state it was already keeping.

## `cobsEncodeStep`: encoding a byte

Encoding is the harder direction, and the reason is timing. The decoder could act
on each byte the instant it arrived, because a code byte tells it everything up
front. The encoder cannot: to emit a group's code byte it must first know the
group's *length*, and it cannot know the length until it has seen the zero (or
the 254th byte, or the end of input) that closes the group. So the encoder is a
two-beat machine --- **fill** a buffer, then **emit** what it holds --- and the
whole function is a `case` on which beat we are in.

```haskell
cobsEncodeStep :: EncSt -> (Maybe (BitVector 8, Bool), Bool) -> (EncSt, (Bool, Maybe (BitVector 8), Bool))
```

The input is a maybe-`(byte, is-last)` paired with `dsReady`, the downstream
ready line. The `is-last` flag rides along with each byte because, again, the
encoder needs to know when the stream ends to close the final group. `dsReady` is
back-pressure from whatever consumes the COBS bytes (the transmitter, via the
loader): the encoder may only push a byte on a cycle the consumer can take one.
The output is `(readyIn, mOut, done)` --- a ready line *back* toward the source,
the maybe-COBS-byte, and a `done` pulse when the frame is fully emitted. Two
ready lines, pointing opposite ways, because the encoder sits in the middle of a
pipeline and must talk to both ends.

### Filling: buffer a group, and the load-bearing order

```haskell
  EFilling -> case mIn of
    Nothing -> (s, (True, Nothing, False))
    Just (b, lst)
      | eFill s == 254 ->
          (s{eMode = EEmitting, eIx = 0, ePend = Just (b, lst)}, (True, Nothing, False))
      | b == 0 ->
          (s{eMode = EEmitting, eIx = 0, eFinal = lst, eLast = lst}, (True, Nothing, False))
      | lst ->
          ((store s b){eMode = EEmitting, eIx = 0, eLast = True, eFinal = False}, (True, Nothing, False))
      | otherwise ->
          (store s b, (True, Nothing, False))
```

Throughout `EFilling`, `readyIn` is `True`: this is the one mode that consumes
input, so it is the one mode that says "keep them coming." With no byte offered
(`Nothing`) it idles, ready. With a byte `(b, lst)` in hand, four guards decide
its fate, and *their order is load-bearing* --- this is the single most important
thing in the encoder, so we take the guards in order and dwell on the first.

**`eFill == 254` comes first, before even the zero check.** If the buffer already
holds 254 bytes, the group is full and must flush as a `0xFF` continuation ---
*whatever `b` is*. We switch to `EEmitting`, reset the emit cursor, and stash `b`
(with its `lst`) in `ePend` to be reprocessed into a fresh group after the flush.
Why must this outrank `b == 0`? Because a `0xFF` group carries no implied zero, so
if we let a zero landing on a full buffer fold *into* the full group, that zero
would simply vanish on decode. The comment in the source is blunt about it, and
it deserves quoting in full:

> A 254-byte group flushes as a `0xFF` continuation (no implied zero), then `b` is
> reprocessed via `ePend`. A zero landing here must terminate a *fresh* empty
> group, not fold into the full group.

So a zero arriving on a full buffer produces `…FF,<254>` and *then* `01` --- the
full group, then a fresh empty group for the zero --- never a single `0xFF` that
quietly eats it. Get the guard order wrong and the encoder loses data on exactly
the inputs a naive test suite is least likely to try. (We will see the regression
test that pins this down.)

The remaining three guards are the ordinary closes:

- **`b == 0`** --- the zero terminates the current group. Switch to `EEmitting` to
  flush what we have; the zero itself contributes no byte to the buffer (it is
  implied). Record `eFinal = lst` and `eLast = lst`: if this terminating zero was
  the stream's last byte, we will owe a final empty group after the flush, and
  the stream has ended.
- **`lst`** --- a non-zero last byte. `store` it, then switch to emit. `eLast =
  True` (the stream is over) but `eFinal = False` (a non-zero last byte owes no
  trailing group; the final group is the one this very byte is in).
- **otherwise** --- an ordinary non-zero, non-last byte. `store` it and stay in
  `EFilling`, ready for more. `store` writes `b` at index `eFill` and bumps the
  count: `st{eBuf = replace (eFill st) b (eBuf st), eFill = eFill st + 1}`.

### Emitting: drive code, then data, then whatever is owed

```haskell
  EEmitting
    | not dsReady -> (s, (False, Nothing, False))
    | eIx s <= eFill s ->
        let out :: BitVector 8
            out = if eIx s == 0 then fromIntegral (eFill s) + 1 else eBuf s !! (eIx s - 1)
         in (s{eIx = eIx s + 1}, (False, Just out, False))
```

In `EEmitting`, `readyIn` is `False` throughout --- the encoder is draining its
buffer, not accepting input. The first guard is the back-pressure gate: if
`dsReady` is low the consumer cannot take a byte, so we stall, holding all state,
emitting nothing. Nothing moves until downstream is ready.

When it is ready, the `eIx <= eFill` guard walks the group out. `eIx == 0` emits
the **code byte**, computed right here as `eFill + 1` --- the group's length plus
one, the encoder's half of the `L + 1` the decoder undoes. Every later index emits
a data byte, `eBuf !! (eIx - 1)`, and the cursor advances. So a group of `eFill`
bytes takes `eFill + 1` emit cycles: one code, then the data. Straightforward
tape-out of `code ++ group`.

The interesting question is what happens *after* the cursor runs past the data,
and the answer is a little cascade of owed work, tried in a deliberate order.

```haskell
    | eFinal s ->
        (s{eFill = 0, eIx = 0, eFinal = False}, (False, Nothing, False))
    | otherwise -> case ePend s of
        Just (pb, pl)
          | pb == 0 ->
              (s{eMode = EEmitting, eFill = 0, eIx = 0, ePend = Nothing, eFinal = pl, eLast = pl}, (False, Nothing, False))
          | pl ->
              ((store s{eFill = 0} pb){eMode = EEmitting, eIx = 0, ePend = Nothing, eLast = True}, (False, Nothing, False))
          | otherwise ->
              ((store s{eFill = 0} pb){eMode = EFilling, ePend = Nothing}, (False, Nothing, False))
        Nothing
          | eLast s -> (initEnc, (False, Nothing, True))
          | otherwise -> (s{eMode = EFilling, eFill = 0}, (False, Nothing, False))
```

**`eFinal` first.** If a final empty group is owed --- because the last input byte
was a zero --- clear `eFill` to `0` and loop back through `EEmitting`, which will
emit the `0x01` of an empty group (`eFill + 1 = 1`) and then fall through here
again with `eFinal` now `False`. This is the trailing-empty-group from the
concept section, made concrete: a payload ending in a zero needs one last `01`,
and this is where it comes from.

**Then `ePend`.** A stashed byte means we just flushed a full 254-group and must
fold that byte into a fresh group. Three sub-cases, mirroring the fill guards:

- **`pb == 0`** --- the stashed byte was a zero. The full group already flushed as
  `0xFF` (no implied zero); now the zero gets its *own* fresh empty group. Reset
  `eFill`, stay emitting, and set `eFinal`/`eLast` from `pl` --- if that zero was
  the last input byte, we still owe the final empty group after it. This is the
  `…FF,<254>,01,01` path in the flesh, and the reason the guard order upstream
  had to put `eFill == 254` first.
- **`pl`** --- the stashed byte is non-zero and last. `store` it in a fresh buffer
  and emit that one-byte group, marking `eLast`.
- **otherwise** --- a non-zero, non-last stashed byte. `store` it into a fresh group
  and return to `EFilling` to keep accumulating from there.

**Finally `Nothing`.** No stash, so the group we just emitted was clean. If
`eLast` is set the whole frame is done: reset to `initEnc` and pulse `done`.
Otherwise there is more payload coming, so drop back to `EFilling` with an empty
buffer.

### Watching it run

Run the payload `11 22 00 33 00` through, `is-last` on the final zero, `dsReady`
always high. It should produce `03 11 22 02 33 01` --- the figure's encoding, now
built rather than read.

<figure class="cenc-fig" style="margin:2rem 0">
<svg class="cenc" viewBox="0 0 760 288" role="img" aria-labelledby="cenc-t cenc-d" xmlns="http://www.w3.org/2000/svg">
<title id="cenc-t">The encoder's two modes and the work owed between them</title>
<desc id="cenc-d">Two large state boxes: EFilling on the left, buffer a group, and EEmitting on the right, drive code then data. EFilling has a self-loop labelled store non-zero byte. An accent arrow from EFilling to EEmitting is labelled with three triggers: b equals zero, last byte, or group full at 254; a note says the full case stashes the byte in ePend. EEmitting has a self-loop labelled emit code then data, gated on downstream ready. A lower arrow returns from EEmitting to EFilling labelled more input, group drained. A short accent arrow from EEmitting reaches a done terminal on the right labelled input ended, nothing owed. Below EEmitting a bracket lists the work owed before returning: eFinal for a final empty group, and ePend to reprocess a stashed byte.</desc>
<style>
.cenc{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.cenc .st{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.cenc .term{fill:var(--bg-main);stroke:var(--fg-main);stroke-width:2}
.cenc .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.cenc .hot{stroke:var(--accent);stroke-width:2.5;fill:none}
.cenc .loop{stroke:var(--fg-main);stroke-width:2;fill:none}
.cenc .bracket{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.cenc text{font-family:var(--sans)}
.cenc .name{fill:var(--fg-main);font-family:var(--mono);font-size:15px}
.cenc .sub{fill:var(--fg-dim);font-size:12px}
.cenc .lab{fill:var(--fg-main);font-size:12px}
.cenc .labA{fill:var(--accent);font-size:12px}
.cenc .dim{fill:var(--fg-dim);font-size:11.5px}
.cenc .ah{fill:var(--fg-main)}
.cenc .ahA{fill:var(--accent)}
</style>
<defs>
<marker id="cenc-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="cenc-aa" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahA"/></marker>
</defs>
<rect class="st" x="70" y="118" width="176" height="64" rx="8"/>
<rect class="st" x="430" y="118" width="196" height="64" rx="8"/>
<circle class="term" cx="700" cy="150" r="20"/>
<circle class="term" cx="700" cy="150" r="14"/>
<path class="loop" d="M120,118 C114,80 202,80 196,118" marker-end="url(#cenc-a)"/>
<path class="hot" d="M246,138 C330,120 350,120 428,138" marker-end="url(#cenc-aa)"/>
<path class="loop" d="M470,118 C464,80 592,80 586,118" marker-end="url(#cenc-a)"/>
<path class="wire" d="M428,168 C350,196 330,196 248,172" marker-end="url(#cenc-a)"/>
<line class="hot" x1="626" y1="150" x2="678" y2="150" marker-end="url(#cenc-aa)"/>
<text class="name" x="158" y="150" text-anchor="middle">EFilling</text>
<text class="sub"  x="158" y="169" text-anchor="middle">buffer a group</text>
<text class="name" x="528" y="150" text-anchor="middle">EEmitting</text>
<text class="sub"  x="528" y="169" text-anchor="middle">drive code + data</text>
<text class="dim"  x="158" y="72" text-anchor="middle">store non-zero byte</text>
<text class="labA" x="337" y="103" text-anchor="middle">b = 0 · last · full(254)</text>
<text class="dim"  x="337" y="212" text-anchor="middle">more input, group drained</text>
<text class="dim"  x="528" y="72" text-anchor="middle">emit code, then data (if dsReady)</text>
<text class="dim"  x="700" y="196" text-anchor="middle">input ended,</text>
<text class="dim"  x="700" y="210" text-anchor="middle">nothing owed</text>
<path class="bracket" d="M436,196 V204 H620 V196"/>
<text class="dim"  x="528" y="224" text-anchor="middle">owed before returning:</text>
<text class="labA" x="528" y="240" text-anchor="middle">eFinal (final empty group) · ePend (reprocess stashed byte)</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The encoder as a two-beat machine. <code>EFilling</code> buffers non-zero bytes (self-loop) until a byte closes the group (accent): a zero, the last byte, or the 254th byte — the last of which stashes the offending byte in <code>ePend</code>. <code>EEmitting</code> drives <code>code ++ group</code> out one byte per ready cycle, then settles any owed work before it leaves: <code>eFinal</code> emits the trailing empty group a payload-ending zero requires, and <code>ePend</code> folds a stashed byte into a fresh group. Only with nothing owed and the input ended does it reset and pulse <code>done</code>.</figcaption>
</figure>

Every emitted byte gets its own cycle, so the trace is longer than the decode,
but it breaks into three clean groups:

1. **`11`, `22`** (fill) --- non-zero, not last: `store` each; `eFill` climbs to
   `2`, and we stay in `EFilling`.
2. **`00`** (fill) --- the zero closes the group `[11 22]`; switch to `EEmitting`.
   `eFinal` and `eLast` stay `False`: this zero is not the last byte.
3. **emit** --- `eIx 0`: code `= eFill + 1 = 03`; then data `11`, then `22`. The
   cursor runs past the data with nothing owed (`eFinal` false, `ePend` none,
   `eLast` false), so back to `EFilling`. Out: `03 11 22`.
4. **`33`** (fill) --- `store`, `eFill = 1`.
5. **`00`** (fill, last) --- the zero closes the group `[33]`, and it is the last
   byte, so set `eFinal = eLast = True`. Switch to emit.
6. **emit** --- `eIx 0`: code `= eFill + 1 = 02`; then data `33`. Out:
   `03 11 22 02 33`.
7. **emit** --- cursor past the data; now `eFinal` fires, clearing `eFill` for one
   *more* empty group and looping.
8. **emit** --- code `= 0 + 1 = 01` for that final empty group. Then `ePend` is
   `Nothing` and `eLast` is `True`, so reset to `initEnc` and pulse `done`. Out:
   `03 11 22 02 33 01`.

Five bytes of payload became six on the wire, and steps 7–8 are where the extra
one is minted: `eFinal` adds the trailing `01` that a payload ending in a zero
always owes. That single byte *is* the "consistent overhead" --- the final group
has no zero in the payload to pay for its code byte, so it costs one, flat, and
COBS never charges more than one such byte per 254.[^overhead]

### The 254 boundary, where the encoder earns its length

Everything genuinely hard about the encoder lives at the full-group boundary, and
three vectors from the test suite map it exactly. All three feed non-zero runs
around the 254-byte cap; watch how the guard order pays off.

- **254 non-zero bytes, `[1..254]`**, the last flagged. The 254th byte arrives
  while `eFill` is still `253`, so `eFill == 254` does *not* fire --- the `lst`
  guard does. `store` fills the buffer to exactly 254 and emits one full group,
  code `0xFF` then the 254 bytes: `FF,<254>`. No trailing anything, because a
  non-zero last byte owes no final group. One full group, and done.
- **255 non-zero bytes, `[1..255]`**. Now the 255th byte (`lst`) arrives with
  `eFill` already at `254`, so `eFill == 254` fires *first*: flush `FF,<254>` and
  stash byte 255 in `ePend`. After the flush, `ePend`'s `pl` case stores it into a
  fresh group and emits `02,255`. Result: `FF,<254>` then `02,255`. The run split
  across the cap, exactly as a continuation should.
- **254 non-zero bytes then a `0x00`**, the zero flagged last. The 254 fill the
  buffer; the zero arrives with `eFill == 254`, so --- and this is the whole point
  of the guard order --- `eFill == 254` fires *before* `b == 0`: flush `FF,<254>`
  and stash the zero in `ePend`. After the flush, `ePend`'s `pb == 0` case emits a
  fresh empty group `01` for the zero and, because it was last, sets `eFinal`,
  which adds the final `01`. Result: `FF,<254>,01,01`. The zero survived as its
  own group instead of being swallowed by the `0xFF` --- the bug the guard order
  exists to prevent.

Three vectors, three faces of one boundary, and the encoder handles them with a
stash register and a carefully ranked `case`. That is what those extra fields and
that extra mode buy: an encoder that never drops a byte at the seam.

## The tests

The module is under a hundred lines of logic, and its tests pin it down the same
way the [CRC][crc]'s did --- from more than one direction at once --- but with a
tool the CRC only hinted at: a **pure reference model** standing in as an oracle.

There are, in fact, *two* COBS implementations in Tamal. The one we just read,
`Tamal.Loader.Cobs`, is the streaming, one-byte-at-a-time form the hardware runs
(the spec calls it §9). Beside it lives `Tamal.Wire.Cobs` (spec §5), a pure
list-to-list reference:

```haskell
cobsEncode :: [BitVector 8] -> [BitVector 8]
cobsDecode :: [BitVector 8] -> Maybe [BitVector 8]
```

`cobsEncode` is COBS written the easy way --- fold over a list, accumulate a group,
`emit` it at each zero --- with no state machine, no back-pressure, no `is-last`
flag, nothing but the algorithm. It is short enough to read and trust at a glance,
and *that* is its job: it is the specification made executable. The streaming
steps are the optimised, clocked implementation of the very same function, and
the tests weld the two together:

```haskell
testProperty "streaming decode of cobsEncode x reconstructs x" $ property $ do
  x <- forAll (Gen.list (Range.linear 0 300) genByteZeros)
  let (dec, bad) = decDrive (cobsEncode x)
  dec === x
  bad === False

testProperty "streaming encode equals cobsEncode (non-empty)" $ property $ do
  x <- forAll (Gen.list (Range.linear 1 300) genByteZeros)
  encDrive x === cobsEncode x
```

The first drives the streaming decoder over `cobsEncode x` --- the oracle's output
--- and demands the original `x` back, un-mangled and not malformed. The second is
the sharper claim: the streaming encoder, byte for byte, produces *exactly* what
the pure `cobsEncode` produces, for hundreds of random payloads. [Hedgehog][hedgehog]
manufactures the payloads with `genByteZeros`, a generator deliberately about
one-quarter zeros so that group boundaries --- the whole point of COBS --- come up
constantly rather than by luck.

But `genByteZeros` has a blind spot, and the suite knows it: a random
quarter-zero list almost never contains a 254-byte run of non-zeros, so it never
reaches the full-group machinery we spent so long on. So there is a second
generator, `genRuns`, that builds concatenated runs of up to 260 non-zero bytes
each --- straddling the cap on purpose --- and the same equivalence is asserted over
*it*:

```haskell
testProperty "streaming encode equals cobsEncode (boundary runs)" $ property $ do
  x <- forAll (Gen.filter (not . L.null) genRuns)
  encDrive x === cobsEncode x
```

Then the round-trips, both directions streaming, over both generators ---
`decDrive (encDrive x) === (x, False)` --- and, nailing the corner the guard order
turns on, the explicit regression:

```haskell
testCase "encode 254 non-zero then 0x00 == FF,<254>,01,01"
  $ encDrive (L.map fromIntegral [1 .. 254 :: Int] <> [0x00])
  @?= (0xFF : L.map fromIntegral [1 .. 254 :: Int]) <> [0x01, 0x01]
```

That last one is the guardrail bolted directly over the trap: if some future edit
ever lets a zero fold into a full group, this line goes red before the property
tests even get their turn. It is the same move the [CRC post][crc] admired --- a
hand-derived witness bolted to the workbench beside the sweeping property --- here
guarding the one input a random generator is least likely to stumble onto.

The shape of the whole suite is worth stepping back for. The pure model is the
*meaning* of COBS; the streaming steps are its *hardware*; and the property tests
are the proof that the hardware means what the model says, checked against a fresh
shower of payloads every run, with the awkward boundary explicitly seeded so it is
never left to chance. You get to write the algorithm twice --- once for clarity,
once for the clock --- and let the machine hold the two versions to each
other.[^purestream]

## What we read

Two pure step functions, no clock between them, and underneath them a whole
framing layer: a decoder that walks a COBS stream a group at a time and
manufactures each implied zero *lazily* --- at the next code byte, so the last
group's phantom zero falls away at the delimiter for free --- and an encoder that
buffers a group, flushes `code ++ group`, and guards the 254-byte seam so
carefully that a zero landing on a full group becomes its own honest little group
instead of vanishing. Beside them a pure reference model that is the same
algorithm written for people, and a property suite that welds the fast version to
the readable one.

The [transmitter][tx] and [receiver][rx] gave us a byte pipe; the [top][top]
sealed it; and today the pipe became a **protocol** --- a stream with a reserved
delimiter and a codec that honours it in both directions. What is still missing is
the little machine that *drives* these steps: the loader proper, the `mealy` that
catches `rxByte` strobes and clocks `cobsDecodeStep`, writes decoded words into
instruction memory, and runs `cobsEncodeStep` in reverse to drain the engine's
results back out while `txReady` is high. It wears the silhouette we have now read
five times --- a sum for its phase, a record for the rest, a step function beside a
`mealy` --- and it is where these two codecs finally get their clock. That is the
next thing we read. And past it, at last, the [Engine][intro] the whole project
has been circling.

[^consistent]: The "consistent" in Consistent Overhead Byte Stuffing is a promise
about the *worst case*. Older byte-stuffing schemes --- the escape-character trick
SLIP uses, say --- have overhead that depends on the data: a payload full of the
reserved byte can *double* in size, because every occurrence becomes two bytes.
COBS refuses that variance. Its overhead is at most one byte per 254 bytes of
payload, roughly 0.4%, no matter what the data is --- and, just as importantly, it
is *bounded and predictable*, which is what lets a fixed-size buffer on the far
end be sized with confidence. You pay a small, flat tax rather than an occasional
ruinous one. That flatness is the property the name is bragging about.

[^overhead]: Where does the "+1" go in the happy case? A payload with *no* zeros
and under 255 bytes encodes to a single group: one code byte, then the payload
verbatim. That is one byte of overhead for the whole frame --- the code byte the
delimiter cannot be. Every additional group (born at each zero, or at each 254-byte
cap) costs one more code byte, but *buys back* the zero it replaces, so a
zero-dense payload is close to break-even and a zero-free one pays the flat
one-per-254. The delimiter itself is the only byte that is pure tax, and it buys
the thing the whole scheme exists for.

[^postcard]: [postcard-rpc][pcrpc] sits on top of `postcard`, a `serde`
serializer for the embedded world that encodes Rust types into a compact,
`no_std`-friendly wire format. COBS is the framing underneath it --- the layer that
turns `postcard`'s byte blobs into delimited messages a receiver can find the
edges of. Meeting COBS there, in anger, doing a real job on a real link, is a
better teacher than any diagram: you internalise very quickly that a byte stream
is not a message stream until something draws the borders.

[^purestream]: The two-implementations pattern --- a pure list model beside a
streaming, clocked one, proven equal --- is not unique to COBS in Tamal; it is
becoming the house style. The pure model is where you *think*, unencumbered by
back-pressure and cycle timing; the streaming model is where you *ship*, and it
has to survive a consumer that stalls and a producer that dribbles. Keeping both
and testing one against the other means the gnarly version is never the only place
the algorithm is written down --- there is always a clean copy to check it against,
and to read when the clocked one stops making sense.
