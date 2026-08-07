+++
title = "Tamal: Frames on the Wire"
date = 2026-08-11T09:00:00
draft = true
description = "The pure host<->FPGA framing the loader realized a bit at a time, opened whole: Tamal.Wire and its Tamal.Wire.Cobs leaf, two pure [BitVector 8] reference models that never run on the fabric. COBS strips every 0x00 from a payload so one 0x00 can delimit frames --- a group is a code byte (len+1) then up to 254 non-zero bytes --- and the load-bearing subtlety is that a full 254-byte group flushes as a 0xFF continuation checked before the b == 0 case, so a zero landing on a full group opens a fresh empty group instead of vanishing. Above it, the frame layer: wordToBytesLE splitting a word into four little-endian bytes (the host transport is LE while the eSPI wire is MSB-first), crc8 folding the CRC post's crc8Update over a byte list, frameEncode appending the CRC then COBS-encoding then the delimiter, and frameDecode undoing it with a WireError sum of named failures. Above that, the message layer: ControlMsg, the 0x01/0x02/0x81 opcodes, encodeControl/decodeControl and encodeResult/decodeResult, bytesToWords regrouping LE quartets and failing unless a multiple of four. The reference-model role made explicit --- the loader implements the streaming form, this pure twin is the readable thing it is proven equal to --- and the tests that pin the boundary down: COBS round-trip over zero-dense and full-group generators, single-byte-corruption never a silent success, and the CRC-8/SMBUS check vector."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "wire", "loader"]
+++

The [engine is open entire][serdes]. The outside-in descent that began with
the [shape post's][shape] map of eight boxes ended when the [serdes
finale][serdes] opened the last of them, and everything the fabric does ---
fetch, decode, compute, drive the pins, record what happened --- has now been
read leaf by leaf. But a running engine on a board is only half a system. A
host has to load a program *into* it and read a trace back *out* of it, and
between the host and the fabric there is a wire. This post is about what
travels on that wire: not the electrical layer, not the UART that carries the
bytes, but the *format* --- the seams that turn a bare stream of bytes into
messages a host and an FPGA can agree on.

We have been here before, from the other side. The [loader][loader] was "the
streaming realization of the pure `Tamal.Wire` model," and the
[COBS-codec post][loader-cobs] realized the streaming byte-stuffing the loader
clocks a byte at a time. Both of those posts implemented something. This post
opens the thing they were implementing *against* --- the pure reference model
they were proven equal to. It is two files, `Tamal.Wire.Cobs` (68 lines) and
`Tamal.Wire` (158), and neither of them ever runs on the fabric. They are pure
`[BitVector 8]` functions over ordinary lists, the same "honest surplus" the
[serdes post's][serdes] unused `deserializeX1` was: a readable specification
that exists so the hardware has something to be checked against.

Two files, three layers, read bottom-up: the byte-stuffing that removes every
zero, the frame that wraps a payload in a CRC and a delimiter, and the messages
the frames carry. We start at the bottom, with the leaf that owes nothing to
anyone.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[isa]: https://balbi.sh/posts/tamal-isa/
[mem]: https://balbi.sh/posts/tamal-mem/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[serdes]: https://balbi.sh/posts/tamal-serdes/
[loader]: https://balbi.sh/posts/tamal-loader/
[loader-cobs]: https://balbi.sh/posts/tamal-loader-cobs/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa
[pcrpc]: https://github.com/jamesmunns/postcard-rpc
[cobs-wiki]: https://en.wikipedia.org/wiki/Consistent_Overhead_Byte_Stuffing

## The leaf that removes every zero

A bare stream of bytes has no seams. Send two messages back to back and the
receiver cannot tell where the first ends and the second begins --- unless you
reserve a byte to mean *stop here*. The obvious candidate is `0x00`, but it is
only usable as a delimiter if it never appears inside a message, and real
payloads are full of zeros. [**COBS** --- Consistent Overhead Byte
Stuffing][cobs-wiki] --- buys that guarantee cheaply: it removes *every* `0x00`
from a payload, at a cost of at most one extra byte per 254, so a single `0x00`
can be reserved --- unambiguously --- to mean *end of frame*.

`Tamal.Wire.Cobs` is the pure model of that transform, and its door lists two
functions:

```haskell
module Tamal.Wire.Cobs
  ( cobsEncode
  , cobsDecode
  ) where

import Clash.Prelude
import qualified Data.List as L
```

It is a **leaf**: it imports no other `Tamal.Wire.*` module, only the prelude
and `Data.List`. That isolation is deliberate --- COBS knows nothing about
CRCs, opcodes, or frames; it is a pure list-to-list transform, and everything
above it in the stack treats it as a black box that removes zeros and puts them
back.

The encoder groups the input:

```haskell
cobsEncode :: [BitVector 8] -> [BitVector 8]
cobsEncode = go []
 where
  go grp [] = emit grp
  go grp (b : bs)
    | L.length grp == 254 = emit grp <> go [] (b : bs) -- group full: flush, reprocess b
    | b == 0 = emit grp <> go [] bs -- zero terminates the group (implied)
    | otherwise = go (grp <> [b]) bs
  emit grp = fromIntegral (L.length grp + 1) : grp
```

Read `emit` first, because it defines the shape of the output. A **group** is a
*code byte* followed by its data bytes, and the code byte is `length + 1` --- so
an empty group emits `[0x01]`, a group of three data bytes emits `[0x04, …]`.
The `+ 1` is what keeps zeros out of the code byte itself: the smallest code is
`0x01`, never `0x00`, so no code byte can ever collide with the delimiter.

Then `go` walks the input, accumulating non-zero bytes into `grp`. Three arms,
and the order they are written in is the whole subtlety of the algorithm:

- **`b == 0`** terminates the group. The zero is *consumed* --- it never appears
  in the output --- and its presence is recorded *implicitly*, by the fact that
  a new group starts after it. A code byte less than `0xFF` means "this group
  was closed by a zero the encoder ate," and the decoder puts that zero back.
- **`otherwise`** appends the non-zero byte and keeps going.
- **`length grp == 254`** is checked *first*, before either of the others, and
  that ordering is load-bearing.

The load-bearing case is worth slowing down for, because it is the one bug this
whole design is arranged to avoid. A group can hold at most 254 data bytes ---
the code byte is `length + 1`, and `254 + 1 = 255 = 0xFF` is the largest byte.
When a group fills to 254, it must be flushed, and the flush code `0xFF` carries
a special meaning: *this group was full, not closed by a zero*. There is no
implied zero after a `0xFF` group.

Now imagine a zero arrives exactly when the group is full. If the `b == 0` case
ran first, the zero would fold into the full group --- which flushes as `0xFF`,
which carries *no* implied zero --- and the zero would silently vanish on
decode. Checking `length == 254` first prevents that: the full group flushes as
`0xFF` and reprocesses `b` in a *fresh* empty group, so a zero landing on a full
boundary terminates that fresh group and survives as an implied zero. The
source comment spells it out, and there is a regression test pinned to exactly
this input:

```haskell
testCase "cobsEncode 254 non-zero then 0x00 == FF,<254>,01,01"
  $ cobsEncode (run254 <> [0x00])
  @?= (0xFF : run254) <> [0x01, 0x01]
```

The `0xFF` flushes the full group, and the trailing `[0x01, 0x01]` is a fresh
empty group (code `0x01`) closed by the zero (another `0x01`). Fold the zero
into the full group and that `[0x01, 0x01]` disappears --- and so does the zero.
This is the same ordering the [streaming COBS post][loader-cobs] had to get
right in its `s -> i -> (s, o)` form: same algorithm, same trap, two
realizations, and the differential tests weld them together.

## Putting the zeros back

`cobsDecode` is the inverse, and it carries the totality discipline the whole
series leans on: it returns `Maybe`, `Nothing` on anything malformed.

```haskell
cobsDecode :: [BitVector 8] -> Maybe [BitVector 8]
cobsDecode bytes
  | L.null bytes = Nothing -- empty is not a valid frame
  | L.elem 0 bytes = Nothing -- a valid (delimiter-stripped) frame has no 0x00
  | otherwise = go bytes
 where
  go [] = Just []
  go (code : rest) =
    let n = fromIntegral code - 1 -- data bytes in this group
     in do
          (grp, more) <- takeExactly n rest
          if L.null more
            then Just grp -- final group: no trailing zero
            else do
              decoded <- go more
              Just (grp <> (if code == 255 then [] else [0]) <> decoded)
```

The two guards reject the two ways a delimiter-stripped frame can be malformed
on its face: it cannot be empty, and it cannot contain a `0x00` --- because COBS
by construction removed all of them, so a zero in the input means corruption.
Everything else is handled by `go`, one group at a time. It reads the code
byte, takes exactly `code - 1` data bytes via `takeExactly` (which returns
`Nothing` if the input is too short --- a code byte demanding more bytes than
remain), and then decides whether to reinsert a zero. The final group ---
`more` is empty --- gets no trailing zero, because nothing came after it. An
interior group gets a zero *unless* its code was `0xFF`, because `0xFF` means
"full group, no implied zero," the exact flag `cobsEncode` set.

That single `if code == 255 then [] else [0]` is the decoder's half of the
load-bearing case. It is why the full-group flush and the zero-terminated group
decode back to different things, and why the regression test round-trips.

The round-trip law is stated as a property, and it is tested over two
generators for a reason:

```haskell
testProperty "cobsDecode . cobsEncode == Just" $ property $ do
  xs <- forAll (Gen.list (Range.linear 0 300) genByteZeros)
  cobsDecode (cobsEncode xs) === Just xs
```

`genByteZeros` is zero-dense --- it produces many group boundaries by emitting
frequent zeros --- but by design it *cannot* build a 254-byte non-zero run, so
it never reaches the full-group cap. A second generator, `genRuns`,
concatenates non-zero runs up to 260 long, each optionally followed by a single
zero, so it crosses the 254 boundary and exercises the full-group / trailing-zero
interaction directly. Two generators, one law, and the boundary the first one
cannot reach is exactly where the algorithm is subtlest.

## From bytes to a word, and back

With the zero-stuffing in hand, we climb to `Tamal.Wire`, and its first job is
smaller than COBS: turn a 32-bit instruction word into bytes. The engine speaks
32-bit words; the wire speaks bytes; something has to choose an order.

```haskell
wordToBytesLE :: BitVector 32 -> Vec 4 (BitVector 8)
wordToBytesLE = reverse . unpack

bytesToWordLE :: Vec 4 (BitVector 8) -> BitVector 32
bytesToWordLE = pack . reverse
```

`unpack` splits the word into a `Vec 4 (BitVector 8)` with the most significant
byte first; `reverse` flips it to **little-endian** --- least significant byte
first, so `0xAABBCCDD` becomes `<0xDD, 0xCC, 0xBB, 0xAA>` (ISA §4). This is a
detail worth naming out loud, because it runs *against* the grain of everything
the bus side of the engine did: the [CRC post][crc] and the [serdes
post][serdes] both unpacked bytes **MSB-first** onto the eSPI wire, because that
is what the eSPI spec demands. Here, the *host transport* is little-endian ---
the natural order for a host CPU writing words to a serial port. The two
orderings live in different worlds and never meet: the eSPI wire is MSB-first,
the host word transport is LE, and `wordToBytesLE` is the seam that keeps them
straight.

The inverse is `pack . reverse`, and `bytesToWordLE (wordToBytesLE w) == w`
falls out by construction --- `reverse` is its own inverse and `pack`/`unpack`
are [total, zero-cost reinterpretations][isa]. The test states it as a property
and pins the concrete vector:

```haskell
testCase "wordToBytesLE 0xAABBCCDD == [DD,CC,BB,AA]"
  $ toList (wordToBytesLE 0xAABBCCDD)
  @?= [0xDD, 0xCC, 0xBB, 0xAA]
```

## The CRC, one layer up

A frame needs integrity, and the [CRC post][crc] already built the primitive:
`crc8Update`, one byte folded into a running CRC-8 (polynomial `0x07`, initial
`0x00`, MSB-first). `Tamal.Wire` reuses it, unchanged, one layer up:

```haskell
crc8 :: [BitVector 8] -> BitVector 8
crc8 = L.foldl' crc8Update 0
```

That is the whole function --- fold `crc8Update` over a byte *list*, starting
from `0`. The CRC block did the hard per-byte arithmetic; the frame layer just
sequences it over a payload. And because it is the CRC-8/SMBUS parameterization,
it satisfies the standard check vector, which the test uses as an independent
oracle:

```haskell
testCase "crc8 matches CRC-8/SMBUS check vector (0xF4)"
  $ crc8 [fromIntegral (fromEnum c) | c <- "123456789"]
  @?= 0xF4
```

The string `"123456789"` is the canonical CRC check input, and `0xF4` is the
published CRC-8/SMBUS result. If the polynomial or the fold order ever drifted,
this one line would catch it against a number no Tamal code produced.

## The frame: CRC, then stuff, then delimit

Now the three pieces --- CRC, COBS, delimiter --- assemble into a frame. The
encoder is one line, and the order of operations is the entire format:

```haskell
frameEncode :: [BitVector 8] -> [BitVector 8]
frameEncode logical = cobsEncode (logical <> [crc8 logical]) <> [0]
```

Read it inside out. Start with the *logical* bytes (an opcode and its payload).
Append the CRC of those bytes. COBS-encode the whole `logical ++ [crc]` blob ---
which removes every `0x00` from it. Then, and only then, append a single `0x00`
delimiter. The delimiter is safe precisely because COBS guaranteed there are no
other zeros in front of it: the one `0x00` on the wire is unambiguously the end
of the frame.

<figure>
<svg class="wirew" viewBox="0 0 760 200" role="img" aria-labelledby="wirew-t wirew-d" xmlns="http://www.w3.org/2000/svg">
  <title id="wirew-t">A wire frame built in four layers</title>
  <desc id="wirew-d">A byte pipeline shown as four stacked rows. Row one, the logical frame, is an opcode byte followed by payload bytes. Row two appends a CRC-8 byte, accented, to the end. Row three COBS-encodes the result: a code byte leads each group and every 0x00 has been removed. Row four appends a single 0x00 delimiter byte, accented, at the very end; it is the only zero on the wire.</desc>
  <style>
    .wirew text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; fill: var(--fg, #222); }
    .wirew .lbl { font-family: system-ui, sans-serif; font-size: 12px; fill: var(--fg-dim, #777); }
    .wirew .cell { fill: none; stroke: var(--fg-dim, #999); stroke-width: 1; }
    .wirew .accent { fill: var(--accent, #c25); fill-opacity: 0.16; stroke: var(--accent, #c25); }
    .wirew .code { fill: var(--fg-dim, #999); fill-opacity: 0.12; }
  </style>
  <text class="lbl" x="8" y="26">logical</text>
  <rect class="cell" x="120" y="14" width="70" height="24"/><text x="132" y="31">opcode</text>
  <rect class="cell" x="190" y="14" width="60" height="24"/><text x="200" y="31">pay</text>
  <rect class="cell" x="250" y="14" width="60" height="24"/><text x="260" y="31">pay</text>
  <rect class="cell" x="310" y="14" width="60" height="24"/><text x="320" y="31">pay</text>
  <text class="lbl" x="8" y="70">+ CRC-8</text>
  <rect class="cell" x="120" y="58" width="70" height="24"/><text x="132" y="75">opcode</text>
  <rect class="cell" x="190" y="58" width="60" height="24"/><text x="200" y="75">pay</text>
  <rect class="cell" x="250" y="58" width="60" height="24"/><text x="260" y="75">pay</text>
  <rect class="cell" x="310" y="58" width="60" height="24"/><text x="320" y="75">pay</text>
  <rect class="cell accent" x="370" y="58" width="60" height="24"/><text x="382" y="75">crc</text>
  <text class="lbl" x="8" y="114">COBS</text>
  <rect class="cell code" x="120" y="102" width="52" height="24"/><text x="130" y="119">code</text>
  <rect class="cell" x="172" y="102" width="70" height="24"/><text x="184" y="119">opcode</text>
  <rect class="cell" x="242" y="102" width="52" height="24"/><text x="252" y="119">pay</text>
  <rect class="cell code" x="294" y="102" width="52" height="24"/><text x="304" y="119">code</text>
  <rect class="cell" x="346" y="102" width="52" height="24"/><text x="356" y="119">pay</text>
  <rect class="cell accent" x="398" y="102" width="52" height="24"/><text x="410" y="119">crc</text>
  <text class="lbl" x="460" y="118">no 0x00 inside</text>
  <text class="lbl" x="8" y="158">+ delim</text>
  <rect class="cell code" x="120" y="146" width="52" height="24"/><text x="130" y="163">code</text>
  <rect class="cell" x="172" y="146" width="70" height="24"/><text x="184" y="163">opcode</text>
  <rect class="cell" x="242" y="146" width="52" height="24"/><text x="252" y="163">pay</text>
  <rect class="cell code" x="294" y="146" width="52" height="24"/><text x="304" y="163">code</text>
  <rect class="cell" x="346" y="146" width="52" height="24"/><text x="356" y="163">pay</text>
  <rect class="cell accent" x="398" y="146" width="52" height="24"/><text x="410" y="163">crc</text>
  <rect class="cell accent" x="450" y="146" width="44" height="24"/><text x="462" y="163">00</text>
  <text class="lbl" x="506" y="162">the one zero on the wire</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">A frame is built in four steps. The <em>logical</em> bytes are an opcode and its payload; <code>frameEncode</code> appends the CRC-8 (accented), COBS-encodes the whole blob so every interior <code>0x00</code> is removed and each group gains a leading code byte, then appends a single <code>0x00</code> delimiter (accented) --- the only zero on the wire, and therefore an unambiguous end-of-frame marker.</figcaption>
</figure>

The decoder runs the pipeline backwards, and every step that can fail names its
failure:

```haskell
frameDecode :: [BitVector 8] -> Either WireError [BitVector 8]
frameDecode wire = do
  stripped <- stripDelim wire
  content <- maybe (Left BadCobs) Right (cobsDecode stripped)
  (logical, crc) <- splitLastByte content
  if crc8 logical == crc
    then Right logical
    else Left BadCrc
```

Strip the trailing `0x00` delimiter (`stripDelim`, failing `ShortFrame` on empty
or `BadCobs` if the last byte is not a zero), COBS-decode (lifting the codec's
`Nothing` to `BadCobs`), split off the trailing CRC byte, and verify it against
a freshly recomputed CRC. Four steps, each in the `Either WireError` monad, so
the first failure short-circuits and names itself.

That `WireError` sum is the same "total, named failures" discipline as the
[ISA post's][isa] `DecodeError`:

```haskell
data WireError
  = BadCrc          -- CRC byte did not match the recomputed CRC
  | BadCobs         -- malformed COBS or missing delimiter
  | UnknownOpcode (BitVector 8)
  | ShortFrame      -- decoded frame lacks an opcode and/or CRC byte
  | BadPayloadLen   -- payload length invalid for the opcode
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

Five ways a frame can be wrong, each a constructor, none of them an exception or
a crash. A decoder that returns `Either WireError` cannot silently accept a
corrupt frame --- the caller must handle every arm --- and the property test
proves the pipeline never lies:

```haskell
testProperty "single-byte corruption is never a silent success" $ property $ do
  xs <- forAll (Gen.list (Range.linear 1 32) genByte)
  let f = frameEncode xs
  i <- forAll (Gen.int (Range.linear 0 (L.length f - 1)))
  let f' = [if j == i then x `xor` 1 else x | (j, x) <- L.zip [0 ..] f]
  frameDecode f' /== Right xs
```

Flip a single bit anywhere in a valid frame and decode must *not* return the
original bytes. It might return a `Left` of some `WireError`, or a `Right` of
*different* bytes --- but never a silent success on corrupted input. That is the
CRC earning its byte, checked at every position in the frame.

## The messages the frames carry

The top layer gives the frames meaning. A frame is just an opcode and a payload;
a *message* is what those mean. Two directions, host-to-FPGA control and
FPGA-to-host results, and a small type for the control plane:

```haskell
data ControlMsg
  = LoadProgram [BitVector 32]  -- instruction words to load into the instr BRAM
  | Trigger                     -- start-of-run pulse
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

Three opcodes name the traffic (§8.1), and `0x00` is conspicuously *not* among
them, because it is the delimiter and can never be an opcode:

```haskell
opLoadProgram = 0x01
opTrigger     = 0x02
opTraceDrain  = 0x81
```

Encoding a control message is opcode-prefix plus payload, run through
`frameEncode`:

```haskell
encodeControl :: ControlMsg -> [BitVector 8]
encodeControl (LoadProgram ws) =
  frameEncode (opLoadProgram : L.concatMap (toList . wordToBytesLE) ws)
encodeControl Trigger = frameEncode [opTrigger]
```

`LoadProgram` splits each 32-bit word into its four little-endian bytes and
concatenates them behind the opcode; `Trigger` is a lone opcode with no payload.
Decoding reverses it and enforces the per-opcode payload rules:

```haskell
decodeControl :: [BitVector 8] -> Either WireError ControlMsg
decodeControl wire = do
  logical <- frameDecode wire
  case logical of
    [] -> Left ShortFrame
    (op : payload)
      | op == opLoadProgram -> LoadProgram <$> bytesToWords payload
      | op == opTrigger ->
          if L.null payload then Right Trigger else Left BadPayloadLen
      | otherwise -> Left (UnknownOpcode op)
```

Every branch is total: an empty logical frame is `ShortFrame`, a `Trigger` with
a non-empty payload is `BadPayloadLen`, an opcode outside the set is
`UnknownOpcode op` carrying the offending byte. And `LoadProgram` defers to
`bytesToWords`, which regroups the little-endian byte payload back into words and
refuses anything that is not a clean multiple of four:

```haskell
bytesToWords :: [BitVector 8] -> Either WireError [BitVector 32]
bytesToWords [] = Right []
bytesToWords (a : b : c : d : rest) =
  (bytesToWordLE (a :> b :> c :> d :> Nil) :) <$> bytesToWords rest
bytesToWords _ = Left BadPayloadLen
```

Four bytes make a word; zero bytes left over is success; one, two, or three
trailing bytes is `BadPayloadLen`. A program is a whole number of 32-bit
instructions or it is not a program.

The result direction is the mirror. `encodeResult` wraps a drained ring
word-stream --- the REVISION word, the records, the HALT terminator (§8.3) ---
behind the `opTraceDrain` opcode, and `decodeResult` unwraps it:

```haskell
encodeResult ws = frameEncode (opTraceDrain : L.concatMap (toList . wordToBytesLE) ws)

decodeResult wire = do
  logical <- frameDecode wire
  case logical of
    [] -> Left ShortFrame
    (op : payload)
      | op == opTraceDrain -> bytesToWords payload
      | otherwise -> Left (UnknownOpcode op)
```

Same shape as the control path, same LE byte regrouping, same named failures ---
and the *content* of that word-stream, the records the ring holds, is the
subject of the [next post](#what-we-read). The two directions round-trip in
tests (`decodeControl . encodeControl == Right`, `decodeResult . encodeResult ==
Right`), and each rejects the other's opcodes: a `Trigger` frame fed to
`decodeResult` comes back `Left (UnknownOpcode 0x02)`, because the result
decoder answers only to `0x81`.

## The reference-model role, made explicit

Nothing in either file is synthesizable. `cobsEncode` builds an unbounded list
with `<>`; `bytesToWords` recurses over a list of runtime-unknown length;
`frameDecode` returns `Either` --- these are ordinary Haskell over `[BitVector
8]`, and no fabric ever runs them. So why do they exist, if the [loader][loader]
already implements the streaming form the hardware actually clocks?

Because the streaming form needs something to be *right against*. The
[loader][loader] walks the wire a byte per cycle, threading state through a
mealy machine; `Tamal.Wire` describes the same result in one pure pass over a
list. The two are welded together by differential property tests --- feed both
the same input, assert the same output --- so the readable model is the
*specification* and the streaming machine is the *implementation*, and the tests
prove they agree. This is the third time the series has used this move: the
[serdes post's][serdes] `deserializeX1`, the `Tamal.Trace` model the [bus
post][bus] kept pointing at, and now the wire format. Each is a pure twin kept
alive not because the fabric calls it, but because a specification you can read
is what lets you trust the machine you cannot.

## What we read

The pure wire format, opened whole. `Tamal.Wire.Cobs` first --- the leaf that
removes every `0x00` so one `0x00` can delimit frames, its groups a code byte
`(len + 1)` and up to 254 non-zero data bytes, its one subtle case the full
group flushed as `0xFF` *before* the zero case is checked, so a zero on a full
boundary opens a fresh group instead of vanishing. Then `Tamal.Wire` above it:
`wordToBytesLE` splitting a word into little-endian bytes (the host transport LE,
against the eSPI wire's MSB-first), `crc8` folding the [CRC post's][crc]
`crc8Update` over a list, `frameEncode` appending the CRC then COBS-encoding then
the delimiter, and `frameDecode` undoing it through a `WireError` sum of named
failures. And the message layer on top: `ControlMsg`, the `0x01`/`0x02`/`0x81`
opcodes, the control and result codecs, `bytesToWords` insisting on whole words.
All of it pure, all of it a specification the [loader][loader] and the [streaming
COBS codec][loader-cobs] were proven equal to.

The wire carries a program *in* and a trace *out*. We have read the program side
--- `LoadProgram`, the words, the LE bytes. The trace side ended at
`encodeResult`, wrapping a word-stream this post never looked inside: the
REVISION word, the records, the HALT terminator. Those records --- what a
`CAPTURE` packs, what a `MARK` carries, how the ring pushes them without ever
tearing a two-word record in half --- are the `Tamal.Trace` model, the pure twin
the [bus post][bus] kept promising. That is the next post, and with it both
formats, in and out, are done --- and the series turns, at last, to the impure
shell that makes the pins physical.
