+++
title = "Tamal: The Record and the Ring"
date = 2026-08-12T09:00:00
draft = false
description = "The pure twin the bus post kept promising: Tamal.Trace, sixty-four lines that never run on the fabric, holding the record format the engine's inline emitter is checked against. Three shapes --- Capture (one word, tag 00), Mark (two words, tag 10, label then payload), Halt (one word, tag 11, trap and reason and overflow and status) --- and encodeRecord laying each into 32-bit words with the exact bitCoerce tuples the engine builds inline in captureWord, markLabelWord and haltWith, so the two can be read side by side and a test can prove them equal. Then ringPush, the overflow-safe, record-atomic push: three cases --- already overflowed drops and latches the sticky flag, fits advances the pointer by the record's whole word count, else overflow drops and latches --- and the reason atomicity matters is Mark's two words, because a torn MARK desyncs the host's parse. The pure model of the drop-on-full, never-past-the-limit, terminator-slot-reserved contract that the bus post met a word at a time. Both formats, in and out, are now done; next the series crosses into the impure shell that makes the pins physical."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "trace", "espi"]
+++

The [wire post][wire] left a word-stream sealed. `encodeResult` wrapped a
drained ring --- a REVISION word, some records, a HALT terminator --- behind an
opcode and never looked inside. This post looks inside. It is the smallest file
of the whole descent, sixty-four lines, and it is the pure twin the [bus
post][bus] kept promising: every time the engine packed a `CAPTURE` or a `MARK`
or a `HALT` inline, a comment said *mirrors `Trace.encodeRecord`*. `Tamal.Trace`
is that mirror.

<!-- more -->

[wire]: https://balbi.sh/posts/tamal-wire/
[bus]: https://balbi.sh/posts/tamal-engine-bus/
[mem]: https://balbi.sh/posts/tamal-mem/
[isa]: https://balbi.sh/posts/tamal-isa/
[serdes]: https://balbi.sh/posts/tamal-serdes/

Like `Tamal.Wire`, nothing here runs on the fabric. `encodeRecord` returns a
list; `ringPush` recurses over one. The engine emits at most one word per cycle,
inline, in the fast synchronous path the [bus post][bus] read. This module
exists so the *layout* lives somewhere readable, and a test can prove the fast
path and the readable model agree. Two functions on the door:

```haskell
module Tamal.Trace
  ( Record (..)
  , encodeRecord
  , ringPush
  ) where
```

## Three shapes

A run leaves behind a stream of records, and there are exactly three kinds:

```haskell
data Record
  = Capture (BitVector 4) (BitVector 8)          -- nbits (1..8), sampled byte
  | Mark (BitVector 14) (BitVector 32)           -- label, payload
  | Halt Bool (BitVector 3) Bool (BitVector 8)   -- trap, reason, overflow, status
```

These are the same three the [bus post][bus] met from the engine side. `Capture`
reports a sampled byte with its valid-bit count; `Mark` carries a host↔trace
correlation label and a register payload; `Halt` terminates the run, folding in
the sticky trap and overflow flags, a 3-bit reason, and a status byte. What the
engine built with hand-rolled `bitCoerce` calls, this type names.

## One encoder, laid in field rulers

`encodeRecord` is the whole point of the file --- three lines, one per shape:

```haskell
encodeRecord :: Record -> [BitVector 32]
encodeRecord = \case
  Capture n b        -> [bitCoerce (0b00 :: BitVector 2, 0 :: BitVector 18, n, b)]
  Mark lbl pl        -> [bitCoerce (0b10 :: BitVector 2, 0 :: BitVector 16, lbl), pl]
  Halt trp rsn ovf st -> [bitCoerce (0b11 :: BitVector 2, 0 :: BitVector 17, rsn, trp, ovf, st)]
```

Read these against the engine's inline builders and they are *identical* tuples.
`captureWord` was `bitCoerce (0b00, 0 :: BitVector 18, nbits, byte)`;
`markLabelWord` was `bitCoerce (0b10, 0 :: BitVector 16, lbl)`; `haltWith` built
`bitCoerce (0b11, 0 :: BitVector 17, reason, trap, ovf, status)`. Same tags, same
zero padding, same field order. The engine open-codes them for speed; this
gathers them in one place you can actually read. (The REVISION preamble lives in
the engine, not here --- this is the *record* encoder, nothing more.)

Every shape sums to a clean 32 bits, and the two-bit tag column is what a host
reads first to know which shape follows:

<figure>
<svg class="trec" viewBox="0 0 720 210" role="img" aria-labelledby="trec-t trec-d" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:720px;height:auto;display:block;margin:0 auto">
  <title id="trec-t">The three record shapes as 32-bit field rulers</title>
  <desc id="trec-d">Three rows of bit-field rulers, each row summing to 32 bits. CAPTURE is one word: a two-bit tag 00, eighteen zero bits, a four-bit nbits field, and an eight-bit byte. MARK is two words: word one is tag 10, sixteen zero bits, and a fourteen-bit label; word two is a thirty-two-bit payload. HALT is one word: tag 11, seventeen zero bits, a three-bit reason, a one-bit trap flag, a one-bit overflow flag, and an eight-bit status. The two-bit tag column on the left of each first word is accented.</desc>

  <g fill="none" stroke="var(--fg-dim,#999)" stroke-width="1">
    <rect x="120" y="18" width="28" height="26" fill="var(--accent,#c25)" fill-opacity="0.16" stroke="var(--accent,#c25)"/>
    <rect x="148" y="18" width="252" height="26" fill="var(--fg-dim,#999)" fill-opacity="0.10"/>
    <rect x="400" y="18" width="56" height="26"/>
    <rect x="456" y="18" width="112" height="26"/>
    <rect x="120" y="74" width="28" height="26" fill="var(--accent,#c25)" fill-opacity="0.16" stroke="var(--accent,#c25)"/>
    <rect x="148" y="74" width="224" height="26" fill="var(--fg-dim,#999)" fill-opacity="0.10"/>
    <rect x="372" y="74" width="196" height="26"/>
    <rect x="120" y="112" width="448" height="26"/>
    <rect x="120" y="168" width="28" height="26" fill="var(--accent,#c25)" fill-opacity="0.16" stroke="var(--accent,#c25)"/>
    <rect x="148" y="168" width="238" height="26" fill="var(--fg-dim,#999)" fill-opacity="0.10"/>
    <rect x="386" y="168" width="42" height="26"/>
    <rect x="428" y="168" width="14" height="26"/>
    <rect x="442" y="168" width="14" height="26"/>
    <rect x="456" y="168" width="112" height="26"/>
  </g>

  <g fill="var(--fg,#222)" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="12" text-anchor="middle">
    <text x="134" y="35">00</text>
    <text x="274" y="35">0 (18)</text>
    <text x="428" y="35">nbits·4</text>
    <text x="512" y="35">byte·8</text>
    <text x="134" y="91">10</text>
    <text x="260" y="91">0 (16)</text>
    <text x="470" y="91">label·14</text>
    <text x="344" y="129">payload·32</text>
    <text x="134" y="185">11</text>
    <text x="267" y="185">0 (17)</text>
    <text x="407" y="185">rsn·3</text>
    <text x="435" y="185">t</text>
    <text x="449" y="185">o</text>
    <text x="512" y="185">st·8</text>
  </g>

  <g fill="var(--fg-dim,#777)" font-family="system-ui,sans-serif" font-size="12">
    <text x="8" y="34">CAPTURE</text>
    <text x="8" y="90">MARK w1</text>
    <text x="8" y="128">MARK w2</text>
    <text x="8" y="184">HALT</text>
  </g>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The three record shapes as 32-bit field rulers, each field's width drawn in proportion to its bit count. Each first word opens with a two-bit tag (accented) the host reads to know which shape follows; every row spans a full 32 bits. In <code>HALT</code>, <code>t</code> and <code>o</code> are the single-bit trap and overflow flags. <code>MARK</code> is the only two-word record --- its label word then a full 32-bit payload --- which is exactly why the push has to be atomic.</figcaption>
</figure>

The [test][isa]-style oracle is a handful of literal-shift assertions --- `Capture
8 0xA5` must equal `0b00 << 30 | 8 << 8 | 0xA5`, `Mark 0x1234 0xDEADBEEF` must be
two words with the label in the first and the payload verbatim in the second ---
each computed by a different arithmetic than `bitCoerce`, so a drift in field
placement would show as a mismatch against a number no `bitCoerce` produced.

## The push that never tears

The other function is the ring discipline, made pure:

```haskell
ringPush ptr limit ovf ws
  | ovf       = (ptr, True, [])
  | fits      = (ptr + count, False, ws)
  | otherwise = (ptr, True, [])
 where
  count = fromIntegral (L.length ws)
  fits  = L.length ws > 0 && (ptr + count - 1) <= limit
```

Three cases, and they are the whole [drop-on-full contract][mem] the bus post met
a word at a time. Already overflowed: drop the record, keep the sticky flag high.
Fits --- the last index this record would occupy, `ptr + count - 1`, stays within
`limit` --- write every word and advance the pointer by the record's *whole* word
count. Otherwise: drop, and latch overflow. The pointer never lands past `limit`,
so the [terminator slot][mem] beyond it is always free for the HALT that ends the
run.

The load-bearing word is *atomically*. `ringPush` writes all of a record's words
or none of them --- where the engine's [`pushWord`][bus] went one word per cycle,
this goes record at a time. The reason is `Mark`: it is two words, and a `Mark`
half-written --- its label word in the ring, its payload word dropped at the limit
--- desyncs the host's parse, because the host reads the label, expects a payload
word next, and finds the HALT terminator instead. Both words or neither. The test
pushes ten one-word records into a ring with `limit = 3` and checks the invariants
the contract promises: the pointer never passes `limit + 1`, at most four words
are ever written, and the sticky overflow flag ends `True`.

## What we read

Sixty-four lines, and both formats are now closed. `Tamal.Trace` is the record
side made pure: three `Record` shapes --- `Capture`, `Mark`, `Halt` ---
`encodeRecord` laying each into 32-bit words with the exact `bitCoerce` tuples the
[engine builds inline][bus], and `ringPush` enforcing the drop-on-full,
never-past-the-limit, record-atomic push whose atomicity exists to keep a two-word
`MARK` from tearing. Not because the fabric calls any of it --- the fabric emits a
word a cycle in its own fast path --- but because a readable specification is what
lets a test prove the fast path honest. The third time the series has kept a pure
twin alive for exactly this reason, after the [serdes finale's][serdes]
`deserializeX1` and the [wire post's][wire] whole reference model.

The wire carries a program in; the trace carries a run out; both are pure and
done. What is left is not a *format* at all --- it is the impure shell that turns
`(value, enable)` pairs into real pins that float and drive. That crossing, out to
the silicon, is the next post.
