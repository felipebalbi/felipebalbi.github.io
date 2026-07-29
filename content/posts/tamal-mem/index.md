+++
title = "Tamal: Four Lines and Three Promises"
date = 2026-07-29T09:00:00
draft = true
description = "Reading Tamal's two block RAMs end to end: the forty-one-line leaf whose entire synthesizable content is blockRamPow2 applied to a vector of zeros, written twice; why the only real decision is which primitive rather than which code, and why the write port is a bare tuple so the memory never learns what a trace record is; the three semantics the wrappers inherit rather than implement --- one-cycle read latency, read-before-write, and an undefined cycle-zero output --- each read out of a concrete four-cycle trace; how eight lines of assoc-list Haskell restate all three as an executable oracle a machine can check; why the sweep property deliberately uses a second, independent oracle instead of the first one again; and why a primitive you did not write is exactly the kind you must pin down before anything else is allowed to depend on it."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "memory", "testing"]
+++

The [loader][loader] spent its entire length driving two memories. It
wrote instruction words into one of them, four bytes at a time, straight
through as they came off the wire. It swept the other one out after
`HALT` in seven patient phases, dancing with a one-cycle read latency
that it never stopped to explain. Both memories were named, used, and
leaned on --- and neither was ever opened.

This post opens them. `Tamal.Mem` is forty-one lines including the
licence header and the doc-comments, and the part that becomes gateware
is four. You can read it in less time than it takes to read this
paragraph, and you would be forgiven for wondering why it gets a post at
all.

It gets one because the four lines are not the module. The module is a
**contract** --- a set of promises about latency, about ordering, and
about what a memory says before it has been told anything --- and the
four lines are only where those promises get pinned to a width and given
a name. The file that holds them down is a hundred and forty-seven lines
of tests, three and a half times the module it checks. That ratio is not
an over-test. It is the honest shape of a module whose entire content is
contract.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[top]: https://balbi.sh/posts/tamal-uart-top/
[cobs]: https://balbi.sh/posts/tamal-loader-cobs/
[loader]: https://balbi.sh/posts/tamal-loader/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The entire source

Minus the SPDX header and the doc-comments:

```haskell
module Tamal.Mem
  ( instrRam
  , ringRam
  ) where

import Clash.Prelude

import Tamal.Params (AW, RW)

instrRam ::
  forall dom.
  (HiddenClockResetEnable dom) =>
  Signal dom (Unsigned AW) ->
  Signal dom (Maybe (Unsigned AW, BitVector 32)) ->
  Signal dom (BitVector 32)
instrRam = blockRamPow2 (repeat 0)

ringRam ::
  (HiddenClockResetEnable dom) =>
  Signal dom (Unsigned RW) ->
  Signal dom (Maybe (Unsigned RW, BitVector 32)) ->
  Signal dom (BitVector 32)
ringRam = blockRamPow2 (repeat 0)
```

Two signatures, two identical bodies, one import that is a pair of type
aliases. The instruction store and the trace ring --- the two halves of
the machine's memory, one that the engine reads from and one that it
writes to --- are the same expression twice, differing only in the width
of the number you address them with.

## What isn't here

There is no `data` declaration. There is no state record, no `where`
clause, no `mealy`. The [CRC][crc] computed a residue; the
[loader][loader] remembered where it stood in a frame. Nothing in this
file computes and nothing in this file remembers.

That is worth sitting with, because it makes `Tamal.Mem` the second kind
of nothing the series has met. The [UART top][top] was the first: a
module with no behaviour of its own, whose whole content was
*composition* --- three blocks that each own an idea, wired into one
block that owns none. `Tamal.Mem` does not even compose. It applies a
library function to a constant, twice. Its content is neither
computation nor wiring but **naming**: it takes a primitive that already
exists, fixes the two parameters that make it *this machine's* memory,
and gives the result a name that the rest of the design can say out
loud.

So if the code is four lines, what is the module for? The answer is the
rest of this post, and it comes in two halves that say the same thing
twice --- once in English, once in a language a machine can check.

## One decision, made once and reused

There is exactly one engineering decision in the file, and it is
*which primitive*, not *which code*.

[Clash] offers several block-RAM shapes. `blockRam` takes an
`Enum`-addressed vector and wants an `Index n`. `blockRamU` starts with
undefined contents; `blockRam1` starts with one repeated value.
`blockRamPow2` takes a `Vec (2^n)` and addresses it with an
`Unsigned n`. The engine already carries its program counter as an
`Unsigned AW` and its ring pointer as an `Unsigned RW`, and both depths
are exact powers of two, so `blockRamPow2` maps onto them **one to one**
--- no `Unsigned`-to-`Index` conversion at the call site, and no
out-of-range case to guard, because at these widths there are no
out-of-range addresses to have --- the [primer]'s point about widths
living in the type, cashed in as a branch that does not need to
exist.[^reset]

The initial contents, `repeat 0`, do double duty. On hardware Clash
lowers the vector into the block RAM's `INIT` strings, which is what
makes Vivado infer a real BRAM rather than scattering the array across
LUTs. In simulation it means every address is *defined* from the first
cycle onward, which is what makes randomised testing possible at all ---
a generator that reads an address nobody has written yet gets a zero
rather than an exception.

The widths themselves live somewhere else. `AW` and `RW` come from
`Tamal.Params`, a leaf that imports nothing but `Clash.Prelude` and
exists so that the engine, the memories, the loader, and the trace model
can all name the same number without importing one another. Resizing the
instruction space becomes one edit in one file instead of a hunt for
hand-copied `Unsigned 10` literals.

The write port is the last piece of the decision, and the most
deliberate. The engine emits its trace writes as `Maybe Ring`, a proper
record with named fields. The obvious signature would take that type.
This one takes a bare `Maybe (Unsigned n, BitVector 32)` instead ---
because accepting `Ring` would mean importing `Tamal.Engine`, and a
memory that imports the engine is a memory that knows what a trace
record is. The unpacking is one line, and it lives up in the topEntity
where the two are already being wired together:

```haskell
ringWrite :: Maybe Ring -> Maybe (Unsigned RW, BitVector 32)
ringWrite = fmap (\(Ring a d) -> (a, d))
```

One `fmap` is the entire price of keeping the memory ignorant. It is the
same discipline that produced `Tamal.RegFile`, built before `Engine.step`
existed to use it and never importing the engine whose registers it
holds. We have not opened that one yet --- it is waiting for a later post
--- but by the time we do, the shape will be familiar.

What falls out of the two widths is the geometry:

| Memory | Depth | Address | Data | ≈ BRAM36 |
|---|---|---|---|---|
| instruction | 1024 = 2^10 | `Unsigned AW` | `BitVector 32` | 1 |
| trace ring | 4096 = 2^12 | `Unsigned RW` | `BitVector 32` | 4 |

Five block RAMs out of the 135 an xc7a100t carries, which is a rounding
error. And one small gift: with the ring at 4096 words, the engine's
reserved terminator slot at `maxBound :: Unsigned RW` is exactly the last
address that exists. `termAddr = maxBound` and `termAddr = D - 1` are the
same constant, so choosing the full width of the address type made the
overflow-proof terminator free. What that slot is *for* is the engine's
business, and stays behind its door.

## Three promises

Here is the part that actually matters. `blockRamPow2` comes with three
semantics, and `Tamal.Mem` implements none of them --- it **inherits**
all three, and its contribution is to pin them at a width where the rest
of the design can rely on them.

**One cycle, not zero and not two.** The output at cycle *t* is the value
read at cycle *t-1*. Write `0xDEAD_BEEF` to address 5 on cycle 0 and read
address 5 from then on:

```haskell
let addrs    = [0, 5, 5, 5]
    writes   = [Just (5, 0xDEAD_BEEF), Nothing, Nothing, Nothing]
    expected = [0, 0xDEAD_BEEF, 0xDEAD_BEEF, 0xDEAD_BEEF]
```

The value appears one output later than the read that asked for it, and
then stays. The leading zero is the load-bearing part of that
expectation: it is the cycle-0 read of address 0, surfacing on cycle 1
while the write to address 5 is still in flight. Drop it and the very
same test would pass against a memory with no latency at all, because
everything after it would still line up. Pinning a delay means pinning
where the trace *starts*, not only what it settles to.

**Reads see the memory as it was.** Read and write the same address in
the same cycle and the read wins; the new value shows up on the cycle
after. Write `0x1111` to address 3, then on the very next cycle write
`0x2222` to address 3 while reading it:

```haskell
simInstr
  [0, 3, 3, 3]
  [Just (3, 0x1111), Just (3, 0x2222), Nothing, Nothing]
  @?= [0, 0x1111, 0x2222, 0x2222]
```

This one is not a convention that Clash picked and could change its mind
about. It is a fact about the silicon.[^rbw]

**The first output is a lie you must not read.** At cycle 0 the memory
has not yet been asked anything, and `blockRamPow2` reports
`deepErrorX` --- an undefined value that explodes if you force it. Every
sampler in the test file therefore drops sample 0 before comparing
anything. That is not defensive tidying; it is the third promise, and
ignoring it is how you get a test suite that fails with a stack trace
instead of a diff.[^outzero]

None of those three sentences is about code that lives in `Tamal.Mem`.
All three are about code that lives in `Clash.Prelude`. What
`Tamal.Mem` does is make them *this machine's* promises, at 10 bits and
at 12 bits, so that the loader and the engine can be written against
them.

## The same three promises, in Haskell

Prose promises do not fail a build. So the test file states the same
three things again, as an executable oracle --- a pure model of a block
RAM in eight lines of ordinary [Haskell], with no `Signal` and no clock
anywhere in sight:

```haskell
refRam ::
  (KnownNat n) =>
  [Unsigned n] ->
  [Maybe (Unsigned n, BitVector 32)] ->
  [BitVector 32]
refRam addrs writes = go [] (L.zip addrs writes)
 where
  go _ [] = []
  go mem ((a, w) : zs) = fromMaybe 0 (L.lookup a mem) : go (push w mem) zs
  push Nothing m = m
  push (Just (wa, wd)) m = (wa, wd) : m
```

Read it against the list above and the three promises are all sitting
there, one per detail.

`fromMaybe 0` is the **zero-initialisation**: an address that appears in
no prior write reads as zero, exactly as `repeat 0` guarantees on the
hardware side.

The order of the two expressions in `go` is the **read-before-write**
rule. The cons cell emits `L.lookup a mem` --- a lookup in the memory as
it stands *before* this cycle --- and only then recurses on `push w mem`,
the memory with this cycle's write applied. Swap those and the model
would describe a write-first RAM, and every collision test would fail.
The rule is not commented; it is structural, which is the better place
for it.

The shape of `go` is the **one-cycle latency**. Each step consumes one
`(address, write)` pair and produces one output, but the output it
produces is the read taken before that step's write landed --- so the
list `refRam` returns is `[out 1, out 2, ...]`, offset by exactly one
from the stimulus that produced it. The undefined `out 0` is not in the
list because the model never had a reason to invent it.

Two smaller things earn their place. The assoc list is kept
most-recent-first, so `L.lookup` --- which returns the first match ---
naturally yields the newest write to an address, and a slot that has been
written five times reads back the fifth value with no bookkeeping. And
the whole thing is polymorphic in the address width `n`, so a single
oracle serves a 1024-word instruction store and a 4096-word ring without
a line of duplication. One model, both memories.

## Getting a clock into a test

The oracle is pure; the thing it is checked against is not. Bridging them
takes a sampler:

```haskell
simInstr :: [Unsigned 10] -> [Maybe (Unsigned 10, BitVector 32)] -> [BitVector 32]
simInstr addrs writes =
  L.drop 1
    $ sampleN
      (L.length addrs + 1)
      ( instrRam (fromList (addrs <> L.repeat 0)) (fromList (writes <> L.repeat Nothing)) ::
          Signal Dom100 (BitVector 32)
      )
```

Three things are load-bearing here, and each of them is the kind of
detail that costs an afternoon the first time.

`sampleN` is what supplies the clock, reset, and enable that
`HiddenClockResetEnable` is asking for. Crucially, the RAM has to be
applied **directly inside** `sampleN`'s argument. You cannot factor
`instrRam` out into a parameter and write one generic sampler for both
memories, because the constraint can only be discharged inside that
rank-2 position. Hence two nearly identical samplers --- `simInstr` here,
`simRing` the same shape at width 12 --- rather than one clever one. The
duplication is not a failure of imagination; it is where the type system
draws the line.

`fromList (addrs <> L.repeat 0)` makes the stimulus infinite. A `Signal`
has no end, so a finite input list would run out and force an exception
on some cycle you never meant to look at. Padding with a harmless tail
means the signal is total and only the prefix is ever compared.

And `sampleN (n + 1)` followed by `L.drop 1` is the third promise,
operationalised. Take one more sample than you have stimulus for, throw
the undefined one away, and what remains is `[out 1 .. out n]` --- the
same length as `refRam`'s output, index-aligned with it. That alignment
is what earns the right to write `===` between them. Get it wrong by one
and the property still typechecks; it just compares the wrong cycles and
tells you a lie.

## What the properties buy

With a model and a sampler, the [hedgehog] properties are short. The main
one feeds random interleaved sequences of read addresses and optional
writes to both, at both widths, and demands they agree. Addresses are
drawn from a deliberately tiny window --- 0 to 15 --- so that random
reads frequently land on addresses that were recently written. A
generator ranging over all 4096 ring slots would produce a beautiful
stream of zeros and test nothing; the small window is what makes
collisions and read-after-write hits common enough to matter.

The second property is more interesting, because it declines to reuse
`refRam`. It writes a random pile of values into the window, sweeps the
whole window, and checks the result against a *different* oracle
computed on the spot --- a lookup over the reversed write list:

```haskell
oracle = fmap (\a -> fromMaybe 0 (L.lookup a (L.reverse ws))) win
```

Same answer, arrived at independently: last write wins, everything else
is zero. Checking `refRam` against the hardware proves they agree, which
is worth a lot --- but two implementations that share an idea can share a
misunderstanding of it. A second oracle written a different way is a
second opinion. It is also the same two-implementations habit the
[COBS][cobs] post named as the house style, applied to a model rather
than a codec.

## Why test a primitive you didn't write

The obvious objection is that all of this tests Clash. It does not.
`blockRamPow2` is correct, and its correctness was never the question.

What is under test is **the reading** of it --- the three promises, at
these two widths, in this domain, written down somewhere that fails
loudly rather than somewhere that fails quietly. If a Clash release ever
changes the collision behaviour, one unit test goes red with a two-value
diff instead of the engine mysteriously fetching a stale instruction on
one path in one program. If someone swaps `blockRamPow2` for `blockRamU`
to save an `INIT` string, the zero-initialisation property catches it
immediately, rather than the loader catching it three modules later as an
intermittent bad word after power-up.

That is why a hundred and forty-seven lines of test sit on top of four
lines of module without any embarrassment. The four lines are not the
deliverable. The contract is the deliverable, and the tests are the only
form in which a contract can be enforced rather than merely intended.

## The door

There is one promise left to spend.

The one-cycle read latency has been, for this entire post, a nuisance ---
something to drop a sample for, to align a list around, to pin with a
test so it does not drift. On the other side of the door it stops being a
nuisance and becomes a design.

The engine's program counter is a register inside its state, projected
out as `pcOut`. That signal feeds `instrRam`, whose output feeds the
instruction word back into the engine, which computes the next PC. A
loop --- but a loop containing *two* registers, the PC and the block
RAM's own output register, so there is no combinational path anywhere in
it and nothing for a synthesiser to complain about. The memory's one-cycle
delay does not have to be worked around, hidden, or paid for out of the
clock period. It is absorbed whole, as a phase, and the engine's own
source calls that phase by name: `Fetch` is documented as *the 1-cycle
instruction-BRAM bubble*.

The cost this post spent eight sections testing around is, one level up,
a pipeline stage the engine spends on purpose.

Both memories are known quantities now. The instruction store holds 1024
words and hands back what you asked for one cycle later. The ring holds
4096, the top one reserved, and behaves identically. Neither knows what a
program is, what a trace record means, or which of its neighbours is
writing. They are four lines and three promises, and everything above
them is now allowed to depend on that.

Which leaves the engine. We have circled it since the [first post][intro]
--- the machine the loader loads, triggers, and drains; the thing that
turns the words in one memory into the records in the other. It is the
last door in the series, and there is nothing left standing in front of
it.

Next, we walk through.

[^reset]: `blockRam` uses only the clock and the enable --- it has no
content reset, and cannot have one, because a block RAM's array is not
resettable silicon. The wrappers still carry the broader
`HiddenClockResetEnable` constraint rather than the precise
`(HiddenClock dom, HiddenEnable dom)`, which is technically looser than
necessary; GHC simply discharges the two it needs and ignores the third.
Uniformity with every other block in the codebase won that trade, and it
costs nothing here, because [Tamal]'s top has no reset port anyway --- the
no-reset power-up design is deliberate, and a memory that cannot be reset
fits it exactly rather than fighting it.

[^rbw]: A synchronous RAM latches the read address at the clock edge, and
the array is read before that same edge's write commits. Xilinx names the
three possible port behaviours `READ_FIRST`, `WRITE_FIRST`, and
`NO_CHANGE`; Clash's `blockRam` models `READ_FIRST`. That makes
read-before-write a property of the hardware you are going to get, not a
convention you are free to choose --- which is precisely the sort of
thing that belongs in an assertion rather than a comment. A comment
describing the behaviour of silicon is a comment that will one day be
describing the behaviour of *different* silicon.

[^outzero]: `deepErrorX` is [Clash] declining to lie. A real block RAM's
output register powers up holding whatever it powers up holding, and
there is no honest 32-bit value to report for the cycle before the first
read completes --- so Clash reports a value that is fine to carry around
and fatal to inspect. This is why Clash's own `blockRam` doctests begin
with `L.tail`, and why `L.drop 1` appears in both samplers here. A test
that forces sample 0 has asked the simulator a question about hardware
that has not happened yet, and deserves the exception it gets.
