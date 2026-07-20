+++
title = "Tamal: The CRC block"
date = 2026-07-20T09:00:00
description = "Reading Tamal's CRC-8 block end to end — the module and its export list, what import Clash.Prelude swaps in, why the type must be BitVector 8 -> BitVector 8 -> BitVector 8, how a left fold unrolls into a shift register, and why, once you see a CRC as a polynomial remainder, the SMBUS check value and the residue law hold by construction."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "crc"]
[extra]
math = true
+++

As mentioned before [Tamal] is an FPGA eSPI exerciser with its
gateware written in [Haskell] and compiled to verilog with [Clash].

In a [previous] post we presented a very short introduction on
Haskell, just enough such that the code blocks below don't look too
foreign. Today we start looking at one of the simplest blocks within
Tamal: its CRC8 engine.

The code is so small that it fits easily in less than one screenfull
of content on a regular smartphone screen, but as we will see, it
hides a good amount of interesting operations due to Haskell's
expressiveness.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[previous]: https://balbi.sh/posts/tamal-haskell-primer/
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

As promised, the entire source fits in a screenfull:

```haskell
module Tamal.Crc
  ( crc8Update
  ) where

import Clash.Prelude

crc8Update :: BitVector 8 -> BitVector 8 -> BitVector 8
crc8Update crc byte = foldl step crc (unpack byte :: Vec 8 Bit)
 where
  step :: BitVector 8 -> Bit -> BitVector 8
  step c inBit
    | feedbackBit == high = shifted `xor` 0x07
    | otherwise = shifted
   where
    feedbackBit = msb c `xor` inBit
    shifted = c `shiftL` 1
```

The first thing we notice are the top three lines:

```haskell
module Tamal.Crc
  ( crc8Update
  ) where
```

A Haskell file opens by naming itself. `module Tamal.Crc`
declares this module's name, and the name is not free-form: it
mirrors the path on disk. `Tamal.Crc` lives in
`src/Tamal/Crc.hs`, a dot for each directory separator, the same
way a Rust `crate::module` path or a Java package tracks its
folder. The compiler leans on that correspondence to find
modules, so the name and the location can never quietly drift
apart.

The parentheses are the more interesting part. They are the
module's **export list** — the complete, deliberate enumeration of
what the outside world is allowed to see:

> A module is a wall with a door in it, and the export list is
> the door.

Only `crc8Update` is on that list, so only `crc8Update` leaves
the file. Everything else — and in a moment we will meet `step`,
the helper that does the actual bit-twiddling — stays private,
sealed behind the wall. Had the author written
`module Tamal.Crc where` with no list at all, Haskell would
export *everything* by default, `step` included, and the module's
public surface would sprawl to match its implementation. The
explicit list is a choice to expose one name and hide the rest.

That choice buys more than tidiness. `step` is an internal detail
of how *this* CRC happens to be computed; nothing outside should
depend on it, name it, or test it directly. Keeping it off the
export list reserves the author's right to rewrite it —
table-driven, byte-parallel, whatever comes later — without
breaking a single caller. The public contract is exactly one
function wide. (It is also why the tests we read at the end
exercise the byte-level `crc8Update` and never reach for `step`:
`step` is not theirs to reach.)

## The prelude swap

The line under the module header looks like ceremony and is
anything but:

```haskell
import Clash.Prelude
```

Every Haskell file begins in a world someone else furnished. That
furniture is the *prelude*: the batch of names, types, and
functions that are simply *there* before you import anything.
Ordinary Haskell hands you the standard `Prelude` — `Int`,
`Bool`, lists, `foldl`, `map`, the familiar fittings.
`import Clash.Prelude` throws that set out and moves a different
one in.

What arrives instead is a hardware vocabulary: the `Bit`,
`BitVector`, `Vec`, and `Signal` types from the [primer], the
`high` and `low` bit literals, `msb`, `shiftL`, `xor`, `unpack` —
and versions of the everyday functions, `foldl` among them, that
operate on `Vec`s and lower to gates. This one line is the
difference between a program that computes and a description that
becomes a circuit:

> Swapping the prelude is what turns a Haskell file into a
> hardware description.

Clash leans on the swap so hard that it *shadows* the list
versions of common names. That has a visible consequence we will
run into in the test file: there, `map` and `++` would mean the
`Vec` versions, so gluing plain lists together has to reach for a
list comprehension and `<>` instead. File that away; for now the
thing to see is that the lone `foldl` two lines down is one of
these swapped names. It is the `Vec` fold, and it will unroll into
wiring rather than spin a runtime loop. Nothing in the module
works without this import; it is the line that says *compile me to
hardware*.

## The type, and why it has to be this one

Now the signature, which under the primer's reading is already
half the documentation:

```haskell
crc8Update :: BitVector 8 -> BitVector 8 -> BitVector 8
```

Two arrows, so — remembering that `->` associates to the right and
every function takes one argument — this is "give me a
`BitVector 8`, then another `BitVector 8`, and I hand back a
`BitVector 8`." Name the three roles and the intent appears: the
first argument is the **running CRC**, the second is the
**incoming byte**, and the result is the **new running CRC**. It
is an accumulator update, one byte at a time.

The type could have been written other ways, and the two decisions
behind it are both load-bearing.

The first is `BitVector 8` rather than `Unsigned 8` or a `Word`.
A `BitVector 8` is eight wires with *no* arithmetic meaning
attached — the type of a thing you `xor`, shift, and index into,
not a thing you add. `Unsigned 8` would have invited `+`, `*`, and
carry, arithmetic this function never performs. Choosing
`BitVector` states plainly that a CRC is bit-manipulation, not
counting, and the type system then holds the code to that promise:
reach for `+` on a `BitVector` by accident and the compiler
objects. It is the primer's "wire-width checker," applied one
level up — the *kind* of value, not just its width, is part of the
contract.

The second decision is the argument order: running CRC first, byte
second. That order is chosen so the function is **fold-shaped**.
Recall currying — `crc8Update crc` is a legal value on its own, a
function still waiting for a byte, meaning "advance *this* running
CRC by one more byte." That is precisely the shape a left fold
wants for its combining step, and it is exactly how the tests will
run the CRC across a whole message: `foldl crc8Update 0 bytes`
reads left to right as "start at zero, fold in each byte." Swap
the arguments and that reading falls apart. The signature is
written to make the fold obvious.

One last thing the type says by saying nothing. Every `BitVector 8`
in, produces a definite `BitVector 8` out — no missing cases, no
error, no `Maybe`. That totality *is* a truth table, $f : (\text{crc},
\text{byte}) \to \text{crc}$, same inputs giving the same output every
time, which is the primer's definition of combinational logic
verbatim. `crc8Update` is a block of gates wearing a Haskell type. But
the body only makes sense once we have the one idea the whole file is
a transcription of.

## A CRC is a remainder

Strip the acronym away and a CRC is a **remainder** — the leftover
from a division, carried out in a number system with exactly two
digits.

Here is the whole trick. Take a stream of bits and read it as the
coefficients of a polynomial: the byte `00000111` becomes
$x^2 + x + 1$, a one in each place where the polynomial has a
term. Bytes, messages, whole packets — all just polynomials whose
coefficients are either $0$ or $1$. Arithmetic on these
coefficients is done modulo two, which collapses into one
startling simplification: $1 + 1 = 0$, there are no carries, and
**addition, subtraction, and XOR are all the same operation**.
That single fact is why CRC hardware is cheap — every
"subtraction" below is just an `xor`.

The divisor in this division has a name, the **generator
polynomial**. Tamal's is the one the module comment spells out,

$$ G(x) = x^8 + x^2 + x + 1 $$

whose coefficients, written as bits, are `1 0000 0111` — nine bits,
`0x107`, degree eight. Look at the low eight of those bits: `0000
0111`, `0x07`, the very constant sitting in the source. The ninth
bit, the $x^8$ term, is the one that will "fall off the top" of an
eight-bit register in a moment; hold onto it.

To take the CRC of a message $M$ is to do one thing:

> Shift the message up by eight bits, divide by $G$, and keep the
> remainder.

In symbols, $C = (M \cdot x^8) \bmod G$. The shift by $x^8$ —
eight zero bits of headroom appended below the message — is what
makes room for an eight-bit remainder to live, and it is also,
quietly, the thing that will make the residue trick at the end of
this post work. A well-chosen $G$ has the property that almost any
corruption of the message changes the remainder, so the remainder
acts as a compact fingerprint: change the packet, change the
fingerprint, and the receiver notices.

The smallest possible example is worth doing by hand, because we
are about to meet it again as a unit test. Let $M$ be the one-byte
message `0x01`, which as a polynomial is simply $1$. Shift up by
$x^8$ and reduce modulo $G$:

$$ x^8 \equiv x^2 + x + 1 \pmod{G} $$

because $x^8 = G + (x^2 + x + 1)$ and $G \equiv 0$ by definition of
"modulo $G$." The remainder is $x^2 + x + 1$, which is `0000 0111`,
which is `0x07`. Remember that number. The code is about to
reproduce it out of shifts and XORs, and the two answers are going
to match.

## The left fold

With the division in mind, the body is short:

```haskell
crc8Update crc byte = foldl step crc (unpack byte :: Vec 8 Bit)
```

Two moves live on that line: turn the byte into bits, then fold
over them.

`unpack byte :: Vec 8 Bit` takes the opaque eight-wire `BitVector`
and re-presents it as a `Vec 8 Bit`, a sequence of eight
individual `Bit`s you can walk one at a time. The annotation `::
Vec 8 Bit` is needed because `unpack` is polymorphic — it will
produce whatever type the surrounding code asks for — so we pin
the target explicitly. The ordering is a convention worth naming:
index `0` of the vector is the most significant bit, so walking
the vector head to tail walks the byte **MSB first**, which is
what this CRC (and eSPI, and SMBus) require.

Then the fold. From a purely functional standpoint a left fold[^fold]
is the most ordinary thing in the world, and it has nothing to do
with mutation:

> `foldl` threads an accumulator through a sequence, left to
> right, handing the running result and the next element to a
> combining function, over and over.

Written out, `foldl step crc [b0, b1, .. b7]` is nothing but a
nest of calls:

```text
step (step (step (.. step (step crc b0) b1 ..) b6) b7)
```

The `crc` seeds the accumulator. `step` folds in `b0` to produce a
*new* accumulator; that value feeds into `step` alongside `b1` to
produce the next; and so on down the byte until `b7` yields the
result. Nothing is ever overwritten. Each `step` **returns**[^returns]
a fresh CRC value and the previous one is simply never referenced
again. What in C is a register clobbered inside a loop is here a value
handed along a chain of pure functions — the same dance, performed
without a single mutation. This is the "mapping, not procedure" point
from the primer made concrete: the accumulator doesn't *change*, it is
*replaced*, eight times, by eight applications of one pure function.

And because the vector has a length the compiler knows — `8`, right
there in `Vec 8 Bit` — the hardware reading is the one the primer
promised:

> A fold over a fixed-size `Vec` is a pipeline unrolled in space,
> not a loop unrolled in time.

Clash does not synthesize a counter and a loop. It lays down eight
copies of `step`'s logic, the output of each feeding the next, a
chain of combinational stages that transforms the whole byte
within a single clock. That chain is a shift register with XOR
taps — a **linear-feedback shift register**, the textbook circuit
for a bit-serial CRC — except Clash *derived* it from a `foldl`
instead of asking anyone to draw it. The accumulator is the
register's contents, `step` is the tap logic, and the fold is the
wiring between the stages.

<figure class="lfsr-fig" style="margin:2rem 0">
<svg class="lfsr" viewBox="0 0 760 200" role="img" aria-labelledby="lfsr-t lfsr-d" xmlns="http://www.w3.org/2000/svg">
<title id="lfsr-t">CRC-8 Galois shift register for polynomial 0x07</title>
<desc id="lfsr-d">Eight one-bit cells, b7 (the MSB) on the left through b0 (the LSB) on the right. Data shifts left. The MSB is XORed with the incoming message bit to form the feedback, which is folded back into the cells at bit positions 2, 1 and 0 — the set bits of the polynomial 0x07 = x squared plus x plus 1.</desc>
<style>
.lfsr{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.lfsr .ff{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.lfsr .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.lfsr .fb{stroke:var(--accent);stroke-width:2;fill:none}
.lfsr .gate{fill:var(--bg-main);stroke:var(--accent);stroke-width:2}
.lfsr .plus{stroke:var(--accent);stroke-width:2}
.lfsr .node{fill:var(--accent)}
.lfsr text{font-family:var(--sans)}
.lfsr .bit{fill:var(--fg-main);font-family:var(--mono);font-size:16px}
.lfsr .idx{fill:var(--fg-dim);font-size:13px}
.lfsr .lead{fill:var(--fg-main);font-size:13px}
.lfsr .tap{fill:var(--code-fg);font-family:var(--mono);font-size:13px}
.lfsr .ah{fill:var(--fg-main)}
.lfsr .ahf{fill:var(--accent)}
</style>
<defs>
<marker id="lfsr-a" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ah"/></marker>
<marker id="lfsr-af" markerWidth="8" markerHeight="6" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" class="ahf"/></marker>
</defs>
<line class="wire" x1="133" y1="144" x2="101" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="221" y1="144" x2="189" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="309" y1="144" x2="277" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="397" y1="144" x2="365" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="485" y1="144" x2="453" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="573" y1="144" x2="541" y2="144" marker-end="url(#lfsr-a)"/>
<line class="wire" x1="661" y1="144" x2="629" y2="144" marker-end="url(#lfsr-a)"/>
<rect class="ff" x="45" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="133" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="221" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="309" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="397" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="485" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="573" y="116" width="54" height="56" rx="6"/>
<rect class="ff" x="661" y="116" width="54" height="56" rx="6"/>
<text class="bit" x="72" y="150" text-anchor="middle">b7</text>
<text class="bit" x="160" y="150" text-anchor="middle">b6</text>
<text class="bit" x="248" y="150" text-anchor="middle">b5</text>
<text class="bit" x="336" y="150" text-anchor="middle">b4</text>
<text class="bit" x="424" y="150" text-anchor="middle">b3</text>
<text class="bit" x="512" y="150" text-anchor="middle">b2</text>
<text class="bit" x="600" y="150" text-anchor="middle">b1</text>
<text class="bit" x="688" y="150" text-anchor="middle">b0</text>
<text class="idx" x="72" y="190" text-anchor="middle">MSB</text>
<text class="idx" x="688" y="190" text-anchor="middle">LSB</text>
<line class="fb" x1="72" y1="116" x2="72" y2="67"/>
<line class="wire" x1="14" y1="56" x2="60" y2="56" marker-end="url(#lfsr-a)"/>
<line class="fb" x1="83" y1="56" x2="688" y2="56"/>
<line class="fb" x1="556" y1="56" x2="556" y2="133"/>
<line class="fb" x1="644" y1="56" x2="644" y2="133"/>
<line class="fb" x1="688" y1="56" x2="688" y2="116" marker-end="url(#lfsr-af)"/>
<circle class="node" cx="556" cy="56" r="3.5"/>
<circle class="node" cx="644" cy="56" r="3.5"/>
<circle class="gate" cx="72" cy="56" r="11"/>
<line class="plus" x1="66" y1="56" x2="78" y2="56"/>
<line class="plus" x1="72" y1="50" x2="72" y2="62"/>
<circle class="gate" cx="556" cy="144" r="11"/>
<line class="plus" x1="550" y1="144" x2="562" y2="144"/>
<line class="plus" x1="556" y1="138" x2="556" y2="150"/>
<circle class="gate" cx="644" cy="144" r="11"/>
<line class="plus" x1="638" y1="144" x2="650" y2="144"/>
<line class="plus" x1="644" y1="138" x2="644" y2="150"/>
<text class="lead" x="14" y="46">message bit</text>
<text class="lead" x="385" y="44" text-anchor="middle">feedback = MSB ⊕ message bit</text>
<text class="tap" x="562" y="80">x²</text>
<text class="tap" x="650" y="80">x¹</text>
<text class="tap" x="694" y="80">x⁰</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The CRC-8 as a Galois shift register (polynomial <code>0x07</code>). Each step shifts the byte left; the MSB is XORed with the next message bit to form the feedback, which is folded back in at the <code>x²</code>, <code>x¹</code> and <code>x⁰</code> taps — the set bits of <code>0x07</code>. <code>crc8Update</code> <em>is</em> this circuit with the flip-flops replaced by wires: Clash unrolls the <code>foldl</code> into eight combinational copies, one per bit, so a whole byte is absorbed in a single clock.</figcaption>
</figure>

## `step`: one bit at a time

Which leaves the heart of the module, the private helper the
export list keeps to itself:

```haskell
step :: BitVector 8 -> Bit -> BitVector 8
step c inBit
  | feedbackBit == high = shifted `xor` 0x07
  | otherwise = shifted
 where
  feedbackBit = msb c `xor` inBit
  shifted = c `shiftL` 1
```

The type tells the whole story of what `step` is *for*. It takes a
running CRC and a **single** `Bit` — not a byte, one bit — and
returns the next running CRC. That is the entire kernel of the
design; everything else in the file is `foldl` invoking this
function eight times per byte. Read `crc8Update` and `step`
together and the division of labour is clean: `step` knows how to
absorb one bit, and the fold knows how to do it eight times.

Take the two `where` bindings first, since the guards are written
in terms of them.

The binding ``shifted = c `shiftL` 1`` slides the CRC one place
toward the most significant bit: a zero enters at the bottom, and
the top bit drops off the end and is gone. In the language of the
previous section, shifting left by one is **multiplying the
polynomial by $x$**. The bit that just dropped off is the
coefficient that *would have* become the $x^8$ term — the exact
term $G$ exists to cancel.

The other binding, ``feedbackBit = msb c `xor` inBit``, takes the
CRC's current top bit (`msb c` reads out the most significant
`Bit`) and XORs in the incoming message bit. This is the point
where the message actually enters the division.

Now the guards. The `|` is a **guard**: a list of boolean tests,
each paired with a result, where the first test that succeeds wins
and `otherwise` is the catch-all that is always true. So the
function reads: if `feedbackBit == high` — `high` being the `Bit`
whose value is one — the answer is ``shifted `xor` 0x07``;
otherwise the answer is just `shifted`.

Here is why that is division and not sleight of hand. Multiplying
by $x$ can push the polynomial up to degree eight, one bit wider
than the register holds. When the coefficient of that
overflow — `feedbackBit` — is one, the value has grown "too big" and
must be brought back into range by subtracting $G$. Subtracting $G$
means XORing `0x107`; but the $x^8$ bit already fell off the top in
the shift, so the only bits left to cancel are the low eight,
`0x07`. When `feedbackBit` is zero there was no overflow and there
is nothing to subtract. That is the complete rule the five lines
encode:

> Multiply by $x$; if the bit that fell off says so, XOR the
> polynomial back in to reduce modulo $G$.

We can watch it produce the very number the algebra promised. Fold
`step` from `crc = 0` across the byte `0x01`, whose bits MSB-first
are seven zeros and then a one:

- On each of the first seven bits, `msb c` is `0` and `inBit` is
  `0`, so `feedbackBit` is `0`; `shifted` is `0` shifted, still
  `0`. The accumulator never leaves zero.
- On the final bit, `inBit` is `1` while `msb c` is still `0`, so
  `feedbackBit` is `0 xor 1 = 1`. `shifted` is
  ``0 `shiftL` 1 = 0``, the guard fires, and the result is
  `0 xor 0x07 = 0x07`.

The fold ends at `0x07` — exactly the $x^8 \bmod G = x^2 + x + 1$
we worked out by hand. The shifts-and-XORs and the polynomial
division are not two implementations that happen to agree; they
are one statement written twice. Fold that per-bit rule across all
eight bits and the accumulator becomes
$(\text{byte} \cdot x^8) \bmod G$, the byte's contribution to the
running remainder; chain `crc8Update` across a whole message,
seeding from `0` and feeding each result back as the next `crc`,
and you have divided the entire message by $G$, one byte at a
time.

## The tests

The module is twenty-eight lines. Its test file is shorter still,
and the two of them together make a quiet point worth pausing on:
a claim this small can be pinned down *completely*, from more than
one direction. The tests come in two flavours that answer two
different questions.

```haskell
crc8 :: [BitVector 8] -> BitVector 8
crc8 = L.foldl' crc8Update 0

tests =
  testGroup "Crc"
    [ testCase "crc8Update 0 0x01 == 0x07" $
        crc8Update 0 0x01 @?= 0x07
    , testCase "CRC-8/SMBUS check \"123456789\" == 0xF4" $
        crc8 [fromIntegral (fromEnum c) | c <- "123456789"] @?= 0xF4
    , testProperty "residue law" $ property $ do
        msg <- forAll (Gen.list (Range.linear 0 32) genByte)
        crc8 (msg <> [crc8 msg]) === 0
    ]
```

First the helper. `crc8Update` works one byte at a time, but a
message is a *list* of bytes, so the tests define `crc8 = L.foldl'
crc8Update 0` — fold the per-byte update across the whole list,
seeding from the initial value `0`. This is the second fold in the
story, and it sits one level up from the first: the source folds
*bits* into a byte's CRC, the test folds *bytes* into a message's
CRC. Same `foldl` idea, twice, at two scales.

A small Clash wrinkle hides in that `L.`. Because `import
Clash.Prelude` shadowed the list functions with `Vec` ones back at
the top of the module, the plain-list fold has to be summoned
from `Data.List` under the qualifier `L`, hence `L.foldl'`. The
note at the bottom of the real test file is flagging the same shadow
for `map` and `++`, which is why the message is stitched together
with a list comprehension and `<>` rather than the names you might
expect. That one import casts its shadow all the way into the
tests.

### The oracle tests

The first two entries are **HUnit** cases — plain, hand-checked
assertions of the shape "this exact input yields this exact
output," where `@?=` reads "the actual value should equal the
expected one."

`crc8Update 0 0x01 @?= 0x07` is the smallest witness in the whole
project. We have now derived it *twice* — once as $x^8 \bmod G$ with
pencil and polynomials, once by tracing the fold through eight
bits — and here it is bolted to the workbench so it can never
silently change. If some future refactor ever breaks the
multiply-by-$x$-then-reduce rule, this one-liner is the first thing
to go red.

The second case checks a number that is not the author's at all —
it is the **world's**. `CRC-8/SMBUS` is a catalogued CRC, and every
catalogued CRC ships with a standard *check value*: the CRC of the
nine ASCII bytes `"123456789"`. For this polynomial and these
parameters that value is `0xF4`, the same `0xF4` printed in
reference tables and reproduced by independent implementations in
every language. Asserting it turns the test suite into a cross-check
against the entire ecosystem: if Tamal agrees with the catalog on
`"123456789"`, then it is computing the CRC it *claims* to be — the
one an eSPI or SMBus partner on the other end of the wire will also
be computing — and not some subtly different cousin. These two are
**oracle tests**: known questions with known answers, one derived
in-house, one borrowed from the standard.

### The property test

The third entry is a different animal. [Hedgehog] is a
property-based testing library: instead of one example, you state a
*law* that must hold for *every* input, and the library manufactures
hundreds of random inputs trying to break it. The law here is the
one the whole block exists to satisfy:

```haskell
crc8 (msg <> [crc8 msg]) === 0
```

Read it aloud: take any message `msg`, compute its CRC, append that
CRC byte to the end of the message, and run the CRC over the whole
extended thing — and you get zero. The line above it, `forAll
(Gen.list (Range.linear 0 32) genByte)`, is Hedgehog drawing `msg`
as a random list of up to thirty-two random bytes, with `genByte`
(from the shared `Test.Gen`) producing fully-defined bytes. Every
run of the suite fires this across a fresh shower of messages. And
on the day it *does* fail, Hedgehog does the thing that earns its
keep: it **shrinks** the counterexample, paring a giant random
failure down to the smallest message that still breaks the law, and
hands you that minimal case instead of a haystack.

Why is this the *right* property — the single law worth elevating
above all the specific numbers? For two reasons, one practical and
one that finally cashes in the polynomial setup.

The practical reason is that it is exactly how Tamal *uses* this
CRC on the bus. The [introduction post][intro]'s
`peripheral_io_read.s` ends its response phase by reading the
trailing CRC byte and checking that the running residue has driven
to zero — `rdsr t2, CRC` followed by `bnez t2, bad_crc`. A good
packet followed by its CRC leaves a residue of zero; anything else
is a corrupted packet. The property test is that runtime check,
lifted out of the eSPI program and asserted directly against the
pure function. It tests the law the hardware actually leans on.

The mathematical reason is the payoff for all the polynomials.
Appending the CRC byte $C$ to the message $M$ builds the number
$M \cdot x^8 + C$ — the message shifted up eight bits to make room,
with $C$ dropped into the low byte. But $C$ was *defined* as the
remainder of $M \cdot x^8$ divided by $G$, and adding a remainder
back onto its own dividend lands you on a clean multiple of the
divisor. (Adding and subtracting are the same XOR here, which is
why *appending* $C$ and *subtracting off the remainder* come to the
same thing.) So $M \cdot x^8 + C$ is divisible by $G$ with nothing
left over — a valid codeword — and running the CRC over it is
dividing a multiple of $G$ by $G$:

> The residue is zero because a message-plus-CRC is, by
> construction, a multiple of the generator polynomial.

There is one subtlety the shift handles for free. Running the CRC
over the codeword multiplies it by a further $x^8$ — the fold's own
headroom — before reducing. But that changes nothing in the
direction we care about: the codeword is already a multiple of $G$,
and $x^8$ times a multiple of $G$ is still a multiple of $G$, so the
remainder is still zero. (The extra factor is harmless the other
way too: because $G$ ends in a one — its constant term is $1$ —
multiplying by $x$ is reversible modulo $G$, so the headroom can
never manufacture a false zero out of a *bad* packet.) The property
holds for every message because the algebra leaves it no other
option, and Hedgehog's hundreds of random trials stand guard over
the day a careless edit breaks that algebra.

## What we read

Twenty-eight lines, one exported name, and underneath it a complete
CRC: a module wall with a single door, a prelude swap that turns the
file into a circuit, a curried type written fold-shaped on purpose,
a left fold that Clash unrolls into a linear-feedback shift
register, and a five-line `step` that is polynomial division
disguised as shift-and-XOR. Beside it, a test file that stakes the
whole thing down from three sides at once — a witness we derived by
hand, the world's catalogued check value, and the residue law the
eSPI engine trusts at runtime.

The [primer] promised that the syntax would stop standing between
you and the hardware. This is the first block where that promise
gets cashed: there is nothing left in these lines that is *merely*
Haskell — every one of them is a statement about gates, or about the
algebra those gates carry out. Next in the series we take on a block
with a clock inside it — the UART — where `Signal` stops being a
footnote and the `mealy` from the primer finally earns its perch at
the top of a module.

[^fold]: A **fold** — also called `reduce`, `accumulate`, `inject`, or
`aggregate`, depending on the language — is the higher-order function
that collapses a whole sequence into a single value by threading an
accumulator through it: start from a seed, then repeatedly apply a
two-argument combining function to "the result so far" and "the next
element." It comes handed: a *left* fold (`foldl`) brackets from the
left and walks front to back, a *right* fold (`foldr`) from the other
end. Fold is bread-and-butter in the languages descended from the
lambda calculus — Lisp and Scheme, the ML family and OCaml, Haskell —
where it sits beside `map`, `filter`, `scan`, and `zip` in a shared
vocabulary of combinators; the *array* languages (APL, J, BQN, Uiua)
know it just as intimately, usually as a single-glyph primitive.
Imperative languages have long had it too, under a scatter of
names — Python's `functools.reduce`, C++'s `std::accumulate`, Ruby's
`inject`, JavaScript's `reduce`, C#'s LINQ `Aggregate` — but have
tended to reach for an explicit loop-with-an-accumulator instead;
Rust is arguably the first mainstream systems language to make `fold`
(and `reduce`) a reached-for, idiomatic tool rather than a
functional-programming curiosity, largely by building it into the
iterator adaptors everyone already uses.

[^returns]: Technically Haskell functions never **return**, rather
they **evaluate to** a value.


