+++
title = "Tamal: Sixteen Registers and a Hardwired Zero"
date = 2026-08-06T09:00:00
draft = true
description = "The last pure leaf before the engine, opened: Tamal.RegFile, the sixteen registers the datapath read its operands from and wrote its results to; a four-name door that exports one opaque newtype and three functions, its Vec 16 of BitVector 32 sealed behind the wall so the engine touches it only through initRegs, readReg and writeReg; a single import --- Tamal.Isa, for the name of a five-bit selector --- and never the engine whose registers it holds, the same discipline the memories promised would be familiar; the power-up bank of sixteen zeros that is the reset value of a machine with no reset port; the private regIndex that truncates a five-bit name to a four-bit slot so the index type is total by construction and out-of-window x16..x31 alias their low-four twin; readReg's hole at x0, the hardwired zero that made no operand free back in the dispatch post; writeReg's discarded write and its functional update, a fresh bank returned rather than a cell mutated; the read-old, write-new hazard-freedom that falls out of a pure Mealy step; and a test file that builds every bank through the public door and pins the bijection, the hole, and the aliasing from six directions at once."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "registers"]
+++

The [instruction set][isa] closed on a name it declined to open. `decode`
had turned a word into an `Instr`, pulled the operands out as five-bit
`Reg` fields, and handed them on --- and the very next thing the
[dispatch post][exec] watched `execInstr` do was read the registers those
fields named: `readReg (regs s) (operandRs1 i)`. But a `Reg` was only ever
a *name*, five bits wide, not yet an index into anything. The thing it
names is this post: `Tamal.RegFile`, the sixteen registers, the register
hardwired to zero, and the two ports the datapath read and wrote through
the whole arc while we held them shut.

This is a leaf again, read whole the way the [CRC][crc] and the
[memories][mem] were --- small enough to hold entire, fifty-one lines with
nothing left closed when the post ends. And it is a leaf the series has
already promised. The [memories][mem] kept their write port ignorant of
the engine --- a block RAM that will not import `Tamal.Engine`, because "a
memory that imports the engine is a memory that knows what a trace record
is" --- and pointed here as its sibling: *the same discipline that
produced `Tamal.RegFile`, built before `Engine.step` existed to use it and
never importing the engine whose registers it holds.* By the time we
opened it, that post said, the shape would be familiar. Here it is.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[exec]: https://balbi.sh/posts/tamal-engine-exec/
[isa]: https://balbi.sh/posts/tamal-isa/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The door, again

A [Tamal] module opens by naming itself and listing what leaves through
the wall, and after the [engine's][shape] eleven-name sprawl this one is a
return to the narrow doors of the leaves:

```haskell
module Tamal.RegFile
  ( Regs
  , initRegs
  , readReg
  , writeReg
  ) where
```

Four names. We have read this shape before, and the aphorism the [CRC][crc]
gave it still holds:

> A module is a wall with a door in it, and the export list is the door.

But look at *what* the four names are, because the split is the whole
design in miniature. One is a noun --- `Regs`, the type of the register
bank --- and three are verbs: `initRegs` makes one, `readReg` inspects one,
`writeReg` evolves one. The [engine][shape] had to export six types
because something one level up assembles it and must name its plugs; a leaf
like the [CRC][crc] exported only a verb and hid its types entirely.
`Tamal.RegFile` sits between: it publishes *one* type, because its callers
must be able to hold a `Regs` and pass it around, but it publishes that
type **opaquely** --- the name `Regs` crosses the wall, and its constructor
does not. You will not find `Regs (..)` on that list, the way the engine
wrote `State (..)` to export a record and all its fields. Just `Regs`, the
bare name, a handle to a thing whose insides stay home.

That is a deliberate choice, and it is the same choice that kept `step`
private inside the CRC: the export list is a place to say less than you
could. Everything a caller is allowed to do to a register bank is on those
four lines, and the shape of the bank behind them is not. Hold that; it is
about to matter twice.

## The prelude, and the only import

Two lines sit under the header, and the first is the one every block in
this series has carried:

```haskell
import Clash.Prelude
import Tamal.Isa (Reg)
```

The [prelude swap][crc] is old news by now --- the line that throws out
ordinary Haskell's furniture and moves in the `Bit`, `BitVector`, `Vec`,
and `Signal` that lower to gates, the line that says *compile me to
hardware*. I will not re-derive it a seventh time. It is the *second*
import that repays a look, and it repays it precisely because of how
little it says.

`import Tamal.Isa (Reg)` reaches across the wall for exactly one name, and
that name is a type synonym: `Reg`, which [we met][isa] as `BitVector 5`,
the five-bit register selector `decode` carves out of an instruction word.
The register file imports the *name of a selector* --- and nothing else.
It does not import `Tamal.Engine`. It does not import the `State` that will
hold it, or the `step` that will call it, or the ALU whose results it will
store. It imports one width-carrying synonym from the instruction set and
stops.

This is the discipline the [memories][mem] named when they refused to let
their write port learn what a trace record was, and it is worth stating as
its own rule now that we are standing inside the leaf it promised:

> A leaf that holds the engine's registers has never heard of the engine.

The dependency arrow runs one way only. `Tamal.Isa` knows nothing of
`Tamal.RegFile`; `Tamal.RegFile` knows only the name `Reg`; and
`Tamal.Engine`, one level up, imports *both* and wires them together. The
register file was, as the mem post said, "built before `Engine.step`
existed to use it" --- a small, [hedgehog][hedgehog]-tested warm-up written
so that when the keystone `step` finally landed, its operand-fetch and
writeback path would be *wiring*, not *invention*. And importing `Reg`
rather than re-declaring a bare `BitVector 5` is the small courtesy that
makes that wiring seamless: `readReg` and `writeReg` take the *exact* type
`decode` produces, so the engine can pass `rs1`, `rs2`, and `rd` straight
through with no conversion at the seam.

## The bank, sealed

Here is the type the whole module exists to serve, and the reason its
constructor stayed off the door:

```haskell
newtype Regs = Regs (Vec 16 (BitVector 32))
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

A `Regs` is a `Vec 16 (BitVector 32)` --- sixteen thirty-two-bit words, a
fixed-length vector, five hundred and twelve bits of architectural state
--- wrapped in a `newtype` whose constructor is also called `Regs`. The
wrapper is the wall. Because the constructor does not leave the module,
nothing outside can write `Regs someVector` to forge a bank, or pattern-
match `Regs v` to reach the raw vector inside; the `Vec 16` never leaks.
The outside world holds a `Regs` as a sealed handle and touches it only
through `initRegs`, `readReg`, and `writeReg`, exactly the three verbs on
the door. Should a later version want a different representation --- a
packed `BitVector 512`, a pair of banked halves, whatever a timing closure
demands --- it can change this line and the three functions and break not a
single caller, because no caller was ever allowed to see the shape it is
changing.[^newtype]

The `deriving` block is the [four-class refrain][shape] every stateful
[Tamal] block wears, and I will be quick with it: `Generic` lets Clash work
structurally, `Show` and `Eq` are for the tests --- and `Eq` earns a
specific keep here, since one property will assert `writeReg rs 0 v === rs`
and needs to compare two banks --- and `NFDataX` is the one that means
"this value can be the contents of a register." Everywhere else in the
series that phrase was a near-metaphor. Here it is *literal*: the
[engine's `State`][shape] has a field `regs :: Regs`, the whole `State` is
what the `mealy` wrapper clocks, and so a `Regs` is not *like* a register's
contents --- it *is* sixteen registers' contents, five hundred and twelve
flip-flops on the fabric. `NFDataX` is the class that lets those flip-flops
have a defined power-up value; without it, `Regs` could not sit in the
state at all.

## Power-up is sixteen zeros

One line says what the bank holds before anything happens:

```haskell
initRegs :: Regs
initRegs = Regs (repeat 0)
```

`repeat 0` is the [`Vec` `repeat`][mem] that fills a whole fixed-length
vector with one value; here it lays down sixteen copies of `0`, and `Regs`
wraps them. Every register powers up holding zero. This is the same move
the [memories][mem] made with `repeat 0` for their `INIT` contents, and it
does the same double duty: on hardware it becomes the flip-flops' reset
value, and in simulation it means every slot is *defined* from the first
cycle, so a randomised test that reads a register nobody has written gets a
zero rather than an exception.

And `initRegs` is not merely *a* starting value; it is *the* one. The
[shape post][shape] read `initState` and found `regs = initRegs` sitting in
it, and read the larger fact folded into `initState` existing at all:
[Tamal] has **no reset port.** The top ties reset permanently deasserted and
leans on power-up `init`, so `initRegs` is not what a reset line loads ---
there is no reset line --- it is what the register bank *powers up holding*.
When a second program runs, `softInit` throws the whole machine back to
`initState`, `regs` included, so every run begins from sixteen zeros and
the [byte-identical re-run][shape] the intro promised extends cleanly
through the register file. The determinism has to reach all the way down
here, and `repeat 0` is where it lands.

## A name is not a slot

Between the five-bit name a caller hands in and the physical register it
means sits one private helper --- private, like the CRC's `step`, kept off
the door because nothing outside should depend on how a name becomes a
slot:

```haskell
regIndex :: Reg -> Index 16
regIndex r = unpack (truncateB r)
```

A `Reg` is a `BitVector 5`; an `Index 16` is a number in `0..15`, the type
Clash uses to index a sixteen-element vector safely. `regIndex` is the
translation, and it is two operations read right to left. `truncateB r`
keeps the **low four bits** of the five-bit selector and discards the top
one, yielding a `BitVector 4`; `unpack` then reinterprets those four bits
as an `Index 16`, whose bit-width is exactly four.[^index] The wider
selector is narrowed to the width the bank actually has.

This is a small instance of the width-changing family --- `truncateB`,
`zeroExtend`, `signExtend` --- as opposed to the [structural `bitCoerce`][isa]
that reshapes bits without changing how many there are. `truncateB` changes
the count: five bits in, four out, the fifth dropped on the floor. And the
choice of which bit to drop is the design.

Look at the result type. `regIndex` returns an `Index 16`, and an
`Index 16` *cannot* be out of range --- the values it can hold are `0`
through `15` and no others, guaranteed by the type. So when `readReg` and
`writeReg` reach for the total Clash primitives `(!!)` and `replace`, there
is no bounds check to write and no out-of-range case to handle, because the
index they are handed made the bound part of its own type.[^prim] This is
the [memories'][mem] "widths live in the type, so there is no branch to
have," moved one level over: the safety is not enforced at runtime, it is
made unrepresentable at compile time.

But narrowing five bits to four has a visible consequence, and the design
chose it on purpose. Two selectors that differ only in their top bit ---
`x1` (`00001`) and `x17` (`10001`) --- truncate to the *same* four bits and
therefore the same slot. The out-of-window selectors `x16..x31` **alias**
their low-four twins. The leaf does not reject them; it folds them back
into the sixteen it has. That keeps the function **total** --- every one of
the thirty-two names a five-bit field can hold yields a defined slot, none
traps --- and totality is the property a leaf is supposed to guarantee.
Whether `x16..x31` should be *refused* is a real question, but it is the
assembler's and the engine's question, not this leaf's: the [instruction
set][isa] noted that `Reg` is "wider than the register file behind it ...
deliberate room to grow," and the fifth bit is that room,
reserved for a future that implements thirty-two registers. Until then the
register file stays total by aliasing, and leaves the rejecting to the
layers whose job rejecting is.

## `readReg`: the hole at x0

The first of the two ports:

```haskell
readReg :: Regs -> Reg -> BitVector 32
readReg (Regs v) r
  | idx == 0 = 0 -- x0 is hardwired to 0.
  | otherwise = v !! idx
 where
  idx = regIndex r
```

The [type is half the documentation][primer]: a bank and a name in, a
thirty-two-bit value out, and --- the part the type says by saying nothing
--- *always* a value, no `Maybe`, no error. Reading a register cannot fail.
It pattern-matches the bank open as `Regs v` (allowed, we are inside the
wall), computes `idx = regIndex r`, and forks on one guard.

That guard is the design decision the whole [RISC-V flavour][intro] turns
on. When `idx == 0`, `readReg` does **not** return `v !! 0` --- it does not
read slot zero's contents at all. It returns the literal `0`. Register
`x0` is hardwired to zero: reading it yields zero no matter what is
physically stored in slot zero, and nothing can make it read anything
else. Every other index falls to `v !! idx`, the total vector index that
pulls slot `idx`'s word straight out.

We have already spent this guarantee, a whole post before we built it. The
[dispatch post's][exec] operand selectors ended every table with a
catch-all `_ -> 0`, so that an instruction with no second source "reads
`x0`" and gets zero --- "a read that is always safe, always defined, and
costs nothing, because `x0` is not a register you can get wrong." *This
guard is why.* The reason reading `x0` is free is that `readReg` answers
`0` without so much as glancing at the vector; `LoadImm`, which names no
sources and reads `x0` twice, pays nothing for the two reads because both
short-circuit here. The engine leaned on a promise, and the promise is one
line of guard in a leaf.

In hardware `readReg` is combinational --- the [truth-table totality][isa]
the series keeps returning to, cashed as a **16-to-1 multiplexer**: the
four-bit index selects one of sixteen thirty-two-bit lanes, and the lane
for slot zero is tied to constant zero rather than to a flip-flop's
output.[^prim] No clock, no latency; the value is there the moment the
index is. And because it is combinational, the datapath can call it twice
in one cycle --- once for `rs1`, once for `rs2` --- and get both source
values at once, which is why a module that exports a single `readReg` gives
the datapath *two* read ports. The one function, instantiated twice, is the
classic two-read shape of a register file; the `x0` register even earns a
second keep as the [always-true branch][exec], where `j off` is really
`beq x0, x0, off`, a comparison of zero against zero that `readReg` makes
free on both sides.

## `writeReg`: the write that replaces

The second port, and the more quietly radical one:

```haskell
writeReg :: Regs -> Reg -> BitVector 32 -> Regs
writeReg regs@(Regs v) r x
  | idx == 0 = regs -- writes to x0 are ignored!
  | otherwise = Regs (replace idx x v)
 where
  idx = regIndex r
```

Read the type first, because its *return* is the whole story. `writeReg`
takes a bank, a name, and a value, and hands back --- a bank. Not `()`, not
`IO ()`, not a mutation performed on the side: a fresh `Regs`. A write to a
register does not *change* the register file. It *computes a new one* and
returns it, leaving the old one exactly as it was.

This is the [primer's][primer] "mapping, not procedure," cashed the way the
[CRC's fold][crc] cashed it: nothing is overwritten. In C, `regs[rd] = x`
reaches into an array and clobbers a cell. Here `replace idx x v` produces a
*new* vector identical to `v` but with slot `idx` set to `x`, and `Regs`
wraps it; the argument `v` is never touched and is simply not referenced
again. What looks like the mutation of a register is a pure function from an
old bank to a new bank, and the "mutation" you imagine happens exactly once,
elsewhere: when the [Mealy wrapper][shape] clocks the returned `State` --
the one now carrying the new `Regs` --- into the fabric flip-flops at the
edge. The datapath itself assigns nothing.

The guard is `x0` again, from the writing side. `idx == 0` returns `regs`
--- the whole original bank, unchanged --- so a write to `x0` is silently
discarded, which is the other half of "hardwired to zero": you cannot read
anything but zero out, and you cannot put anything in. The
[dispatch post][exec] promised this too, noting that `writeReg` "quietly
enforces one rule ... a write to `x0` is discarded, because `x0` is
hardwired zero." Here is the enforcement, one guard wide. Every other index
takes the `replace` path. And the as-pattern `regs@(Regs v)` is a small
convenience doing real work: it binds the whole argument as `regs` *and* its
unwrapped vector as `v` in one shot, so the `x0` arm can return the original
`regs` without rebuilding it while the other arm still has `v` in hand to
hand to `replace`.

## One invariant, one place, twice

Two things are worth pausing on together, because they are really one
decision. First, `x0` is guarded in *both* ports --- read returns zero,
write is discarded --- and both guards test the **truncated** index,
`idx == 0`, not the raw five-bit selector. That is what makes the hole
consistent under aliasing: `x16` truncates to slot zero, so `x16` reads
zero and discards its writes *exactly* as `x0` does. The aliasing and the
hardwiring compose without anyone arranging it; the same `idx == 0` that
protects `x0` protects its out-of-window twin for free.

Second, and larger: the `x0` rule lives *here*, in the register file, and
nowhere else. The [engine][shape] reads and writes registers uniformly ---
`readReg (regs s) rd`, `writeReg (regs s) rd result` --- and never checks
whether `rd` is zero, because it never has to. If the rule lived in the
engine instead, every one of the [thirteen DATA arms][exec] that writes a
register would need an `if rd /= 0` around its writeback, and the invariant
would be scattered across a dozen call sites, each an opportunity to forget
it. Housed in one leaf, it is impossible to forget: the register file
*is* the thing that makes `x0` behave, so anything that goes through the
register file inherits the behaviour.

> `x0` is not a register the engine keeps at zero. It is a register that
> cannot be anything else, because the only door to it refuses to carry a
> value either way.

<figure class="rf-fig" style="margin:2rem 0">
<svg class="rf" viewBox="0 0 760 244" role="img" aria-labelledby="rf-t rf-d" xmlns="http://www.w3.org/2000/svg">
<title id="rf-t">The tamal register file: sixteen registers with x0 a hardwired zero, two read ports, one write port</title>
<desc id="rf-d">Sixteen register cells in a row, labelled x0 on the left through x15 on the right, together forming the opaque Regs value, a Vec of sixteen 32-bit words. x0 is drawn in the accent colour and marked identically zero: reading it returns zero and writes to it are discarded. A five-bit selector is truncated to its low four bits to choose a slot, so out-of-window selectors x16 through x31 alias their low-four twin. Below the bank, one write port labelled writeReg carries a destination and a value up into the bank and produces a fresh bank, while two read ports labelled readReg tap values rs1v and rs2v out of it.</desc>
<style>
.rf{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.rf .cell{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.rf .cellA{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.rf .nm{fill:var(--fg-main);font-family:var(--mono);font-size:12px}
.rf .nmA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.rf .zero{fill:var(--accent);font-family:var(--mono);font-size:11px}
.rf .brk{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.rf .cap{fill:var(--fg-dim);font-family:var(--mono);font-size:12px}
.rf .prd{fill:var(--fg-main);font-family:var(--mono);font-size:12px}
.rf .prdA{fill:var(--accent);font-family:var(--mono);font-size:12px}
.rf .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.rf .accw{stroke:var(--accent);stroke-width:2.5;fill:none}
.rf .ah{fill:var(--fg-main)}
.rf .aha{fill:var(--accent)}
</style>
<defs>
<marker id="rf-a" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="ah"/></marker>
<marker id="rf-aa" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- bracket over the whole bank -->
<path class="brk" d="M28,64 V56 H728 V64"/>
<text class="cap" x="378" y="48" text-anchor="middle">Regs — Vec 16 (BitVector 32)</text>
<!-- 16 cells -->
<rect class="cellA" x="28" y="70" width="40" height="54" rx="4"/>
<text class="nmA" x="48" y="94" text-anchor="middle">x0</text>
<text class="zero" x="48" y="112" text-anchor="middle">≡0</text>
<rect class="cell" x="72" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="92" y="101" text-anchor="middle">x1</text>
<rect class="cell" x="116" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="136" y="101" text-anchor="middle">x2</text>
<rect class="cell" x="160" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="180" y="101" text-anchor="middle">x3</text>
<rect class="cell" x="204" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="224" y="101" text-anchor="middle">x4</text>
<rect class="cell" x="248" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="268" y="101" text-anchor="middle">x5</text>
<rect class="cell" x="292" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="312" y="101" text-anchor="middle">x6</text>
<rect class="cell" x="336" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="356" y="101" text-anchor="middle">x7</text>
<rect class="cell" x="380" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="400" y="101" text-anchor="middle">x8</text>
<rect class="cell" x="424" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="444" y="101" text-anchor="middle">x9</text>
<rect class="cell" x="468" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="488" y="101" text-anchor="middle">x10</text>
<rect class="cell" x="512" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="532" y="101" text-anchor="middle">x11</text>
<rect class="cell" x="556" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="576" y="101" text-anchor="middle">x12</text>
<rect class="cell" x="600" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="620" y="101" text-anchor="middle">x13</text>
<rect class="cell" x="644" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="664" y="101" text-anchor="middle">x14</text>
<rect class="cell" x="688" y="70" width="40" height="54" rx="4"/>
<text class="nm" x="708" y="101" text-anchor="middle">x15</text>
<!-- write port: value up into the bank -->
<path class="wire" d="M180,220 V124" marker-end="url(#rf-a)"/>
<text class="prd" x="180" y="230" text-anchor="middle">writeReg rd x</text>
<!-- read ports: values down out of the bank -->
<path class="wire" d="M470,124 V210" marker-end="url(#rf-a)"/>
<text class="prd" x="470" y="230" text-anchor="middle">readReg rs1 → rs1v</text>
<path class="wire" d="M632,124 V210" marker-end="url(#rf-a)"/>
<text class="prd" x="632" y="230" text-anchor="middle">readReg rs2 → rs2v</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The register file as the datapath sees it. The sixteen cells are the opaque <code>Regs</code>, a <code>Vec 16 (BitVector 32)</code>; <code>x0</code> (accent) is the hole, <code>≡0</code> --- <code>readReg</code> returns zero for it and <code>writeReg</code> discards writes to it, both keyed on the truncated index so <code>x16</code>‥<code>x31</code> alias <code>x0</code>‥<code>x15</code> and inherit the same behaviour. A five-bit <code>Reg</code> is truncated to its low four bits (<code>regIndex</code>) to pick a slot, so the index is an <code>Index 16</code> that <em>cannot</em> be out of range. One <code>writeReg</code> port returns a fresh bank; one <code>readReg</code>, instantiated twice, gives the two combinational read ports the datapath fetches <code>rs1</code> and <code>rs2</code> through.</figcaption>
</figure>

## Read-old, write-new, for free

Step back up one level, to the [dispatch post's][exec] `dataWb`, and watch
all three functions meet in a single cycle:

```haskell
  rs1v = readReg (regs s) (operandRs1 i)
  rs2v = readReg (regs s) (operandRs2 i)
  dataWb rd =
    let s' = (advance s){regs = writeReg (regs s) rd (dataResult i rs1v rs2v)}
     in (s', busOut s', Nothing)
```

Every one of them touches `regs s` --- the *current* cycle's bank. The two
reads pull `rs1v` and `rs2v` out of it; the write builds a *new* bank from
it and tucks that into `s'` under `regs`. Because `readReg` reads the value
the state holds *now*, and `writeReg` merely computes the value the state
will hold *next*, the two cannot collide. Even when a `DATA` instruction
writes the very register it read --- `add x3, x3, x1`, where `rd` and `rs1`
are both `x3` --- `rs1v` is the *old* `x3`, sampled before the write, and
the new `x3` does not exist until the [Mealy wrapper][shape] clocks `s'` at
the edge. Read-old, write-new, hazard-free, and nobody wrote a hazard
check: it falls out of the read and the write being pure functions of the
same value, with the "write" a separate return that only becomes state a
cycle later.[^hazard]

This is why the register-file design rejected a `blockRam`-backed
bank, and it is worth naming because it is the road not taken. A block RAM
has a one-cycle read latency and lives at `Signal` level; it could not be a
field of a pure Mealy `State`, and reading and writing the same address in
one cycle would open exactly the read-during-write hazard the pure version
closes by construction. Five hundred and twelve bits is small enough to
live in fabric flip-flops with a combinational read, so it does --- and the
purity that made the [whole engine testable][shape] makes its register
hazards vanish as a side effect. The same choice buys both.

## The tests

`Test.RegFile` is short, the way a leaf's tests are short, and it makes the
same quiet point the [CRC][crc] and the [memories][mem] made: a claim this
small can be pinned down *completely*. Its cleverest line is a generator:

```haskell
genRegs :: Gen Regs
genRegs = do
  ws <- Gen.list (Range.linear 0 20) ((,) <$> genReg <*> genWord)
  pure (foldl' (\rs (r, v) -> writeReg rs r v) initRegs ws)
```

Read what it does *not* do. It builds an arbitrary register bank by drawing
a random list of up to twenty `(register, value)` pairs and *folding
`writeReg` over them*, starting from `initRegs` --- so every `Regs` the
tests ever see is constructed **only through the public door**. The
generator never reaches for the `Regs` constructor, because the constructor
is not exported and is not the test's to touch, exactly as the [CRC's
tests][crc] "exercise the byte-level `crc8Update` and never reach for
`step`." The wall holds even here; the properties test the bank the way a
real caller builds it, one `writeReg` at a time. (The `foldl'` is
`Data.List`'s, not the `Vec` fold `import Clash.Prelude` [shadowed][crc] ---
`ws` is a list --- the same prelude-swap wrinkle that made the CRC tests
reach for `L.foldl'`.)

The properties then stake down every corner of the design at once:

```haskell
testProperty "read-after-write (r /= x0)" $ property $ do
  rs <- forAll genRegs
  r <- forAll genNonZeroReg
  v <- forAll genWord
  readReg (writeReg rs r v) r === v
```

Write a value to any non-`x0` register and read it straight back: you get
the value, whatever the slot held before. That is the ordinary register's
contract --- an overwrite wins --- and it is the first thing to prove. Three
more guard the `x0` hole from all sides: `readReg rs 0 === 0` says the read
is always zero; `writeReg rs 0 v === rs` says the write is a no-op, and it
leans on the derived `Eq Regs` to compare whole banks (the reason `Eq` was
on the door); and a *register independence* property draws two selectors
with distinct low-four indices and checks that writing one leaves the
other's read untouched --- no write scribbles on a neighbour. One pins the
power-up state, `readReg initRegs r === 0` for every name. And two pin the
truncation itself:

```haskell
testProperty "x16..x31 alias x0..x15" $ property $ do
  r <- forAll genNonZeroReg -- x1..x15
  v <- forAll genWord
  readReg (writeReg initRegs (r + 16) v) r === v
```

```haskell
testCase "x16 aliases x0 (write discarded)"
  $ writeReg initRegs 16 42
  @?= initRegs
```

The property writes to `r + 16` --- an out-of-window selector --- and reads
back through `r`, demanding the value land in the twin slot; the [Hedgehog]
run fires it across every `r` in `x1..x15`. The `HUnit` case nails the one
corner the property cannot reach, since `x0` has no non-zero twin to read:
writing `42` to `x16`, which aliases `x0`, must leave the bank *identical*,
because the write is discarded. Six checks and a witness, and between them
they describe the whole surface of the leaf --- the bijection on
`x1..x15`, the hole at `x0`, the aliasing of everything above --- with
nothing left to guess.

## What we read

The last pure leaf, opened whole. A four-name door that exports one type
and three verbs, and exports the type *opaquely*, so a `Vec 16 (BitVector
32)` can be sealed behind the wall and touched only through `initRegs`,
`readReg`, and `writeReg`. A single import --- `Tamal.Isa`, for the name of
a selector --- and, pointedly, not the engine whose registers it holds: the
[memories'][mem] discipline, cashed as promised, a leaf built before the
`step` that would use it. A `newtype` bank of five hundred and twelve bits
that powers up to sixteen zeros and, deriving `NFDataX`, *is* the engine's
register flip-flops in its [Mealy state][shape]. A private `regIndex` that
truncates a five-bit name to a four-bit slot, so the index is an `Index 16`
that cannot be out of range and the out-of-window `x16..x31` alias their
low-four twins rather than trap --- totality by construction, room to grow
left in the fifth bit. `readReg`'s hole at `x0`, the hardwired zero that
made "no operand" [free a post ago][exec]; `writeReg`'s discarded write and
its functional update, a fresh bank *returned* rather than a cell *mutated*,
the primer's mapping-not-procedure one last time. One `x0` invariant, kept
in one place and keyed on the truncated index so aliasing inherits it. The
read-old/write-new hazard-freedom that falls out of a pure step for nothing.
And a test file that builds every bank through the public door and pins the
bijection, the hole, and the aliasing from six directions at once.

The register file hands the datapath two values every cycle, and those
values do not sit idle. `rs1v` and `rs2v` --- the two reads `dataWb` took
before it wrote --- flow straight on into the one box the [dispatch
post][exec] still had shut over its writeback: `dataResult`, the ALU, which
turns two register values and an instruction into the one value that comes
back to `writeReg`. And the same two reads feed the branch comparator that
decided whether a jump was taken. Both were named and left closed. Next we
open them together --- `Tamal.Alu` and `Tamal.Branch`, the add and the
`and` and the taken branch --- the compute layer that sits between the two
ports we just read, consuming what one hands out and producing what the
other takes in.

[^newtype]: `newtype` rather than `data` is a deliberate, zero-cost choice.
A `newtype` may wrap exactly one field, and Haskell guarantees it is
*erased* at compile time: at runtime a `Regs` **is** a `Vec 16 (BitVector
32)`, with no box, no tag, no indirection --- the wrapper exists only in the
type-checker, where it does the work of keeping the two types distinct and
the constructor private. So the encapsulation the opaque export buys is
free: it costs a name in the type system and nothing in the gates. Clash
lowers the `Vec 16` to a bank of flip-flops exactly as it would an
unwrapped vector, and the `Regs` label vanishes before synthesis ever sees
it. You get the wall without paying for a wall.

[^index]: The widths line up by type inference, read outside in. `regIndex`
is annotated to return `Index 16`, and the `BitSize` of `Index 16` is `4`
(four bits address sixteen slots). That return type fixes `unpack ::
BitVector 4 -> Index 16`, which in turn fixes `truncateB r :: BitVector 4`
--- `truncateB` is polymorphic in its output width, so the surrounding type
is what tells it to keep four bits rather than three or five. Nothing here
names the number four; it is deduced from `Index 16`, so changing the bank
to `Vec 32` and the selector's window would re-derive the truncation width
automatically. The type carries the arithmetic, and the code only says
"narrow to fit."

[^prim]: `(!!) :: Vec n a -> Index n -> a` and `replace :: Index n -> a ->
Vec n a -> Vec n a` are Clash's *total* vector primitives: because the
index has type `Index n`, there is no out-of-range value to pass and thus no
partial case, no runtime bounds fault. In hardware `(!!)` on a `Vec 16`
lowers to a 16-to-1 multiplexer selected by the four index bits, and
`readReg`'s `x0` guard is a further two-way mux tying the output to constant
zero when the index is zero. `writeReg`'s `replace` lowers to sixteen
thirty-two-bit registers, each with a load-enable asserted when `idx`
equals its position *and* `idx` is non-zero --- which is precisely why slot
zero is never written. All of it is combinational logic over five hundred
and twelve flip-flops; none of it is a `blockRam`, and that is the point.

[^hazard]: The hazard-freedom is a property of *values*, not of careful
sequencing. `readReg (regs s) rs` and `writeReg (regs s) rd v` are both
functions of the same immutable `regs s`; neither can observe the other,
because there is no mutable cell for one to write and the other to read.
The new bank is a distinct value that the caller places into `s'`, and only
the clock edge --- the one genuinely stateful event, owned by the `mealy`
wrapper a level up --- turns `s'` into the next `regs s`. So "read the old,
write the new" is not a rule the register file implements; it is the only
thing that *can* happen when reads and writes are pure and state advances
once per cycle. A conventional imperative register file has to design
around the write-during-read case; this one cannot express it.
