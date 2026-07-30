+++
title = "Tamal: One Instruction, One Cycle"
date = 2026-08-03T09:00:00
draft = true
description = "Standing inside Exec, the engine's one working phase, and watching a decoded word become an action: how stepExec is two lines --- decode, then dispatch or trap --- and why a decode error is the first of five trap reasons, driving the pins safe on its way to Halted; how execInstr is a case wider than any in the series and yet collapses to a handful of handlers, thirteen DATA instructions funnelling through one dataWb that calls the still-shut ALU; the operand-selector tables that say which registers each instruction reads, and why immediates need none; RDSR, the single status read the engine answers itself, handing back the CRC residue the CRC post built; the four branch arms and the PC arithmetic the engine owns, an eleven-bit signed offset truncated to ten and added modulo the program counter so a backwards jump falls out of wraparound; the pin and config pokes that latch one field and step on, sharing a handler that is not really about pins; and the instructions that spend their one Exec cycle not finishing but arming --- loading a shifter, setting a beat count, recording what they owe in pending --- and handing off to the phases the next post opens."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "engine"]
+++

The [last post][shape] drew the map and left every road on it unwalked.
We read the engine's shape --- the pure `step`, the eight phases, the
seventeen-field state it threads --- and then stopped at the one phase
where the work actually happens: `Exec`, with the instruction word from
memory finally valid in hand, decoded by nothing yet. This post walks in.

`Exec` is the hub. Every instruction the engine runs passes through it
exactly once, and what happens in that one cycle is the whole of the
machine's behaviour above the wire. Most instructions *finish* here ---
compute a value, take a branch, poke a pin --- and hand the machine back
to `Fetch` for the next word. A few use their one cycle in `Exec` not to
finish but to *arm themselves* and hand off to a longer phase. Either
way, the cycle is the same shape: decode the word, and dispatch on what
it turned out to be.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Haskell]: https://www.haskell.org/
[Clash]: https://clash-lang.org
[primer]: https://balbi.sh/posts/tamal-haskell-primer/
[crc]: https://balbi.sh/posts/tamal-crc/
[mem]: https://balbi.sh/posts/tamal-mem/
[shape]: https://balbi.sh/posts/tamal-engine-shape/
[intro]: https://balbi.sh/posts/tamal-introducing/
[hedgehog]: https://hedgehog.qa

## Decode, then dispatch

The phase helper `step` hands the cycle to is `stepExec`, and it is two
lines:

```haskell
stepExec :: State -> BusIn -> (State, BusOut, Maybe Ring)
stepExec s inp = case decode (instrWord inp) of
  Left _ -> haltWith True 1 0 (safePins s) -- decode error -> reason 1
  Right i -> execInstr i s inp
```

Everything the engine does with a word begins with `decode (instrWord
inp)`. `decode` is the first of the [shut boxes][shape] we reach for ---
it lives in `Tamal.Isa`, it turns the raw 32 bits the memory handed back
into a typed `Instr`, and it is the whole subject of a later post. Here
we treat it exactly as its type asks us to: it returns an `Either`, and
the two sides are the fork this function is built on.

`Left` is a **decode error**: the word was not a legal instruction ---
a reserved opcode, a bad field, a bit pattern the ISA refuses. There is
no "ignore it and move on"; an engine that silently skips a word it
cannot read is an engine that turns a corrupted program into undefined
behaviour. So a decode error *traps*: `haltWith True 1 0 (safePins s)`.
We will read `haltWith` in full in the next post --- it writes the
terminator record that ends a trace --- but its arguments already tell
the story. `True` is the trap flag; `1` is the **reason**, and it is the
first of five the engine can give[^reasons]; `safePins s` drives `CS#`,
`RESET#`, `SCK`, and the lanes back to their safe idle before the machine
stops, so a program that dies mid-transaction does not leave the bus
wedged. A trap is a `HALT` with a cause and clean pins.

`Right i` is the good path: a decoded `Instr`, handed to `execInstr`
along with the state and this cycle's inputs. That function is the rest
of the post.

## A case as wide as the ISA

`execInstr` dispatches on the decoded instruction, and its `case` is the
widest in the series --- one arm per instruction the ISA defines. Here is
its shape, the DATA block collapsed to save your scrolling:

```haskell
execInstr :: Instr -> State -> BusIn -> (State, BusOut, Maybe Ring)
execInstr i s inp = case i of
  LoadImm rd _ -> dataWb rd
  Lui rd _ -> dataWb rd
  Mov rd _ -> dataWb rd
  Add rd _ _ -> dataWb rd
  -- … Addi, Sub, And_, Andi, Or_, Ori, Xor_, Xori, Shift: all dataWb rd
  Halt st -> haltWith False 0 st s
  Rdsr rd srn -> {- status read or trap -}
  Beq a b off -> branch Br.Beq a b off
  -- … Bne, Bltu, Bgeu
  CsAssert -> pinOp (s{csN = 0})
  -- … CsDeassert, RstAssert, RstDeassert, CrcReset, SetConfig, GetAlert
  PutByteImm b -> startPut b 8
  -- … the other PUT/GET forms, TarImm, TarReg
  Mark lbl a -> {- arm TraceEmit -}
  WaitOn rd _ timeout -> {- arm WaitAlert -}
```

The width is real --- there are more than thirty constructors --- but the
*handlers* are few, and that gap is the first thing worth seeing. A
[total case][primer] over `Instr` means every instruction the ISA can
produce has a defined action here, checked by the compiler: leave one out
and Clash refuses to build. But thirteen of those arms say the same three
words, `dataWb rd`, and four of them say `branch`, and a cluster of them
say `pinOp`. The `case` is wide because the instruction set is wide; the
behaviour behind it collapses into a handful of shapes. Read the shapes,
and you have read the dispatch.

There are exactly three destinations, and they are the three exits from
`Exec` the [shape post][shape]'s lifecycle diagram drew without opening:

<figure class="exdisp-fig" style="margin:2rem 0">
<svg class="exdisp" viewBox="0 0 760 372" role="img" aria-labelledby="exdisp-t exdisp-d" xmlns="http://www.w3.org/2000/svg">
<title id="exdisp-t">How Exec dispatches a decoded instruction to three destinations</title>
<desc id="exdisp-d">The instruction word enters a decode box on the left. Decode either fails, which traps, or yields an Instr that execInstr dispatches to one of three destinations. The top destination, drawn in the accent colour, is single-cycle and returns to Fetch: the thirteen DATA instructions, RDSR, the four branches, and the CS, RST, CRC_RESET, SET_CONFIG and GET_ALERT pokes. The middle destination is multi-cycle and hands off to the BusBeat, TraceEmit and WaitAlert phases opened in the next post: PUT, GET, TAR, MARK and WAIT_ON. The bottom destination stops the machine at Halted: HALT, a decode error, and the config or rdsr traps.</desc>
<style>
.exdisp{max-width:760px;width:100%;height:auto;display:block;margin:0 auto}
.exdisp .box{fill:var(--bg-dim);stroke:var(--fg-main);stroke-width:2}
.exdisp .boxA{fill:var(--bg-dim);stroke:var(--accent);stroke-width:2.5}
.exdisp .wire{stroke:var(--fg-main);stroke-width:2;fill:none}
.exdisp .accw{stroke:var(--accent);stroke-width:2.5;fill:none}
.exdisp .kw{fill:var(--fg-main);font-family:var(--mono);font-size:14px}
.exdisp .ti{fill:var(--fg-main);font-family:var(--sans);font-size:13px}
.exdisp .tiA{fill:var(--accent);font-family:var(--sans);font-size:13px}
.exdisp .mem{fill:var(--fg-dim);font-family:var(--mono);font-size:10.5px}
.exdisp .sig{fill:var(--fg-dim);font-family:var(--mono);font-size:11.5px}
.exdisp .ah{fill:var(--fg-main)}
.exdisp .aha{fill:var(--accent)}
</style>
<defs>
<marker id="ex-a" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="ah"/></marker>
<marker id="ex-aa" markerWidth="9" markerHeight="7" refX="7" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" class="aha"/></marker>
</defs>
<!-- input + decode -->
<text class="sig" x="16" y="192" text-anchor="start">instrWord</text>
<line class="wire" x1="86" y1="188" x2="98" y2="188" marker-end="url(#ex-a)"/>
<rect class="boxA" x="100" y="166" width="92" height="44" rx="6"/>
<text class="kw" x="146" y="192" text-anchor="middle">decode</text>
<!-- fan arrows -->
<path class="accw" d="M192,180 C280,150 330,96 424,86" marker-end="url(#ex-aa)"/>
<path class="wire" d="M192,192 C300,192 320,192 424,192" marker-end="url(#ex-a)"/>
<path class="wire" d="M192,200 C280,230 330,290 424,300" marker-end="url(#ex-a)"/>
<!-- top: single-cycle -> Fetch -->
<rect class="boxA" x="426" y="52" width="318" height="68" rx="8"/>
<text class="tiA" x="440" y="74" text-anchor="start">single-cycle → Fetch</text>
<text class="mem" x="440" y="94" text-anchor="start">13× DATA · RDSR · BEQ BNE BLTU BGEU</text>
<text class="mem" x="440" y="110" text-anchor="start">CS/RST · CRC_RESET · SET_CONFIG · GET_ALERT</text>
<!-- mid: multi-cycle -> part 3 -->
<rect class="box" x="426" y="160" width="318" height="60" rx="8"/>
<text class="ti" x="440" y="182" text-anchor="start">multi-cycle → BusBeat / TraceEmit / WaitAlert</text>
<text class="mem" x="440" y="202" text-anchor="start">PUT/GET/TAR · MARK · WAIT_ON</text>
<!-- bot: stop -> Halted -->
<rect class="box" x="426" y="260" width="318" height="56" rx="8"/>
<text class="ti" x="440" y="282" text-anchor="start">stop → Halted</text>
<text class="mem" x="440" y="302" text-anchor="start">HALT · decode error · config / rdsr trap</text>
</svg>
<figcaption style="text-align:center;color:var(--fg-dim);font-size:.9rem;margin-top:.85rem;font-family:var(--sans)">One <code>Exec</code> cycle, zoomed in from the <a href="https://balbi.sh/posts/tamal-engine-shape/">shape post</a>'s lifecycle. <code>decode</code> turns the fetched word into an <code>Instr</code> (or fails, and traps). <code>execInstr</code> sends it to one of three destinations: the accent path is the single-cycle majority that computes or jumps or pokes and returns to <code>Fetch</code>; the middle path arms a multi-cycle transfer and hands off to the phases the next post opens; the bottom path stops the machine. The <code>case</code> is wide, but every arm lands in one of these three.</figcaption>
</figure>

We walk the accent path first, because it is where nearly every arm ends
up.

## Thirteen instructions, one handler

The DATA group is the engine's compute: load an immediate, move a
register, add, subtract, the three bitwise operations and their immediate
forms, the shift. Thirteen instructions --- and all thirteen dispatch to
the same three words:

```haskell
  LoadImm rd _ -> dataWb rd
  Lui rd _ -> dataWb rd
  Mov rd _ -> dataWb rd
  Add rd _ _ -> dataWb rd
  Addi rd _ _ -> dataWb rd
  Sub rd _ _ -> dataWb rd
  And_ rd _ _ -> dataWb rd
  Andi rd _ _ -> dataWb rd
  Or_ rd _ _ -> dataWb rd
  Ori rd _ _ -> dataWb rd
  Xor_ rd _ _ -> dataWb rd
  Xori rd _ _ -> dataWb rd
  Shift rd _ _ _ -> dataWb rd
```

Every arm names its destination register `rd` and throws the rest of its
fields away with underscores, because the fields do not matter *here* ---
they matter to the thing that computes the value. The arm's whole job is
to say "this instruction writes a register, and the register is `rd`."
The computing is `dataWb`, one of the helpers in the `where` block:

```haskell
  rs1v = readReg (regs s) (operandRs1 i)
  rs2v = readReg (regs s) (operandRs2 i)
  dataWb rd =
    let s' = (advance s){regs = writeReg (regs s) rd (dataResult i rs1v rs2v)}
     in (s', busOut s', Nothing)
```

Read `dataWb` inside out. `dataResult i rs1v rs2v` is the value ---
computed by `dataResult`, another [shut box][shape], the ALU that a later
post opens. Notice *how* it is called: it is handed the whole instruction
`i` and two operand values, and it works out the rest --- which operation,
which immediate --- from `i` itself. That is why all thirteen arms could
collapse into one handler: they do not each need to pick out their own
operation, because `dataResult` reads the operation off the instruction.
The arm supplies `rd`; the ALU supplies everything else.

`writeReg (regs s) rd (…)` stores that value into the register file ---
`writeReg` is the [register file][shape]'s, a box for a later post, and
it quietly enforces one rule we will lean on: a write to `x0` is
discarded, because `x0` is hardwired zero. `advance s` sets the phase back
to `Fetch` and steps the program counter to `pc + 1`. And the output is
`(s', busOut s', Nothing)` --- next state, its projected pins, and
`Nothing`, because a register write leaves no trace record. One cycle,
one register, back to `Fetch`.

## Which registers, though

Two lines in that `where` block did the reading: `rs1v` and `rs2v`, the
values of the two source registers, each `readReg (regs s)` applied to a
register *number* that comes from `operandRs1 i` and `operandRs2 i`. Those
two selectors are small tables, and they are pure engine logic --- not a
leaf, nothing shut, so we read them here:

```haskell
operandRs1 :: Instr -> Reg
operandRs1 = \case
  Mov _ a -> a
  Add _ a _ -> a
  -- … every instruction with a first source register returns it …
  Beq a _ _ -> a
  PutByteReg a -> a
  Mark _ a -> a
  _ -> 0
```

```haskell
operandRs2 :: Instr -> Reg
operandRs2 = \case
  Add _ _ b -> b
  Sub _ _ b -> b
  -- … only the instructions with a second source register …
  Bgeu _ b _ -> b
  _ -> 0
```

Each is a `\case` --- a bare `case` on the function's one argument --- that
picks the relevant field out of the constructors that have one, and falls
through to `0` for everything else. `operandRs1` returns the first source
register of any instruction that has one; `operandRs2` the second, which
far fewer instructions have. The catch-all `_ -> 0` is doing real work:
`0` is `x0`, the register hardwired to zero, so an instruction with no
second operand "reads `x0`" and gets zero --- a read that is always safe,
always defined, and costs nothing, because `x0` is not a register you can
get wrong. An immediate instruction like `Addi` names one source and lets
the other resolve to `x0`; `LoadImm` names neither and reads `x0` twice.
The value it does not use is a harmless zero, and the ALU ignores it.

This is the [RISC-V flavour][intro] the engine borrows showing through:
sources named `rs1` and `rs2`, a zero register that makes "no operand"
and "the zero operand" the same thing, so the datapath never needs a
special case for an absent argument.

## The one status the engine answers

Most of what a program might ask about lives in the host, not the engine
--- but there is exactly one status read the engine answers itself:

```haskell
  Rdsr rd srn
    | srn == 0 ->
        let s' = (advance s){regs = writeReg (regs s) rd (zeroExtend (rxCrc s))}
         in (s', busOut s', Nothing)
    | otherwise -> haltWith True 3 0 (safePins s) -- reserved sr# -> reason 3
```

`RDSR` --- read status register --- names a status register number `srn`,
and the engine defines exactly one: number `0`, the running receive
CRC-8 residue. When `srn == 0` the engine writes `zeroExtend (rxCrc s)`
into `rd` --- the eight-bit residue, widened to the register's
thirty-two --- and advances. Any other `srn` is a register the engine
does not have, and reading it traps with reason `3`.

That residue is not new; it is the [CRC block][crc] cashed in. `rxCrc` is
the accumulator that post built, folded over every byte the engine reads
off the bus, and the [introduction][intro]'s `peripheral_io_read.s` ended
its response phase by reading it --- `rdsr t2, CRC` --- and branching on
whether it had driven to zero. This arm is the other end of that program
line: the one place the engine reaches into its own CRC state and hands
it to a register a program can test. The residue law the CRC post proved
by algebra is the check a real eSPI program runs, and `RDSR sr0` is how
it runs it.

## Branches, and arithmetic the engine owns

Four arms are conditional branches, and all four defer to one helper:

```haskell
  Beq a b off -> branch Br.Beq a b off
  Bne a b off -> branch Br.Bne a b off
  Bltu a b off -> branch Br.Bltu a b off
  Bgeu a b off -> branch Br.Bgeu a b off
```

`branch` is where the engine does arithmetic that is genuinely its own
--- not delegated to a leaf, but computed right here, because it is about
the program counter and nothing else knows the program counter:

```haskell
  branch op a b off =
    let taken = Br.branchTaken op (readReg (regs s) a) (readReg (regs s) b)
        -- offset is 11-bit signed; PC is AW=10-bit. Take the low AW bits;
        -- pc + off ≡ pc + (off mod 2^AW) (mod 2^AW)
        offAw = unpack (truncateB off) :: Unsigned AW
        s'
          | taken = s{phase = Fetch, pc = pc s + offAw}
          | otherwise = advance s
     in (s', busOut s', Nothing)
```

The condition, `taken`, is `Br.branchTaken op …` --- the comparator from
`Tamal.Branch`, imported qualified as `Br`, a [shut box][shape] for the
compute post. It takes the comparison (`Beq`, `Bne`, and the unsigned
`Bltu`/`Bgeu`) and the two register values and answers yes or no. Give
that to the engine and the rest is the program counter.

The offset arithmetic repays a close look, because it is a small, exact
piece of hardware reasoning. The branch offset `off` is an eleven-bit
*signed* number --- it can reach forwards or backwards. The program
counter is ten bits (`AW`), addressing the thousand-and-twenty-four-word
[instruction store][mem]. So the offset is one bit wider than the counter
it adjusts, and the code reconciles them in one move: `truncateB off`
keeps the low ten bits, `unpack … :: Unsigned AW` reads them as an
*unsigned* ten-bit number, and `pc s + offAw` adds. That looks like it
throws the sign away --- and it does, and it is still correct, because the
addition is modular. A backwards jump of four is the signed offset `-4`,
which in eleven bits is `0x7FC`; its low ten bits are `0x3FC`, which is
`1020` unsigned; and `pc + 1020` modulo `1024` is `pc - 4`. The
two's-complement wraparound that makes signed and unsigned addition the
same operation --- the [CRC post][crc]'s "add and subtract are one thing"
has a cousin here --- means the engine needs no signed adder and no
special case for a backwards branch. It adds the low bits and lets the
tenth-bit overflow fall off, and the arithmetic comes out right.[^offset]

The two outcomes differ in one field. Taken: `s{phase = Fetch, pc = pc s
+ offAw}` --- jump, by pointing the counter at the target and going
straight to `Fetch`. Not taken: `advance s` --- the ordinary `pc + 1`. The
offset is measured from the branch's own address, because `pc` still
holds that address in `Exec`; `Fetch` spent a cycle but never touched it.
And the unconditional jump the assembler offers, `j off`, is not a new
instruction at all --- it is `beq x0, x0, off`, a branch whose condition
compares `x0` to itself and is therefore always taken. The zero register
earns its keep twice: once as the absent operand, once as the always-true
comparison.

## Pokes: pins and config

A cluster of arms change one piece of state and step on. They share a
handler, and the handler's name is a small, honest lie:

```haskell
  CsAssert -> pinOp (s{csN = 0})
  CsDeassert -> pinOp (s{csN = 1, lanes = hiZ})
  RstAssert -> pinOp (s{rstN = 0})
  RstDeassert -> pinOp (s{rstN = 1})
  CrcReset -> pinOp (s{rxCrc = 0})
  SetConfig p -> case decodeConfig p of
    Right c -> pinOp (s{cfg = c})
    Left _ -> haltWith True 2 0 (safePins s) -- unsupported config -> reason 2
```

```haskell
pinOp :: State -> (State, BusOut, Maybe Ring)
pinOp s = let s' = advance s in (s', busOut s', Nothing)
```

`pinOp` is the single-cycle latch: take a state that already has one
field changed, `advance` it to the next instruction, project the pins,
write no trace. Read the arms as "change this, then step." `CsAssert`
pulls `CS#` low to open a frame; `CsDeassert` raises it *and* releases
the lanes to `hiZ`, ending the frame cleanly; `RstAssert`/`RstDeassert`
drive the `RESET#` sideband; `CrcReset` clears the receive residue so the
next `RDSR` starts fresh.

The name is a lie because `CrcReset` and `SetConfig` are not pins ---
`pinOp` is really "the shape of a single-cycle state poke," and it just
happened to be named for its most common user. `SetConfig` is the one
with a fork: it runs its immediate through `decodeConfig` --- a [shut
box][shape], and the one whose contents fold into the ISA post, since
decoding a configuration field is the same act as decoding an instruction
--- and either latches the new `cfg` or, on a configuration the engine
does not support, traps with reason `2`. The two traps we have now met in
passing, config and `RDSR`, are reasons `2` and `3`; the decode error was
`1`; the fourth and fifth wait for the parts that raise them.

One arm in this neighbourhood is not a `pinOp`, because it does not just
change state --- it reads the world and writes a register:

```haskell
  GetAlert rd ->
    let b =
          if cfgAlertSource (cfg s) == AlertPin
            then alertIn inp
            else ioIn inp !! (1 :: Index 4)
        s' = (advance s){regs = writeReg (regs s) rd (zeroExtend (pack b))}
     in (s', busOut s', Nothing)
```

`GET_ALERT` samples the alert line and drops it into `rd`. Which line
"the alert" is depends on configuration: an eSPI target can raise its
alert on a dedicated `ALERT#` pin or in-band on `IO[1]`, and
`cfgAlertSource (cfg s)` picks between `alertIn` (the synchronised pin
from this cycle's `BusIn`) and `ioIn inp !! 1` (lane one of the sampled
IO). Either way the sampled bit is zero-extended into the register. It is
a single-cycle read --- no clock toggles, no beat --- because
electrically it *is* just a sample, which is exactly why it lives here in
`Exec` among the one-cycle arms rather than out in the bus phase with the
transfers.

## The ones that don't finish here

Everything so far has finished inside its one `Exec` cycle and returned
to `Fetch`. The remaining arms are the transfers and the trace and the
wait --- the instructions the [shape post][shape] drew detouring through
`BusBeat`, `TraceEmit`, and `WaitAlert` --- and their arms do something
subtly different. They spend their `Exec` cycle *arming* the state and
setting the next phase, then hand off. The finishing is the next post's
subject; the *arming* is this one's, because it happens right here.

The `PUT` and `GET` families are the clearest pair. Putting a byte on the
wire:

```haskell
  PutByteImm b -> startPut b 8
  PutByteReg a -> startPut (truncateB (readReg (regs s) a)) 8
```

```haskell
  startPut byte total =
    let s' =
          s
            { phase = BusBeat
            , busPhase = 0
            , beatIx = 0
            , beatTot = total
            , shifter = byte
            , pending = PendNone
            , lanes = serializeX1 byte !! (0 :: Unsigned 4)
            }
     in (s', busOut s', Nothing)
```

Read what `startPut` writes into the state, because every field is a
piece of setup the [shape post][shape] named and left idle. `phase =
BusBeat` points the machine at the bus phase. `busPhase`, `beatIx` reset
the SCK counter and the bit counter to the start of a transfer.
`beatTot = total` records how many bits --- eight for a byte. `shifter =
byte` loads the bits to send. And `lanes = serializeX1 byte !! 0` drives
the *first* bit onto the lanes immediately, using `serializeX1`, the
[serialiser][shape] that is a shut box for the last post in this arc.
`startPut` does not run the transfer; it loads the gun and points the
machine at the phase that pulls the trigger, and then it is done, in one
cycle, like everything else in `Exec`.

Getting a byte is the mirror image, and it uses the `pending` field the
[shape post][shape] introduced as the machine's "deferred work" slot:

```haskell
  startGet rd total crc =
    let s' =
          s
            { phase = BusBeat
            -- … busPhase, beatIx, beatTot as before …
            , shifter = 0
            , lanes = hiZ
            , pending = PendGet rd total crc
            }
     in (s', busOut s', Nothing)
```

A `GET` cannot write its result now --- the bits have not been sampled
yet. So it records what it will owe when they have: `pending = PendGet rd
total crc` says "when this transfer completes, write the result into
`rd`, it is `total` bits wide, and `crc` decides whether to fold it into
the residue." The register the [shape post][shape] saw in `PendGet`'s
first field is filled in right here, by the instruction that will be paid
out cycles later. `lanes = hiZ` releases the bus so the other side can
drive it. The rest --- `TAR`, which drives a turnaround; `MARK`, which
writes the first word of a two-word trace record and arms `TraceEmit` for
the second; `WAIT_ON`, which loads `waitTimer` and enters `WaitAlert` ---
are the same move in different clothes: set the phase, arm the scratch,
record what is pending, hand off. Their completions, and the bus timing
that drives them, are the next post.

## What we read

`Exec`, walked end to end. It begins by asking `decode` to turn the
fetched word into an `Instr`, and forks on the answer: a `Left` traps
with reason one and safe pins, a `Right` goes to a `case` as wide as the
instruction set. That width is a surface; underneath it the arms collapse
to three destinations. Thirteen DATA instructions share one `dataWb` that
hands the whole instruction to the still-shut ALU and writes one
register; the operand-selector tables say which registers each reads, and
`x0` makes "no operand" free. `RDSR` answers the one status the engine
owns, the [CRC][crc] residue. The four branches defer their condition to
the shut comparator but keep the PC arithmetic for themselves --- an
eleven-bit signed offset added modulo a ten-bit counter, backwards jumps
falling out of wraparound, `j` revealed as `beq x0, x0`. The pin and
config pokes latch one field through a handler misnamed for its commonest
user, and `GET_ALERT` samples a line the configuration chooses. And the
transfers, the trace, and the wait spend their one cycle here not
finishing but *arming* --- loading a shifter, counting out beats,
recording in `pending` what they will owe --- and pointing the machine at
a longer phase.

We have now used, without opening, every leaf the engine composes:
`decode`, `dataResult`, `branchTaken`, `readReg` and `writeReg`,
`decodeConfig`, and `serializeX1`. That was the bet of reading [outside
in][shape], and it has paid: you can follow every instruction the engine
runs while holding those seven as one-line promises. The promises come
due later in the arc.

But first, the handoff. Four arms pointed the machine at `BusBeat`,
`TraceEmit`, and `WaitAlert` and stopped mid-gesture --- a shifter loaded,
a beat count set, a write pending, a phase changed --- and the cycle
ended there. Next we follow them across that seam: the bus micro-FSM that
divides the fabric clock into an `SCK`, walks the beats a `PUT` or `GET`
counts out, samples what comes back and folds it into the residue and the
trace ring, and finally writes the terminator records --- `HALT` and the
traps we kept meeting --- that end a run. The engine has decided what to
do; next it does it, on the wire, one quarter-clock at a time.

[^reasons]: The engine gives five trap reasons, and this post meets three
of them where they are raised: `1` for a decode error (here in
`stepExec`), `2` for an unsupported configuration (`SetConfig`), and `3`
for a reserved status register (`RDSR`). The remaining two --- `4` for an
illegal or reserved-group instruction, and the machinery that folds the
reason, a trap flag, and the sticky overflow bit into a single terminator
word --- belong to the next post, where `haltWith` is read in full. Every
trap is a `HALT` that also says *why*, and drives the pins safe on the way
down, so a program that dies mid-frame cannot leave `CS#` or the lanes
stuck driving the bus.

[^offset]: The one thing the truncation *does* cost is range: an
eleven-bit offset can name distances a ten-bit counter cannot, so the top
bit of the offset is redundant with the sign that the low ten bits
already imply modulo `1024`. In practice the assembler only emits offsets
that resolve to a real target inside the thousand-and-twenty-four-word
program, so the reachable set is the whole store either way, and the
wraparound is a correctness argument, not a reachability limit. It is the
same reason a byte-addressed CPU can use a signed branch displacement
narrower than its full address space: you never branch further than the
program is long.
