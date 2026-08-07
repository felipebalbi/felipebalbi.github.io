+++
title = "Tamal: Add, And, and the Taken Branch"
date = 2026-08-07T09:00:00
draft = false
description = "The two shut boxes of the dispatch post, opened together: Tamal.Alu and Tamal.Branch, the pure combinational compute layer that turns two register values into the one written back or the one bit that decides a jump; the ALU's two layers, a thin total alu core dispatched by an eight-constructor AluOp over register values, and dataResult, the wrapper that places the Lui, Mov and LoadImm constants and resolves operand B --- a register value or a sign-extended immediate --- so the register/immediate opcode pairs collapse to eight operations; the two import lines that are both scars from naming collisions, Clash.Prelude hiding And and Xor because base already owns those newtype names and Tamal.Isa imported qualified because Instr's own Add and Sub would clash; the shift amount masked to five bits so a shift past the word is well-defined, the logical Srl and the arithmetic Sra that reinterprets its operand as Signed to borrow a sign; the total case's documented-unreachable zero default; the branch comparator, four lines returning only taken-or-not, its unsigned Bltu and Bgeu free from BitVector's own Ord while the PC math stays in the engine; and the tests that pin the subtle corners --- two's complement, shift masking, sign-fill, the unsigned boundary --- against reference models the derived Enum lets them run over every op."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "alu"]
[extra]
math = true
+++

The [register file][regfile] handed the datapath two values, and we
followed them to the door of the next box. `rs1v` and `rs2v` --- read
through one register port, bound for the other --- do not travel straight
from the read to the write. Between the two ports sits a layer that turns
them into something: the value that comes back to `writeReg`, or the single
bit that decides whether to jump. That layer is this post. The [dispatch
post][exec] held two boxes shut over exactly these two acts --- `dataResult`,
the value its `dataWb` wrote, and `branchTaken`, the decision its `branch`
turned on --- and we open both here, in the two small modules that hold
them: `Tamal.Alu` and `Tamal.Branch`.

These are leaves, read whole --- `Alu` is eighty-six lines, `Branch` is
thirty-four --- and together they are the engine's **compute layer**: pure,
combinational, single-cycle functions of operand *values*. Neither knows
there is a register file above it or an engine around it; each takes
thirty-two-bit values and answers. This is where the machine finally does
arithmetic --- and where, in two import lines, it collides with the rest of
the language over what to call it.

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
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## Two boxes, one kind of thing

The [dispatch post][exec] left seven leaves shut, and two of them sat over
the only places `Exec` actually *computed*. One was `dataResult`, called
from `dataWb` --- the handler that [thirteen DATA instructions][exec] all
funnelled through, each supplying a destination register and letting the
box work out the value. The other was `branchTaken`, called from `branch`
--- the helper the four conditional branches deferred their condition to
while the engine kept the program-counter arithmetic for itself. We open
them together because they are the same kind of thing: a pure, total,
single-cycle function of two register values, no state, no clock, no wire.
The difference is only in what they return --- one a thirty-two-bit value,
the other a single `Bool` --- and that difference is exactly the line
between the DATA group's compute and the CTRL group's branches.

They come as two modules, and the split is deliberate: one concern each,
the way the [ISA][isa], the [CRC][crc], and the rest are one concern each.

That has been the shape of this whole descent: the [engine's map][shape]
named eight boxes and opened one, and every post since has opened the next
in the order the engine reaches for it --- `decode` first, then the
[registers][regfile] its operands named, and now the compute those
registers feed. Two boxes fall in one post here because they are siblings:
both are handed the same pair of register reads, both answer in a single
cycle, and only the shape of the answer --- a value, or a verdict --- tells
them apart.

The ALU is the larger story, so we read it first, all the way down --- and
its very first two lines are a puzzle worth stopping on.

## The two-layer plan

`Tamal.Alu`'s door lists one type and two functions:

```haskell
module Tamal.Alu
  ( AluOp (..)
  , alu
  , dataResult
  ) where
```

`AluOp` is exported with `(..)` --- all its constructors, because callers
name them --- and then `alu` and `dataResult`. Two functions for one
module is the shape to notice: they are two *layers*, and the layering is
the whole design. `alu` is the thin core --- give it an operation and two
values, it computes. `dataResult` is the wrapper --- give it a decoded
`Instr` and two register values, and it works out *which* operation, pulls
any immediate out of the instruction, and calls `alu`. The core knows
arithmetic; the wrapper knows the instruction set. Keeping them apart is
what let the [dispatch post's][exec] `dataWb` hand the whole instruction to
`dataResult` and collapse thirteen arms into one: the wrapper reads the
operation off the instruction, so no caller has to.

## Two import lines, two collisions

Under the header sit three imports, and two of them carry scars:

```haskell
import Clash.Prelude hiding (And, Xor)
import Tamal.Isa (Instr)
import qualified Tamal.Isa as Isa
```

The [prelude swap][crc] is the line every block carries --- but here it
comes with a `hiding (And, Xor)` clause, and that clause is the first
symptom of a collision this module walks straight into on purpose. The
ALU wants to name its operations the obvious way: `Add`, `Sub`, `And`,
`Or`, `Xor`. That vocabulary is the plainest in all of computing --- and it
is already spoken for, twice.

The first claimant is the standard library. Modern `Data.Bits` ships
`newtype` wrappers called `And` and `Xor` --- little monoid boxes whose
`<>` is bitwise-and and bitwise-xor, handy for folding a pile of bits down
with `mconcat` --- and `Clash.Prelude` re-exports them.[^databits] So the
moment `AluOp` declares a constructor `And`, it clashes with the `And` the
prelude just brought in. The module's answer is to slam that door:
`hiding (And, Xor)` drops those two names from the import, leaving `AluOp`'s
constructors the only `And` and `Xor` in scope. The core never needed the
wrappers anyway --- it does bitwise work with the *operators* `.&.` and
`xor`, not the monoids. And `Or` escapes the whole affair, because
`Data.Bits` spells its or-wrapper `Ior`, not `Or`, so there is nothing to
hide.

The second claimant is closer to home: the [instruction set][isa] itself.
`Tamal.Isa`'s `Instr` has constructors `Add` and `Sub` --- the DATA
opcodes --- and `AluOp` wants those exact names for its operations. Two
types in scope, each with an `Add`: an unresolvable ambiguity. So `Instr`
comes in *qualified*, `import qualified Tamal.Isa as Isa`, and every
instruction constructor must be written `Isa.Add`, `Isa.Sub`, and so on,
while the bare `Add` and `Sub` belong to `AluOp`. (The unqualified
`import Tamal.Isa (Instr)` on the line between is just so the *type*
`Instr` can be named without a prefix in the signatures.)

Read together, the two lines are not clumsiness to apologise for --- they
are the two-layer split showing through the namespace. `AluOp` and `Instr`
both need a word for "add," because they live at two different levels: one
is a *hardware operation*, the other an *instruction*. The whole job of
`dataResult` is to translate the second into the first --- `Isa.Add` into
`alu Add` --- and a translator between two vocabularies only exists because
the two vocabularies are, deliberately, distinct. The collision is the
design, made visible at the import list.

> When two layers both need the word "add," the import list is where you
> can see the seam between them.
## The core: eight total operations

With the names sorted out, the enum is plain:

```haskell
data AluOp = Add | Sub | And | Or | Xor | Sll | Srl | Sra
  deriving stock (Generic, Show, Eq, Enum, Bounded)
  deriving anyclass (NFDataX)
```

Eight operations: add, subtract, the three bitwise ops, and three shifts
--- shift-left-logical, shift-right-logical, shift-right-arithmetic. The
[deriving refrain][shape] is the usual four, with two additions worth
naming: `Enum` and `Bounded`. Those two let a caller write
`[minBound .. maxBound]` and get *every* `AluOp` in order --- which is
precisely how the tests will enumerate the operations later, and a small
example of a derived instance bought for a specific downstream use.

The core itself is one total `case`:

```haskell
alu :: AluOp -> BitVector 32 -> BitVector 32 -> BitVector 32
alu op r1 r2 = case op of
  Add -> r1 + r2
  Sub -> r1 - r2
  And -> r1 .&. r2
  Or -> r1 .|. r2
  Xor -> r1 `xor` r2
  Sll -> r1 `shiftL` sh
  Srl -> r1 `shiftR` sh
  Sra -> pack (shiftR (unpack r1 :: Signed 32) sh)
 where
  sh :: Int
  sh = fromIntegral (unpack (truncateB r2) :: Unsigned 5)
```

The [type is half the documentation][primer]: an operation and two
thirty-two-bit values in, one out, always --- no `Maybe`, no failure. The
first five arms are the ones that need no comment: `Add` and `Sub` are
`BitVector`'s `Num`, wrapping modulo $2^{32}$; `And`, `Or`, `Xor` are the
operators `.&.`, `.|.`, `` `xor` `` the module hid the wrappers to keep
clear. That `Sub` is `r1 - r2` and nothing fancier is the same
two's-complement fact the [branch offset][exec] leaned on and the [CRC][crc]
met in its own algebra: subtraction is addition of the negation, one
operation on the hardware, which is why the tests can assert
`alu Sub a b === alu Add a (complement b + 1)` and have it hold by
construction.

The shifts are where the close reading earns its keep. All three take their
distance from `sh`, and `sh` is defined once in the `where`: the **low five
bits of operand B**, `truncateB r2` narrowed to a `BitVector 5`, unpacked to
an `Unsigned 5`, and read as an `Int` in `0..31`. Masking the shift amount
to five bits is a RISC-V habit with a real payoff --- a five-bit count can
only ask for a shift of zero through thirty-one, so a shift by thirty-two or
more is *impossible to express*, and the "shift wider than the word"
undefined behaviour that haunts C simply has no encoding here. A shift by 32
masks to a shift by 0; the operation stays total because its argument was
range-limited before it arrived.[^mask]

Then the split between `Srl` and `Sra`, which is the subtlest line in the
module. `Srl` is ``r1 `shiftR` sh`` --- and `BitVector`'s `shiftR` is
**logical**, filling from the top with zeros. `Sra` wants the *arithmetic*
shift, the one that fills with copies of the sign bit so that shifting a
negative number right divides it. `BitVector` has no sign to preserve, so
`Sra` borrows one: `unpack r1 :: Signed 32` reinterprets the same
thirty-two bits as a *signed* number, `shiftR` on `Signed` sign-fills, and
`pack` puts the bits back as a `BitVector`. Nothing moved but the *type*.
The bits are identical going in; reading them as `Signed` is what makes the
shift arithmetic, and reading them back as `BitVector` is what returns them
to the untyped wires the rest of the datapath speaks.[^sra] It is the
[CRC's][crc] "the kind of value is part of the contract" turned into a tool:
when the operation needs a sign, the code manufactures one by changing how
the bits are read, for exactly one expression, and changes it back.

And notice what is *not* in the `case`: a ninth arm, an error, a reserved
op. `AluOp` has eight constructors and `alu` handles eight; it is total
with nothing left over. That is only possible because the one reserved shift
encoding --- the `0b11` op field --- never reaches here: the [instruction
set][isa] traps it at decode, the [`SHIFT` op `0b11` that the isa post's
test pinned][isa]. The reserved case is handled one layer out, at the
decoder, so the core can be a clean eight-way truth table --- which in
hardware is exactly what it becomes, a bank of adders and shifters and gate
arrays with `op` selecting one.[^lion]

And a truth table computes *everything at once*. There is no clock inside
`alu` and no sequencing --- the [primer's][primer] combinational logic --- so
every arm's gates exist and run every cycle: the adder adds, the shifters
shift, the gate arrays and-or-xor in parallel, and the `case` is a
multiplexer at the *output* that keeps the one result `op` asked for and
discards the rest. The apparent waste is the shape of hardware, and it is
also why the whole of `alu` resolves inside the single `Exec` cycle the
[dispatch post][exec] spent on a DATA instruction: one operation chosen from
eight computed, no time borrowed.

## The wrapper: resolving operand B

`alu` computes; it does not know an instruction from a hole in the ground.
`dataResult` is the layer that does, and it is where the DATA group's
compute opcodes fold onto the core's eight operations:

```haskell
dataResult :: Instr -> BitVector 32 -> BitVector 32 -> BitVector 32
dataResult instr rs1v rs2v = case instr of
  Isa.LoadImm _ imm -> signExtend imm
  Isa.Lui _ imm21 -> (zeroExtend imm21 :: BitVector 32) `shiftL` 11
  Isa.Mov _ _ -> rs1v
  Isa.Add _ _ _ -> alu Add rs1v rs2v
  Isa.Addi _ _ imm -> alu Add rs1v (signExtend imm)
  -- … Sub, And_/Andi, Or_/Ori, Xor_/Xori, all the same shape …
  Isa.Shift _ _ shOp amt -> alu (toAluShift shOp) rs1v (zeroExtend amt)
  _ -> 0 -- BUS / CTRL / RDSR: never routed here by the Engine
 where
  toAluShift :: BitVector 2 -> AluOp
  toAluShift = \case
    0b00 -> Sll
    0b01 -> Srl
    _ -> Sra -- 0b10; 0b11 is unreachable (decode traps it)
```

Read the arms in three groups. The first three *bypass* `alu` entirely,
because none of them is arithmetic --- they are ways to get a *constant*, or
a copy, into a register. `LoadImm` sign-extends its twenty-one-bit
immediate, so one instruction reaches the whole signed-twenty-one range
(`li rd, -1` in a single word); `Lui` places the upper immediate for the
two-instruction constants; and `Mov` is the degenerate case, an operation
with nothing to compute --- it copies `rs1v`, ignores `rs2v`, the identity
function wearing an opcode. The last real arm, `Shift`, dispatches to one of
the three shift ops via `toAluShift`. And everything between --- the arithmetic and
logic --- is the group that reveals *why the wrapper exists*: look at `Add`
and `Addi` sitting one line apart.

```haskell
  Isa.Add _ _ _ -> alu Add rs1v rs2v
  Isa.Addi _ _ imm -> alu Add rs1v (signExtend imm)
```

Both call `alu Add rs1v`. The only difference is the second operand: `Add`
uses `rs2v`, the value of the second register; `Addi` uses `signExtend imm`,
the instruction's immediate widened to thirty-two bits. That is the entire
distinction between a register-register op and its immediate form, and it
repeats for `And`/`Andi`, `Or`/`Ori`, `Xor`/`Xori`. So the arithmetic and
logic opcodes collapse onto eight operations not by coincidence but because
each register/immediate pair is *one* operation over *one* choice of operand B:
a register value, or an immediate. `dataResult`'s job, boiled down, is to
**resolve operand B** and hand off. This is the other side of the
[dispatch post's][exec] collapse --- there, thirteen arms became one
`dataWb` because "`dataResult` reads the operation off the instruction";
here is the reading, and the eight-fold fan-out it feeds.

Step back and the operand path is complete across three posts. The
[dispatch post's][exec] `operandRs1` and `operandRs2` tables said *which*
registers an instruction reads; the [register file][regfile] turned those
names into the values `rs1v` and `rs2v`; and `dataResult` says *which
operation*, and where operand B comes from. Four pieces in a line --- the
selector tables, `readReg`, `dataResult`, `writeReg` --- are a DATA
instruction's entire datapath, and every one of them was built and tested
alone before the engine wired them in a row. That was the wager of reading
[outside in][shape], paid back a leaf at a time.

The immediates are widened by one rule, almost: every I-form immediate
*sign-extends* --- `signExtend imm`, so an immediate with its top bit set is
negative and `addi rd, rd, -1` subtracts one --- and the single exception is
the shift amount, `zeroExtend amt`, because a shift distance is a count, not
a signed value.[^li] `Lui` is the one placement worth a second look:
``(zeroExtend imm21 :: BitVector 32) `shiftL` 11`` takes the twenty-one-bit
immediate the [ISA post][isa] watched get carved out of the `rs1 ++ rs2 ++
imm` fields --- the widest immediate in the set, from the fields its opcodes
had spare --- and lays it into bits `[31:11]`, low eleven zero. That shift
of eleven is chosen so `Lui`'s bottom edge meets `Addi`'s sign-extended top,
which is what lets the assembler build any thirty-two-bit constant in two
instructions.[^li]

Two things the wrapper does *not* do, both by design. It takes register
*values* `rs1v` and `rs2v`, never a `Regs` --- so it has no idea the
[register file][regfile] exists, and the `x0`-hardwiring and the
discarded-write-to-`x0` we read last post are *not* its concern; the engine
reads `x0` as zero on the way in and drops writes to `x0` on the way out,
and `dataResult` computes on whatever values it is handed. The compute
layer sits *between* the two register ports and touches neither: the engine
wires `readReg`'s outputs into `dataResult` and `dataResult`'s output into
`writeReg`. Each leaf minds one concern --- the register file was engine-
ignorant, the ALU is register-file-ignorant --- and the engine is the only
place that knows them all.

And the `_ -> 0` at the bottom is the totality tax. `dataResult` matches on
the whole `Instr` type, so Clash demands every constructor have an answer
--- but the BUS, CTRL, and `RDSR` instructions never arrive here, because
the engine routes only DATA-compute through `dataResult` and handles the
rest itself. The default is unreachable *in the engine*; `0` is a safe,
deterministic value for a case the wiring guarantees will never come up.[^default]
<figure class="cmp-fig" style="margin:2rem 0">
<svg class="cmp" viewBox="0 0 760 350" role="img" aria-labelledby="cmp-t cmp-d" xmlns="http://www.w3.org/2000/svg">
<title id="cmp-t">The tamal compute layer: the two-layer ALU and the branch comparator</title>
<desc id="cmp-d">A dataflow diagram. On the left, two register values rs1v and rs2v and an immediate enter. Operand A of the ALU is rs1v; operand B is chosen by a multiplexer between rs2v, used by the register-register forms, and the sign-extended immediate, used by the immediate forms. The ALU core, drawn in the accent colour, applies one of eight operations selected by AluOp: Add, Sub, And, Or, Xor, Sll, Srl, Sra. A final multiplexer selects between the ALU result and the constant placers LoadImm, Lui and Mov to produce dataResult, which flows to writeReg rd. Below, a separate branchTaken comparator takes the same two register values and one of Beq, Bne, Bltu, Bgeu and returns taken as a Bool, while the engine does the PC math.</desc>
<style>
.cmp{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.cmp .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.cmp .boxA{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.cmp .mux{fill:var(--bg-main);stroke:var(--fg-main);stroke-width:1.8}
.cmp .muxA{fill:var(--bg-main);stroke:var(--accent);stroke-width:2}
.cmp .t{fill:var(--fg-main);font-family:var(--mono);font-size:13px}
.cmp .tA{fill:var(--accent);font-family:var(--mono);font-size:14px}
.cmp .s{fill:var(--fg-dim);font-family:var(--mono);font-size:11px}
.cmp .io{fill:var(--fg-main);font-family:var(--mono);font-size:12px}
.cmp .w{stroke:var(--fg-main);stroke-width:2;fill:none}
.cmp .wa{stroke:var(--accent);stroke-width:2.5;fill:none}
.cmp .nd{fill:var(--fg-main)}
.cmp .ah{fill:var(--fg-main)}
</style>
<defs>
<marker id="cmp-a" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="ah"/></marker>
</defs>
<!-- inputs -->
<text class="io" x="14" y="100" text-anchor="start">rs1v</text>
<text class="io" x="14" y="150" text-anchor="start">rs2v</text>
<text class="io" x="14" y="176" text-anchor="start">imm / amt</text>
<!-- operand A: rs1v -> alu -->
<path class="w" d="M56,96 H300" marker-end="url(#cmp-a)"/>
<!-- rs2v -> operand-B mux (reg) -->
<path class="w" d="M56,146 H214"/>
<!-- imm -> operand-B mux (sign-extended) -->
<path class="w" d="M82,172 H214"/>
<text class="s" x="150" y="190" text-anchor="middle">sext imm</text>
<!-- operand-B mux -->
<path class="muxA" d="M214,140 L248,150 L248,168 L214,178 Z"/>
<text class="s" x="227" y="163" text-anchor="middle">B</text>
<path class="w" d="M248,159 H300" marker-end="url(#cmp-a)"/>
<!-- alu core -->
<rect class="boxA" x="300" y="66" width="180" height="104" rx="8"/>
<text class="tA" x="390" y="94" text-anchor="middle">alu</text>
<text class="s" x="390" y="122" text-anchor="middle">Add  Sub  And  Or  Xor</text>
<text class="s" x="390" y="142" text-anchor="middle">Sll  Srl  Sra</text>
<text class="s" x="390" y="162" text-anchor="middle">total · dispatched by AluOp</text>
<!-- alu -> final mux -->
<path class="wa" d="M480,118 H540"/>
<!-- constant placers -->
<rect class="box" x="300" y="200" width="180" height="48" rx="6"/>
<text class="t" x="390" y="222" text-anchor="middle">LoadImm · Lui · Mov</text>
<text class="s" x="390" y="240" text-anchor="middle">constant / pass-through</text>
<path class="w" d="M480,224 H540"/>
<!-- final select mux -->
<path class="mux" d="M540,104 L572,124 L572,218 L540,238 Z"/>
<path class="w" d="M572,171 H612" marker-end="url(#cmp-a)"/>
<text class="t" x="618" y="167" text-anchor="start">dataResult</text>
<text class="s" x="618" y="185" text-anchor="start">→ writeReg rd</text>
<!-- branch taps off rs1v / rs2v -->
<circle class="nd" cx="86" cy="96" r="3.5"/>
<path class="w" d="M86,96 V300 H300" marker-end="url(#cmp-a)"/>
<circle class="nd" cx="110" cy="146" r="3.5"/>
<path class="w" d="M110,146 V316 H300" marker-end="url(#cmp-a)"/>
<!-- branch comparator -->
<rect class="box" x="300" y="284" width="180" height="46" rx="6"/>
<text class="t" x="390" y="306" text-anchor="middle">branchTaken</text>
<text class="s" x="390" y="324" text-anchor="middle">Beq · Bne · Bltu · Bgeu</text>
<path class="w" d="M480,307 H512" marker-end="url(#cmp-a)"/>
<text class="t" x="518" y="303" text-anchor="start">taken : Bool</text>
<text class="s" x="518" y="321" text-anchor="start">engine does PC math</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The compute layer as two functions. <code>dataResult</code> (top) resolves operand B --- a register value <code>rs2v</code> or a sign-extended immediate --- and feeds the accent <code>alu</code> core, the eight <code>AluOp</code>s the DATA compute collapses onto; <code>Lui</code>, <code>Mov</code> and <code>LoadImm</code> bypass the core as constants, and a final select yields the value written back. <code>branchTaken</code> (bottom) is the sibling: the same two register values, one of four comparisons, a single <em>taken</em> bit --- the decision, with the PC arithmetic kept in the engine. Both take <em>values</em>, not registers; the compute layer sits between the two register ports and touches neither. (The shift amount uses <code>zeroExtend amt</code>, not sign-extension.)</figcaption>
</figure>

## The comparator

`Tamal.Branch` is the sidecar in that diagram, and it is small enough to
read whole:

```haskell
module Tamal.Branch
  ( BranchOp (..)
  , branchTaken
  ) where

import Clash.Prelude

data BranchOp = Beq | Bne | Bltu | Bgeu
  deriving stock (Generic, Show, Eq, Enum, Bounded)
  deriving anyclass (NFDataX)

branchTaken :: BranchOp -> BitVector 32 -> BitVector 32 -> Bool
branchTaken op r1 r2 = case op of
  Beq -> r1 == r2
  Bne -> r1 /= r2
  Bltu -> r1 < r2
  Bgeu -> r1 >= r2
```

Note the import: a plain `import Clash.Prelude`, no `hiding` clause. The
ALU had to fight for its names; the branch comparator does not, because
`Beq`, `Bne`, `Bltu`, `Bgeu` collide with nothing --- they are the module's
own coinage, not words the standard library or the instruction set already
owns. The absence of a scar is itself a small tell: this module reuses no
vocabulary, so it borrows no trouble.

`branchTaken` returns a `Bool` and nothing else --- *taken, or not*. That is
the entire point of the split the [dispatch post][exec] set up: the
comparator decides, and the engine jumps. When the engine ran `branch`, it
called `Br.branchTaken` for the yes/no and then did the program-counter
arithmetic *itself* --- the eleven-bit signed offset truncated to ten bits
and added modulo the counter, backwards jumps falling out of the
[two's-complement wraparound][exec]. None of that is here. `branchTaken`
never sees the offset, never sees the PC; it is a pure predicate on two
values, and the offset math belongs to the only thing that knows where the
program counter is. That is why `Branch` needs no `Instr` wrapper the way
`Alu` did: there is no immediate to resolve, no operand B to mux --- both
operands are registers --- so mapping the four branch constructors to
`BranchOp` is a one-line remap the engine does inline, and a `branchResult`-
on-`Instr` layer would earn nothing. `Alu` grew a second layer because it
had immediates to fold in; `Branch` stayed one because it did not.

The four comparisons hold one detail that is easy to miss and important to
get right. `Beq` and `Bne` are `==` and `/=`, unremarkable. But `Bltu` and
`Bgeu` --- the "u" is for *unsigned* --- are just `<` and `>=`, and they are
unsigned *for free*, because they operate on `BitVector`, and `BitVector`'s
`Ord` is unsigned. A `BitVector 32` has no sign bit to interpret; ordered,
it compares as a plain thirty-two-bit magnitude, `0xFFFFFFFF` the largest
value rather than $-1$. That is *exactly* the `BLTU`/`BGEU` semantics, so
the comparator gets them by writing `<` and doing nothing else.

This is the mirror image of the ALU's `Sra`, and the two together are the
whole story of how this layer handles number types. `Sra` *reached for*
`Signed 32`, reinterpreting its bits to borrow a sign it needed for an
arithmetic shift. `branchTaken` *stays on* `BitVector` to keep the sign
away, because an unsigned compare is what it wants. Same thirty-two bits,
opposite choices --- and each is made by choosing the type the bits are read
through, for exactly the operation that needs it.

> The bits never change. The type you read them through is the operation.

Signed branches --- `BLT`, `BGE`, the ones that *would* need `Signed`'s
`Ord` --- are simply not in v1, reserved for a later phase; the four here
are the unsigned pair and the two equalities, and every one of them is a
single operator on the untyped word.
## The tests

Two test files, and they pose a question the [CRC][crc] and the
[memories][mem] did not have to: when your reference model is nearly
identical to your implementation, what does a test *buy*? `alu Add a b ===
a + b` checks that add is add --- a near-tautology. The answer is that the
boring arms are there to catch a *wiring* slip (an `Add` that quietly does
`.|.`), and the real value lives in the arms where the behaviour is subtle.
Those get pinned twice, by property and by hand:

```haskell
testProperty "Sub == Add of two's complement" $ property $ do
  a <- forAll genWord
  b <- forAll genWord
  alu Sub a b === alu Add a (complement b + 1)
```

```haskell
testCase "Sra 0x80000000 by 1 = 0xC0000000 (sign-fill)"
  $ alu Sra 0x80000000 1
  @?= 0xC0000000
testCase "Srl 0x80000000 by 1 = 0x40000000 (zero-fill)"
  $ alu Srl 0x80000000 1
  @?= 0x40000000
```

The first states the two's-complement identity as a law. The two concrete
vectors are the sharpest lines in the suite: the *same* input,
`0x80000000`, shifted right by one, gives `0xC0000000` under `Sra` and
`0x40000000` under `Srl` --- the arithmetic shift copying the sign bit down,
the logical shift feeding a zero. If the `unpack … :: Signed 32`
reinterpretation in `Sra` were ever dropped, the first case goes red
instantly. Around them sit the property that the shift amount is masked to
five bits (`alu Sll a b === alu Sll a (b .&. 0x1F)`), that a shift by zero is
identity, and that `Sra` preserves the sign bit --- the whole subtle corner,
staked down.

The `dataResult` tests check the *other* layer: not the arithmetic, but the
wiring that routes each opcode to it. `Mov` returns `rs1v`; `LoadImm`
sign-extends; `Lui` lands its immediate at `[31:11]` with the low eleven
zero (`(r .&. 0x7FF) === 0`); each immediate form agrees with `alu` on the
sign-extended operand, each register form agrees with `alu` on `rs2v`. They
prove the wrapper dispatches correctly, independently of whether the core
computes correctly --- two layers, two sets of tests.

`Test.Branch` gets to do something the others cannot, and it is the
`Enum`/`Bounded` deriving paying off:

```haskell
testProperty "branchTaken matches reference (all ops)" $ property $ do
  op <- forAll (Gen.element [minBound .. maxBound])
  a <- forAll genWord
  b <- forAll genWord
  branchTaken op a b === ref op a b
```

`[minBound .. maxBound]` is *every* `BranchOp`, enumerable because the type
derives `Enum` and `Bounded`, so "for all ops" is literal rather than a
hand-kept list that could fall out of date. Beside it, the complementarity
properties --- `Beq` is exactly `not Bne`, `Bltu` exactly `not Bgeu` --- fall
free out of the comparator's structure, and three concrete vectors stand
guard over the unsigned reading:

```haskell
testCase "Bltu is unsigned: 0x7FFFFFFF < 0x80000000"
  $ branchTaken Bltu 0x7FFFFFFF 0x80000000
  @?= True
```

Read as *signed*, `0x80000000` is the most negative number and
`0x7FFFFFFF` the most positive, so a signed `<` would call this `False`.
Read as *unsigned* --- which is what `BitVector`'s `Ord` does --- `0x80000000`
is the larger, and the answer is `True`. This one case is the tripwire: the
day someone reinterprets the operands as `Signed` by mistake, it goes red
and names the bug. It is the [regfile tests'][regfile] discipline in a
different key --- the smallest input that distinguishes the behaviour you
want from the one you might slip into.
## What we read

The two computing boxes of the [dispatch post][exec], opened. `Tamal.Alu`
in two layers: a thin, total `alu` core dispatched by an eight-constructor
`AluOp` over register values --- its `Sub` the two's-complement add, its
shifts masked to five bits so a shift past the word is well-defined, its
`Sra` borrowing a `Signed` reading for one expression while `Srl` stays
logical --- and above it `dataResult`, the wrapper that places the `Lui`,
`Mov`, and `LoadImm` constants and resolves operand B, so the arithmetic
and logic opcodes collapse onto eight operations because each register/immediate pair
is one operation over one choice of second operand. It takes *values*, so
it never meets the [register file][regfile]; it sits between the two ports
and touches neither. Two import lines that were both naming collisions ---
`And` and `Xor` hidden from the prelude, `Isa` qualified --- because a
compute layer and an instruction set both need the word "add," and the
translator between them exists precisely because they are kept apart. And
`Tamal.Branch`, four lines returning only taken-or-not, its `Bltu`/`Bgeu`
unsigned for free from `BitVector`'s own `Ord`, the PC arithmetic left to
the engine --- the clean inverse of `Sra`'s reach for a sign. The bits never
changed; the type read through them was the operation, every time. And
tests that pin the subtle corners against near-identical references, and
enumerate every op because the enums derive `Bounded`.

`dataResult`'s value now flows back to `writeReg`, and `branchTaken`'s bit
to the program counter --- and with that, the compute is accounted for. Of
the leaves the [dispatch post][exec] named and held shut, only one is still
closed, and it is the one nearest the wire. When a `PUT` loaded its shifter
and the [bus post][bus] walked the beats, the drive on the lanes came from
`serializeX1`, and a turnaround from `tarBeat` --- names for a byte becoming
MSB-first bits on `IO[0]`, and for the handover clock, all of it built out
of a single tiny type: a lane, a `(value, enable)` pair, the whole tri-state
story in two bits. That serialiser is the last box. We open it next --- and
when it is open, the engine is open entire, map and every piece --- before
the series turns, in a later batch, to the impure shell that carries those
`(value, enable)` pairs the last step out, to the pins.

[^databits]: Since `base` 4.16 `Data.Bits` provides the `newtype` wrappers
`And`, `Ior`, `Xor`, and `Iff`, whose `Semigroup`/`Monoid` instances fold a
collection under bitwise-and, -or, -xor, and -xnor --- so `getAnd (foldMap
And xs)` ands a list together. `Clash.Prelude` re-exports `Data.Bits`, `And`
and `Xor` among them, so the moment `AluOp` declares constructors of those
names they clash; `hiding (And, Xor)` drops the imports, and the core does
its bitwise work with the operators `.&.` and `` `xor` `` anyway. `Or`
survives only by luck of spelling: the or-wrapper is `Ior` ("inclusive
or"), not `Or`, so nothing shadows `AluOp`'s `Or`.

[^mask]: `sh` is `fromIntegral (unpack (truncateB r2) :: Unsigned 5)`.
`truncateB r2` keeps the low five bits as a `BitVector 5`; `unpack` reads
them as an `Unsigned 5` (0..31); `fromIntegral` makes the `Int` that
`shiftL`/`shiftR` want. The detour through `Unsigned 5` is not optional:
`BitVector` has no `Integral` instance --- you cannot `fromIntegral` it
directly --- because a bare bag of bits has no agreed numeric reading, the
[CRC's][crc] point that a value's *kind* is part of its contract. Masking to
five bits before shifting is what makes a shift-by-≥-32 well-defined: the
count physically cannot exceed thirty-one, so the C-style "shift wider than
the type is undefined" has no representable input.

[^sra]: `unpack` and `pack` are the [`bitCoerce` family][isa] --- total,
zero-cost reinterpretations between types of the same bit-width, lowering to
no gates, only a relabelling of wires. `unpack r1 :: Signed 32` reads the
thirty-two bits as a two's-complement number; `shiftR` on `Signed` is
arithmetic, replicating the sign bit; `pack` reads the result back as a
`BitVector`. That sandwich is the whole mechanism: `BitVector`'s own
`shiftR` is logical, and the only difference between `Srl` and `Sra` is
which type does the shifting. Nothing is added or dropped --- the bits match
on both sides of the `unpack`/`pack` --- only their reading differs, signed
for the length of one shift.

[^li]: The eleven-bit shift on `Lui` is not arbitrary. `Lui` carries a
twenty-one-bit immediate (the [ISA post's][isa] `rs1 ++ rs2 ++ imm`,
$5 + 5 + 11 = 21$) and places it at bits `[31:11]`; `Addi` sign-extends its
eleven-bit immediate across `[10:0]` and up. Because `Lui`'s bottom bit
meets `Addi`'s sign-extended top, `li = LUI + ADDI` builds *any* thirty-two
-bit constant in at most two instructions, with sign-extension used
uniformly (the shift amount, a count, is the exception, `zeroExtend amt`).
An earlier draft shifted `Lui` by twelve to mirror RISC-V and left a
reachability gap needing a third instruction; widening the immediate to
twenty-one and realigning the shift to eleven closed it. The primitive here
fixes only `Lui = imm21 << 11`; the constant-tiling lives in the assembler.

[^default]: `dataResult` matches the entire `Instr` type, so Clash requires
a total function --- every constructor needs a right-hand side. The BUS,
CTRL, and `RDSR` instructions have one only as a formality: the engine
dispatches on the decoded *group* and routes just DATA-compute through
`dataResult`, handling `RDSR` (which reads engine state, not operands) and
the branches and bus ops on their own paths. So `_ -> 0` is unreachable by
construction, and `0` is a deterministic placeholder, not a computed value.
A `Maybe`-returning variant was considered and rejected: every input the
engine actually delivers yields a real value, so the `Nothing` would never
fire and would only burden every caller with an unwrapping.

[^lion]: The "small total `alu` core plus a `decode` that returns `Either`"
division is borrowed from [Lion](https://github.com/standardsemiconductor/lion),
an RV32I core in Clash: keep the arithmetic core total by pushing every
reserved or illegal encoding out to the decoder, so the datapath never has
to represent "no answer." tamal diverges from RV32I in the particulars ---
its own opcode groups, eleven-bit immediates, unsigned-only branches in v1
--- so it does not inherit RV32I's formal-verification harness; hedgehog
property tests are the verification baseline instead.
