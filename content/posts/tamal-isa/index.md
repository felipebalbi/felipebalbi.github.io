+++
title = "Tamal: The Instruction Set"
date = 2026-08-05T09:00:00
draft = true
description = "The first shut box, opened: Tamal's instruction set, the Instr ADT and the total encode and decode that every road out of Exec began by calling. The 32-bit word and its six fields --- group, sub, rd, rs1, rs2, imm --- split and joined by bitCoerce; thirty-six constructors across three groups and a reserved fourth; encode as the easy direction, a total case that lays each opcode's operands into their fields and zeroes the rest; decode as the hard one, dispatching on the group and rebuilding an instruction only when every reserved field is zero, so a junk word gets a defined rejection instead of undefined behaviour; the two laws that fall out --- decode after encode is the identity, and every one of the four billion possible words that decode accepts re-encodes to itself --- which together make the encoding canonical, one word per instruction and one instruction per word; the folded Config, a decode within the decode that turns six bits into a role, an I/O width, a clock, and an alert source and rejects every selection v1 does not implement; and the property test that checks totality not on a handful of samples but across the entire word space."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine", "isa"]
+++

For [three posts][shape] we held the leaves shut. We read the engine's
[shape][shape], watched it [dispatch an instruction][exec], and [ran its
wire][bus], and all the while `decode`, `dataResult`, `branchTaken`, and
the rest were names with one-line jobs and no insides. Now we start
opening them --- in the order the engine reaches for each --- and the
very first thing the engine does to any word, before it can compute or
branch or drive a pin, is `decode` it. So the descent begins where every
road out of `Exec` began: in `Tamal.Isa`, the instruction set itself.

This is a leaf again, read whole the way the [CRC][crc] and the
[memories][mem] were --- but it is the leaf the entire engine is written
against. Every constructor `execInstr` matched, every field it pulled out
with an underscore or a name, every trap that fired on a word the engine
could not read: all of it is defined here, in one file, as a type and two
functions that are exact inverses of each other over exactly the words
that are legal.

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
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## The word

Every tamal instruction is exactly thirty-two bits, and those bits are
carved into six fields the same way for every opcode. The module's
doc-comment draws the map:

```text
 31 30 | 29 .. 26 | 25 .. 21 | 20 .. 16 | 15 .. 11 | 10 .. 0
 group |   sub    |    rd    |   rs1    |   rs2    |  imm
```

Two bits of **group**, four of **sub**, then three five-bit register
selectors --- **rd**, **rs1**, **rs2** --- and an eleven-bit **imm** at
the bottom. The group and sub together are the opcode: the group picks
one of four families, the sub picks an operation within it. The three
register fields and the immediate are the operands, and which of them an
opcode actually uses depends on the opcode --- the rest are *reserved*,
and we will see that "reserved" is a promise the decoder enforces, not a
comment.

<figure class="isaw-fig" style="margin:2rem 0">
<svg class="isaw" viewBox="0 0 760 138" role="img" aria-labelledby="isaw-t isaw-d" xmlns="http://www.w3.org/2000/svg">
<title id="isaw-t">The 32-bit tamal instruction word and its six fields</title>
<desc id="isaw-d">A 32-bit instruction word drawn left to right, most significant bit 31 on the left. Six fields shown in proportion to their bit counts: a two-bit group in bits 31 to 30 and a four-bit sub in bits 29 to 26 together form the opcode; then a five-bit rd in bits 25 to 21, a five-bit rs1 in bits 20 to 16, a five-bit rs2 in bits 15 to 11, and an eleven-bit imm in bits 10 to 0 are the operands.</desc>
<style>
.isaw{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.isaw .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.isaw .boxA{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.isaw .nm{fill:var(--fg-main);font-family:var(--mono);font-size:14px}
.isaw .nmA{fill:var(--accent);font-family:var(--mono);font-size:14px}
.isaw .rng{fill:var(--fg-dim);font-family:var(--mono);font-size:11.5px}
.isaw .brk{stroke:var(--fg-dim);stroke-width:1.5;fill:none}
.isaw .brkA{stroke:var(--accent);stroke-width:1.5;fill:none}
.isaw .cap{fill:var(--fg-dim);font-family:var(--sans);font-size:11.5px}
.isaw .capA{fill:var(--accent);font-family:var(--sans);font-size:11.5px}
</style>
<!-- opcode / operand brackets -->
<path class="brkA" d="M54,50 V42 H174 V50"/>
<text class="capA" x="114" y="34" text-anchor="middle">opcode</text>
<path class="brk" d="M174,50 V42 H694 V50"/>
<text class="cap" x="434" y="34" text-anchor="middle">operands</text>
<!-- field boxes -->
<rect class="boxA" x="54" y="56" width="40" height="46" rx="4"/>
<text class="rng" x="74" y="83" text-anchor="middle">31:30</text>
<text class="nmA" x="74" y="122" text-anchor="middle">group</text>
<rect class="boxA" x="94" y="56" width="80" height="46" rx="4"/>
<text class="rng" x="134" y="83" text-anchor="middle">29:26</text>
<text class="nmA" x="134" y="122" text-anchor="middle">sub</text>
<rect class="box" x="174" y="56" width="100" height="46" rx="4"/>
<text class="rng" x="224" y="83" text-anchor="middle">25:21</text>
<text class="nm" x="224" y="122" text-anchor="middle">rd</text>
<rect class="box" x="274" y="56" width="100" height="46" rx="4"/>
<text class="rng" x="324" y="83" text-anchor="middle">20:16</text>
<text class="nm" x="324" y="122" text-anchor="middle">rs1</text>
<rect class="box" x="374" y="56" width="100" height="46" rx="4"/>
<text class="rng" x="424" y="83" text-anchor="middle">15:11</text>
<text class="nm" x="424" y="122" text-anchor="middle">rs2</text>
<rect class="box" x="474" y="56" width="220" height="46" rx="4"/>
<text class="rng" x="584" y="83" text-anchor="middle">10:0</text>
<text class="nm" x="584" y="122" text-anchor="middle">imm</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">The 32-bit instruction word, MSB (bit 31) on the left. The two-bit <code>group</code> and four-bit <code>sub</code> form the opcode; <code>rd</code>, <code>rs1</code>, <code>rs2</code> are five-bit register selectors and <code>imm</code> is an eleven-bit immediate. Widths sum to exactly 32. Every opcode reads the same fields from the same bits; which operands it <em>uses</em> is what distinguishes them, and every bit an opcode does not use is reserved-must-be-zero.</figcaption>
</figure>

The register fields are five bits wide, and that width is a small piece of
the [RISC-V flavour][intro] the project borrows:

```haskell
-- | A 5-bit register selector (RISC-V-standard field width). v1 uses x0..x15.
type Reg = BitVector 5
```

Five bits can name thirty-two registers; v1 implements sixteen and lets
the top bit go unused, so `Reg` is wider than the register file behind it.
That is deliberate room to grow, and it means a register selector is just
five bits of a word --- not yet an index into anything, only a name. The
thing it names is the [next post's][shape] subject; here it is a field.

## One constructor per opcode

The instruction set is a Haskell data type. Every opcode is a
constructor, and every constructor carries exactly the operands its opcode
uses --- no more, so the type itself refuses to build an `Add` without
three registers or a `Halt` without a status:

```haskell
data Instr
  = -- BUS group (group 00)
    CsAssert
  | CsDeassert
  | PutByteImm (BitVector 8)
  | PutByteReg Reg
  | GetByte Reg
  | PutBitsImm (Index 8) (BitVector 8)  -- n-1, bits (n = count in 1..8)
  | PutBitsReg Reg (Index 8)
  | GetBits Reg (Index 8)
  | TarImm (BitVector 4)
  | TarReg Reg
  | RstAssert
  | RstDeassert
  | GetAlert Reg
  | -- CTRL group (group 01)
    Halt (BitVector 8)
  | Beq Reg Reg (BitVector 11)
  | Bne Reg Reg (BitVector 11)
  | Bltu Reg Reg (BitVector 11)
  | Bgeu Reg Reg (BitVector 11)
  | WaitOn Reg (BitVector 2) (BitVector 9)  -- rd, cond, timeout
  | SetConfig (BitVector 6)
  | Mark (BitVector 11) Reg               -- label, payload reg
  | CrcReset
  | -- DATA group (group 10)
    LoadImm Reg (BitVector 21)
  | Lui Reg (BitVector 21)
  | Mov Reg Reg
  | Add Reg Reg Reg
  | Addi Reg Reg (BitVector 11)
  | Sub Reg Reg Reg
  -- … And_, Andi, Or_, Ori, Xor_, Xori, all Reg Reg (Reg | imm) …
  | Shift Reg Reg (BitVector 2) (BitVector 5)  -- rd, rs1, op, amt
  | Rdsr Reg (BitVector 5)                     -- rd, sr#
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

Thirty-six constructors, three groups, and every one of them is a name you
have already seen `execInstr` [match on][exec]. The grouping is the
opcode's top structure made into comments: `group 00` is BUS, the
thirteen opcodes that touch the wire; `group 01` is CTRL, the nine for
flow and configuration; `group 10` is DATA, the fourteen that compute. The
fourth group, `11`, has no constructors at all --- it is reserved, and a
word in it decodes to nothing.

Read the operand types and the encoding starts to show through the ADT.
`PutByteImm` carries a `BitVector 8`, the byte it will drive. `Beq` carries
two `Reg`s and a `BitVector 11`, the two sources and the branch offset ---
the [PC arithmetic from the dispatch post][exec] operated on exactly that
eleven-bit field. `PutBitsImm` carries an `Index 8` *and* a byte, because
"put n bits" needs both a count and the bits; the comment `n-1` warns that
the field holds one less than the count, a detail the encoder will handle.
And `LoadImm` carries a `BitVector 21` --- a twenty-one-bit immediate, wider
than the eleven-bit `imm` field, which is the one interesting packing
problem in the whole module. Hold that thought; the encoder solves it in
two lines.

Beside `Instr` sits the type that says why a word was *not* an
instruction:

```haskell
data DecodeError
  = ReservedFieldNonZero
  | OpcodeUnimplemented
  | IllegalOpcode
  deriving stock (Generic, Show, Eq)
  deriving anyclass (NFDataX)
```

Three ways to fail: a reserved field that was not zero, an opcode
recognised but not yet built, an opcode that is illegal outright. These
are the `Left` the [dispatch post's][exec] `stepExec` turned into a
trap[^unimpl] --- and now we are on the other side of that `Left`, in the
code that produces it.

## Encode: the easy direction

Turning an `Instr` into a word is the easy half, because an `Instr` is
already valid by construction --- the type would not let you build a
malformed one --- so the encoder only has to *place* the operands and
zero everything else. It is a total `\case`, one arm per constructor, and
every arm calls the same helper:

```haskell
encode :: Instr -> BitVector 32
encode = \case
  CsAssert       -> joinW (0b00, 0x0, 0, 0, 0, 0)
  PutByteImm b   -> joinW (0b00, 0x2, 0, 0, 0, zeroExtend b)
  GetByte rd     -> joinW (0b00, 0x4, rd, 0, 0, 0)
  Beq a b off    -> joinW (0b01, 0x1, 0, a, b, off)
  WaitOn rd c t  -> joinW (0b01, 0x5, rd, 0, 0, bitCoerce (c, t))
  Add rd a b     -> joinW (0b10, 0x3, rd, a, b, 0)
  Shift rd a op a5 -> joinW (0b10, 0xC, rd, a, 0, bitCoerce (op, 0 :: BitVector 4, a5))
  -- … thirty-odd arms in the same shape …
```

Every arm reads the same way: pick the `group` and `sub` that name the
opcode, drop the operands into their fields, and put `0` everywhere else.
`CsAssert` has no operands, so it is all opcode and zeros. `GetByte` uses
only `rd`. `Beq` fills `rs1`, `rs2`, and `imm` with its two sources and
its offset, and leaves `rd` zero. The zeros are the reserved fields, and
the encoder writes them faithfully --- which is what makes a decode able
to *check* them later.

`joinW` is the field-packer, and it is one of a small family of functions
that are all the same trick:

```haskell
type Fields = (BitVector 2, BitVector 4, BitVector 5, BitVector 5, BitVector 5, BitVector 11)

splitWord :: BitVector 32 -> Fields
splitWord = bitCoerce

joinW :: Fields -> BitVector 32
joinW = bitCoerce
```

`Fields` is the word's six fields as a tuple, and `joinW` and `splitWord`
are both just `bitCoerce` --- the same [structural bit-reinterpretation the
trace records used][bus] to pack a tag and a payload into one word. Here it
lays six fields end to end into thirty-two bits, or takes them back apart,
and the type system checks the arithmetic: `2 + 4 + 5 + 5 + 5 + 11 = 32`,
so the coercion is total and lossless, a reshaping of bits with nothing
added and nothing dropped.[^bitcoerce] `WaitOn` and `Shift` reach for
`bitCoerce` a second time, to pack *sub-fields* of the immediate --- a
condition and a timeout, or a shift-op and an amount --- into the eleven
bits they share.

And the twenty-one-bit immediate that did not fit? It is split across the
three fields nobody else was using:

```haskell
joinImm21 :: BitVector 5 -> BitVector 5 -> BitVector 11 -> BitVector 21
joinImm21 rs1 rs2 imm = bitCoerce (rs1 ++# rs2 ++# imm)
```

`LoadImm` and `Lui` have no register sources, so `rs1`, `rs2`, and `imm`
--- five plus five plus eleven --- sit empty and adjacent, exactly
twenty-one bits, exactly the room a wide immediate needs. The encoder
glues them into one `BitVector 21` on the way out and the decoder splits
them back on the way in. It is a small, satisfying piece of format design:
the widest immediate in the ISA is carved out of the fields the
immediate-loading opcodes had no other use for.

## Decode, and the reserved zero

Decoding is the hard direction, because now the input is *any* thirty-two
bits, most of which are not valid instructions, and the decoder has to
tell which is which. It begins by splitting the word and dispatching on the
group:

```haskell
decode :: BitVector 32 -> Either DecodeError Instr
decode w =
  case grp of
    0b00 -> decodeBus sub' rd rs1 rs2 imm
    0b01 -> decodeCtrl sub' rd rs1 rs2 imm
    0b10 -> decodeData sub' rd rs1 rs2 imm
    _ -> Left IllegalOpcode -- group 11 reserved
 where
  (grp, sub', rd, rs1, rs2, imm) = splitWord w
```

`splitWord` takes the word apart into the six fields --- the inverse of the
`joinW` the encoder used --- and the group selects one of three per-group
decoders, or, for the reserved group `11`, rejects outright. Each
per-group decoder is a `case` on the sub-opcode that rebuilds the
constructor. But rebuilding is not enough: the decoder must also *refuse* a
word whose reserved fields are not zero, and that refusal is the heart of
the module. It runs through one tiny guard:

```haskell
only :: Bool -> Instr -> Either DecodeError Instr
only ok r = if ok then Right r else Left ReservedFieldNonZero
```

`only` takes a condition and an instruction and hands back the instruction
*only* when the condition holds. The condition is always the same kind of
thing --- "every field this opcode does not use is zero" --- and you can
read it plainly in the BUS decoder:

```haskell
decodeBus sub' rd rs1 rs2 imm =
  case sub' of
    0x0 -> only (z rd && z rs1 && z rs2 && z imm) CsAssert       -- no operands
    0x2 -> only (z rd && z rs1 && z rs2 && immHi8 == 0) (PutByteImm (truncateB imm))
    0x3 -> only (z rd && z rs2 && z imm) (PutByteReg rs1)         -- rs1 used
    0x4 -> only (z rs1 && z rs2 && z imm) (GetByte rd)            -- rd used
    -- …
    0xc -> only (z rs1 && z rs2 && z imm) (GetAlert rd)
    _ -> Left IllegalOpcode
 where
  z = (== 0)
  immHi8 = slice d10 d8 imm  -- imm[10:8] reserved for PUT_BYTE
```

`z` is "is zero." Every arm names the fields its opcode *uses* and asserts
`z` on all the others. `CsAssert` uses nothing, so all four operand fields
must be zero. `GetByte` uses `rd`, so `rs1`, `rs2`, and `imm` must be zero.
`PutByteImm` uses the low eight bits of `imm` for its byte and reserves
the top three --- `immHi8 == 0` --- so a `PUT_BYTE` word with junk in
`imm[10:8]` is rejected even though its opcode is real. The decoder is
strict about the whole word, not just the opcode: a legal instruction is a
legal opcode *and* clean reserved bits. `decodeCtrl` and `decodeData` are
the same shape at greater length, each checking the exact bits its
opcodes leave unused --- `HALT` reserving `imm[10:8]`, `SET_CONFIG`
reserving `imm[10:6]`, the reserved shift-op `0b11` refused, and so on.

## Total, both ways

Why be this strict? Because the strictness buys two laws, and the two laws
are what make an instruction encoding trustworthy.

The first is the **round trip**: `decode (encode i) == Right i`. Encode any
instruction and decode the result and you get the instruction back. This
is nearly free --- the encoder places operands and zeroes reserved fields,
the decoder reads operands and checks reserved fields are zero, so of
course they agree. It says the encoder and decoder are inverses on the
instructions.

The second is deeper and is the one the reserved zero is really for:
**canonicity**. Over *every* thirty-two-bit word --- not just the ones the
encoder produces, but all four billion of them --- any word `decode`
accepts re-encodes to itself: `encode (decode w) == w` whenever `decode w`
succeeds. There are no two words that decode to the same instruction,
because the only words decode accepts are the ones with zero reserved
fields, which is exactly the set the encoder emits. Drop the reserved-field
checks and this law dies: a hundred different `imm[10:8]` values would all
decode to the same `PUT_BYTE`, the mapping would be many-to-one, and the
word would no longer be a *canonical* name for the instruction. The
`only` guard is what keeps the correspondence one-to-one.

And under both laws sits totality. `decode :: BitVector 32 -> Either
DecodeError Instr` is a total function: every one of the $2^{32}$ possible
words has a defined answer, an `Instr` or a named error, with no partial
case, no crash, no loop. That is the [primer's][primer] "totality is a
truth table" cashed at scale --- decode is a four-billion-row truth table,
which in hardware is exactly what it becomes: a combinational decoder that
wires every input pattern to an output.[^truthtable] The [dispatch
post's][exec] `Left -> trap` was this totality made visible from the
outside: because decode has a defined answer for *every* word, a corrupted
or malicious program cannot reach an undefined state through the front
door. Every word either is an instruction or is a trap with a reason.

## A decode within the decode

One BUS-group neighbour we passed over has an inside of its own.
`SetConfig` carries a `BitVector 6`, and back in the [dispatch post][exec]
its arm ran that payload through `decodeConfig` and trapped on a `Left`.
That function lives in `Tamal.Config`, and it is the [fold-in this post
promised][shape] --- because decoding a configuration payload is the same
act as decoding an instruction, one level down.

The shape is identical: a total function from a bitfield to `Either` an
error or a typed value.

```haskell
decodeConfig :: BitVector 6 -> Either ConfigError Config
decodeConfig p =
  case (role, io, sck) of
    (0b0, 0b00, 0b00) -> Right (Config Controller X1 Sck20 alertSrc)
    (0b1, _, _)         -> Left UnsupportedRole
    (_, io', _) | io' /= 0b00 -> Left UnsupportedIoMode
    _                   -> Left UnsupportedSck
 where
  (role, io, sck, alert) = bitCoerce p :: (BitVector 1, BitVector 2, BitVector 2, BitVector 1)
  alertSrc = if alert == 0 then AlertPin else AlertIo1
```

Six bits come apart by `bitCoerce` --- the same field-split, smaller --- into
a one-bit role, a two-bit I/O mode, a two-bit clock, and a one-bit alert
source. And then the striking part: only *one* combination is accepted.
`(0b0, 0b00, 0b00)` --- controller role, single I/O, 20 MHz --- decodes to a
`Config`; every other role traps `UnsupportedRole`, every other I/O mode
`UnsupportedIoMode`, every other clock `UnsupportedSck`. The values it
decodes into are a little vocabulary of their own:

```haskell
data Role        = Controller | Target
data IoMode      = X1 | X2 | X4
data Sck         = Sck20 | Sck33 | Sck50 | Sck66
data AlertSource = AlertPin | AlertIo1
data Config = Config
  { cfgRole :: Role, cfgIoMode :: IoMode, cfgSck :: Sck, cfgAlertSource :: AlertSource }
```

The types describe the *whole* eSPI configuration space --- `Target`,
`X2`/`X4`, the faster clocks --- but `decodeConfig` accepts only the corner
of it v1 actually implements, and names the rest as errors rather than
pretending they work. This is the [introduction's][intro] honesty line
turned into code: the [shape post's][shape] `powerUpDefault` --- controller,
x1, 20 MHz, alert pin --- is the one config that decodes, and the alert
source is the single bit v1 lets you actually choose. The reserved-zero
discipline of the ISA has a cousin here: a config the engine cannot honour
is refused at decode, so `SET_CONFIG` can never quietly leave the engine in
a mode its gates do not implement. It traps instead, [reason two][exec].

## The tests

A module whose whole job is a bijection over a word space is a module you
can test *completely*, and `Test.Isa` does, from both directions at once.
The round trips are one property per group:

```haskell
testProperty "BUS: decode . encode == Right" $ property $ do
  i <- forAll genBusInstr
  decode (encode i) === Right i
```

[Hedgehog][hedgehog] draws a random instruction, encodes it, decodes the
result, and demands the original back --- the first law, checked across
hundreds of generated instructions per group. But the property that earns
its keep is the second one, and it runs over the *whole word space*, not
the image of the encoder:

```haskell
testProperty "any 32-bit word: decode is canonical or traps" $ property $ do
  w <- forAll genWord
  case decode w of
    Right i -> encode i === w
    Left _  -> success
```

Read what this checks. Draw *any* thirty-two-bit word --- valid or
garbage, any group, any junk in any reserved field. If `decode` rejects it,
fine, `success`. But if `decode` *accepts* it, the accepted instruction
must re-encode to the very word we started from. That is canonicity stated
as a test: there is no word decode accepts that is not the encoder's own
output for that instruction. A single too-lax reserved-field check --- one
`only` that forgot a field --- and Hedgehog would find a word that decodes
to an instruction whose encoding differs, and the property would go red
with the counterexample in hand. It is the reserved zero, guarded from the
outside.

Two `HUnit` cases pin the reserved discipline to specific words:

```haskell
testCase "reserved non-zero field traps (CS_ASSERT with junk imm)" $
  decode (busWord 0b00 0x0 + 1) @?= Left ReservedFieldNonZero
testCase "reserved SHIFT op (0b11) traps" $
  decode (encode (Shift 0 0 0b11 0)) @?= Left ReservedFieldNonZero
```

The first takes a clean `CS_ASSERT` word and sets one reserved bit; it must
trap. The second is sharper: `encode` never validates, so it will happily
build a `SHIFT` word with the reserved op `0b11` --- and the tightened
decoder must reject what the encoder was willing to write. That gap between
a permissive encoder and a strict decoder is the safe direction to err:
the engine only ever runs `decode`, so the decoder is the gate, and the
test proves the gate is shut.

`Test.Config` does the same for the fold-in, five cases wide: the v1
default decodes, the in-band alert source decodes, and target role, dual
I/O, and 33 MHz each trap with their own error. The whole legal surface
and three witnesses that the illegal surface is refused.

## What we read

The first box, opened. The instruction set is a thirty-two-bit word in six
fields, a data type with one constructor per opcode across three groups and
a reserved fourth, and two functions that are inverses over exactly the
legal words. `encode` is the easy direction --- an `Instr` is valid by
construction, so encoding only places operands and zeroes the reserved
fields, `bitCoerce` doing the packing the way it packed the [trace
records][bus], the widest immediate carved from the fields its opcodes left
spare. `decode` is the hard one, dispatching on the group and accepting a
word *only* when every reserved field is zero --- and that strictness buys
the round trip, canonicity, and a totality that gives every one of four
billion words a defined home, which is the [trap][exec] seen from the
inside. The folded `Config` is the same function one level down, six bits
to a role and a mode and a clock, accepting the single corner v1
implements and naming the rest as errors. And the tests check the bijection
across the whole word space, not a sample of it.

We opened `decode` because it was the engine's first act on any word. Its
last act on that word --- once decoded, the very next thing `execInstr`
did --- was to read the registers the operands named: `readReg (regs s)
(operandRs1 i)`. Those register selectors were `Reg`s, the five-bit fields
we met at the top of this post, still only *names*. Next we open the thing
they name: `Tamal.RegFile`, the sixteen registers, the hardwired zero, and
the two ports the datapath read and wrote through the whole time we were
holding it shut.

[^unimpl]: `OpcodeUnimplemented` is the one `DecodeError` the current
decoders never actually return --- they reject with `IllegalOpcode` (an
unknown sub-opcode, or the reserved group) or `ReservedFieldNonZero` (a
dirty reserved field). It exists in the type for the case of an opcode that
is *recognised* but not yet built, so that a future partial implementation
can distinguish "this is not an instruction" from "this is an instruction I
have not finished." Declaring the variant now costs nothing and reserves
the vocabulary; the engine treats all three the same way, as a trap.

[^bitcoerce]: `bitCoerce` is Clash's total, zero-cost reinterpretation of a
value as another type of the *same bit width* --- it is `pack` followed by
`unpack`, and it lowers to no gates at all, only a relabelling of wires.
The safety is entirely in the widths: `joinW :: Fields -> BitVector 32`
typechecks only because the six fields sum to thirty-two, so a field-layout
error is a compile error, not a silently truncated word. It is the same
"widths live in the type" guarantee the [primer] opened with, doing the
load-bearing work in a format definition --- the reason the ISA can be
described as tuples of `BitVector`s and trusted to pack correctly.

[^truthtable]: The hardware reading matters because `decode` is not a
program the engine runs step by step --- it is a combinational function the
`Fetch`-to-`Exec` boundary evaluates in a single cycle. Clash turns the
nested `case`s into a tree of multiplexers and comparators: the group
selects a per-group decoder, the sub selects an arm, the reserved-field
tests become AND-reductions over the unused bits. There is no loop and no
sequencing, because a truth table has none; the whole of `decode` is one
wide slab of logic that turns thirty-two input wires into a decoded
instruction and a valid bit, every cycle, for whatever word `Fetch`
happened to hand it.
