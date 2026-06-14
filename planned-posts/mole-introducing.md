+++
title = "Mole: where it lives, what it looks like"
date = 2026-08-03T09:00:00
description = "A tour of the mole repository: the crates that compile and load programs, the SpinalHDL engine that runs them, the result ring that comes back, and a smallest-possible program that exercises the round trip."
[taxonomies]
tags = ["embedded", "rust", "fpga", "spinalhdl", "mole", "testing", "i2c"]
+++

The [last post](/posts/mole-why/) introduced mole at the sketch
level — two layers, a bit-cycle engine, a host-side SDK that
compiles down to bytecode, and a position on the I²C
failure-path-testing problem that motivated the whole thing. That
post stopped at the architecture diagram. This one puts the code
in front of you.

The repo is [github.com/felipebalbi/mole](https://github.com/felipebalbi/mole),
public and dual-licensed. The live engine is v0.2 — 26 opcodes
across three groups, 32-bit fixed-width instructions, a 5-stage
pipeline (`EnginePipeline`) — running on REVISION 0.1.1 silicon
on an iCEbreaker UP5K. The v0 engine (15 opcodes, 16-bit
instructions) is retired and stashed under `fpga/Mole/src/attic/`;
v0.2 bytecode is not cross-compatible with it.

What follows is a tour. The two deep-dive posts later in the
series take the engine apart in SpinalHDL; this one stays at the
level of "here is what is in the repo, here is what a program
looks like, here is what happens when you press the button." Post
6 closes the series by reproducing the I²C `Restarted(0)` failure
from post 1 as a real, runnable mole program.

## What's in the repo

The layout is intentionally small. Each top-level directory does
one thing.

`mole-asm/` is the bytecode compiler: a Rust library that takes
`.moleasm` source and emits the 32-bit instruction stream the
engine executes. `mole-asm-cli/` is the thin
clap-and-color-eyre wrapper that gives you the `mole-asm` binary
on your `$PATH`.

`mole-loader/` and `mole-loader-cli/` are the host-side runtime
counterpart. The loader opens a serial port to the iCEbreaker,
ships the compiled bytecode over UART (framed bytes, mandatory
hardware RTS/CTS, CRC-16/XMODEM trailer), waits for the engine to
halt, drains the result ring back over the same UART, and decodes
what came back into typed records the host can pattern-match on.

`mole-abi/` is the small crate of wire-format constants and
bytecode definitions shared across the host crates so the
encoder, the loader, and any future tool agree on the same byte
layouts.

`fpga/Mole/` is the SpinalHDL project: the bit-cycle engine
(`EnginePipeline.scala`), the UART blocks, the SPRAM controller,
the result-ring drainer, the top-level FSM that wires them
together (`MoleTop.scala`), and the per-board scaffolding for the
iCEbreaker UP5K. The retired v0 engine lives under `src/attic/`
for historical reference; nothing in the live design links it.

`book/` is the mdBook tutorial. It follows a standard tutorial /
reference split: Quickstart, Mental Model, Syntax, Opcodes,
Bounded Loops, Target Role, Worked Examples, Patterns on the
tutorial side; Errors, Reference Tables, Glossary on the
reference side. `docs/MOLE-0.2-SPEC.md` is the normative ISA
spec — the book teaches the language, the spec defines the
bytes, and when they disagree the spec wins.

The license is standard Rust-ecosystem dual MIT + Apache-2.0.

The host tools are one Cargo workspace at the repo root. The
forthcoming Pico-side firmware (a roadmap item, not present
today) will live under `firmware/` as its own `no_std` workspace.
They will stay separate; nothing about the host toolchain has any
business being `#![no_std]`, and nothing in the firmware has any
business pulling in `std`.

## The shape of a program

The smallest interesting mole program is the round trip that
motivated the whole series: a boundary-aligned I²C `write_read`
of eight bytes, the shape from post 1 that the upstream stack
turned into `Restarted(0)` instead of a real bus failure. As an
I²C transaction, that's six steps:

```
START
addr 0x2a + W
write 8 bytes
Sr + 0x2a + R
read 8 bytes
STOP
```

In moleasm, none of those steps are primitives. There is no
`I2C_START` opcode, no `I2C_WRITE_BYTE` opcode, no notion of an
"address phase" anywhere in the engine. What mole has, instead,
is one instruction that emits exactly one quarter-bit-time on
SDA and SCL with independent control of each pad, and one
instruction that walks an eight-data-bit-plus-ACK pattern through
the bus at the canonical SCL timing. You build START out of the
former; you build an address byte out of the latter.

The preamble is identical for every I²C program: configure the
timing divider, declare the bus mode, declare the role.

```moleasm
.equ slow_div, 59          ; 100 kHz at the 24 MHz fabric

start:
    LOAD_TIMING   reg=0, divider=slow_div
    SET_BUS_MODE  i2c
    SET_ROLE      controller
```

That's three instructions and the engine now knows it is running
I²C at 100 kHz as a controller. The next four `EMIT_QUARTER_IMM`s
walk through the START edges — SDA falling while SCL is high,
then SCL falling — by spelling out the SDA and SCL values for
each quarter directly:

```moleasm
    ; START condition
    EMIT_QUARTER_IMM  sda=recessive scl=recessive
    EMIT_QUARTER_IMM  sda=dominant  scl=recessive
    EMIT_QUARTER_IMM  sda=dominant  scl=dominant
    EMIT_QUARTER_IMM  sda=dominant  scl=dominant
```

Then the address byte. `0x2a` shifted left with R/W=0 is `0x54`.
A single `EMIT_BYTE_IMM` clocks the eight data bits MSB-first
through the canonical SCL pattern, then opens a ninth `hiz`-SDA
slot for the target's ACK. The flag triple in the operand tells
the engine to expect a dominant ACK, set the `MISMATCH_FLAG` if
it doesn't see one, and push the sampled bit into the result
ring for the host to consume:

```moleasm
    EMIT_BYTE_IMM imm=0x54 expect=0 mask=1 capture=1
```

The data-byte loop has the same shape it would have in any
fixed-iteration assembly: load a counter into R6, emit a byte
from R7, decrement, branch back if not zero.

```moleasm
    LOAD_LOOP     8                          ; sugar for LOAD_IMM R6, 8
write_loop:
    EMIT_BYTE_REG expect=0 mask=1 capture=1  ; byte from R7, ACK validated
    DEC           R6
    BRANCH_ON     NOT_REG_ZERO, write_loop
```

The repeated START, the read direction, and the eight-byte read
loop have the same shape with different operands. STOP is
another four `EMIT_QUARTER_IMM`s. The program ends with `HALT
status=0` if everything ACKed or a different status code if a
mismatch sent the program down a branch to a different `HALT`
slot. The full program for the post-1 reproduction is post 6's
problem, not this one's.

The load-bearing observation is in those snippets. The assembly
is a thin literal mapping to the 26-opcode ISA: every line is
one opcode, every operand corresponds to bits in the instruction
word, and there is no macro layer. The only sugar in the whole
language is `JMP <label>` for `BRANCH_ON ALWAYS, <label>` and
`LOAD_LOOP n` for `LOAD_IMM R6, n`. The point of keeping Layer 0
this small is that the assembly is small enough to keep in your
head. Everything protocol-shaped — the abstraction layer that
turns "issue an I²C write_read at address 0x2a" into the
opcode-by-opcode program above — lives in the Layer 1 SDK on the
host, which is a roadmap item.

## The four moving pieces of the assembly

The 26 opcodes group into three live families plus a reserved
fourth.

**WIRE** is the ten opcodes that touch the bus: the three
`EMIT_*` pairs (`_IMM` and `_REG` for bit-, quarter-, and
byte-sized work), the `STRETCH_SCL_*` pair for holding the line
in place, and the two target-role companions `SAMPLE_BIT_ON_SCL`
and `DRIVE_BIT_ON_SCL` that slave to an external controller's
SCL when mole is running as a follower. Every WIRE opcode that
can sample SDA carries the same three-bit operand triple —
`expect`, `mask`, `capture` — which is how a program asserts what
the bus should look like and records what it actually looked
like in one instruction.

**CTRL** is eight opcodes for the control plane: `BRANCH_ON` and
`WAIT_ON` (which share a condition-code namespace covering
sticky flags, bus events, and the loop-counter `REG_ZERO` flag),
`SET_BUS_MODE`, `SET_ROLE`, `LOAD_TIMING`, `FLAG_CLEAR` for
acknowledging sticky flags between operations, `MARK` for
pushing a labelled three-word timestamped record into the result
ring, and `HALT` to stop the engine with a 5-bit status code.

**DATA** is the eight opcodes that operate on the 32-bit
register file: `LOAD_IMM`, `MOV`, `ADD_IMM`, `DEC`, the three
bitwise immediates (`AND_IMM`, `OR_IMM`, `XOR_IMM`), and
`SHIFT`. R6 is the conventional loop counter; R7 is the
canonical capture-result register that the `_REG` variants of
the WIRE opcodes read from.

**LOOP** is the fourth group's two-bit prefix, and it is fully
reserved. There is no loop opcode today. The idiom you saw above
— `LOAD_IMM R6, n`, body, `DEC R6`, `BRANCH_ON NOT_REG_ZERO` —
is the loop, and because it works with any general-purpose
register pair it gives arbitrary nesting depth out of opcodes
you already have. The reserved group exists so a future first-
class loop construct doesn't need an ISA-breaking opcode-space
shuffle to land. That's a roadmap artifact, not a commitment.

## The toolchain

Round-trip in four pieces.

**Compile.** `mole-asm` reads `.moleasm` source and emits either
`.molecode` (raw bytecode) or `.mole.bin` (the same bytecode
wrapped in the 8-byte preamble and 2-byte CRC-16/XMODEM trailer
the loader expects on the UART). Single-pass. Library plus CLI.

**Load.** `mole-loader` opens the iCEbreaker's USB-serial port at
1 Mbaud, 8N1, with mandatory hardware RTS/CTS, and streams the
framed bytes in. The loader refuses to fall back to no-flow-
control; the engine FSM has no recovery path for dropped bytes
and a silent failure is worse than a hard error. Host writes;
engine pulls via CTS.

**Run.** The top-level FSM in the FPGA is three states:
`acceptLoad`, `running`, `draining`. `acceptLoad` is the only
state in which CTS lets host bytes in. The cycle after a
CRC-valid frame's final SPRAM write retires, the FSM rising-edges
`engine.start`. There is no explicit "go" command in the wire
format — building a valid frame is the go command. The engine
fetches, executes, runs to `HALT`.

**Drain.** On `HALT` the drainer sweeps the entire ring back out
the UART, four little-endian bytes per 32-bit word, gated by the
host's RTS. The ring is a 32-bit-grained record stream: a
`REVISION` header at slot 0, a contiguous stream of `CAPTURE`
(one word) and `MARK` (three words) records, and a `HALT` word
reserved at `resultLimit` so an overflowing record stream cannot
clobber the terminator. Bytecode in, structured records out.

## Where to read next

Two pointers inside the repo. The mdBook under `book/` is the
tutorial — Quickstart through Patterns on the tutorial side,
Errors / Reference Tables / Glossary on the reference side; if
you want to learn the language top to bottom, start there.
[`docs/MOLE-0.2-SPEC.md`](https://github.com/felipebalbi/mole/blob/main/docs/MOLE-0.2-SPEC.md)
is the ISA spec — the normative bytes, the field layouts, the
ABI constants.

The next two posts in this series take the engine apart in
SpinalHDL: the loader and the top-level FSM in post 4, the
pipeline and the bit-cycle / bus-observer machinery in post 5.
Post 6 reproduces the post-1 `Restarted(0)` test as a real mole
program against a real target on the bench, and is the only post
in the series that runs the round-trip end-to-end.

## What this isn't

A few honest limits before you reach for the repo.

It is not, yet, a productized board you can buy. What runs today
is a SpinalHDL design synthesised onto an off-the-shelf
iCEbreaker UP5K. The three-tier product line in `README.md`
(Verde / Rojo / Negro across iCE40 UP5K, ECP5, and CertusPro-NX
silicon) is a roadmap, not a shipping list.

It is not a finished higher-level SDK. The Layer 1 SDK
mentioned in post 2 — the host-side library that turns "issue a
write_read at 0x2a" into the bytecode you saw above — is on the
roadmap; today you write tests in Layer 0 assembly, which is
what the rest of this series demonstrates.

The wire format is not, yet, a stability contract. The v0.2
preamble layout, instruction encoding, HALT-word bit positions,
and result-ring format do not become release contracts until
Phase 0 ships its first tagged encoder release. The golden
fixtures under `mole-asm/tests/` prove byte-stability *within* a
build, not across revisions. If you pin against a tag, pin
against the tag.

None of those are surprises. They are what the project actually
is at v0.2 on REVISION 0.1.1 silicon: a working bit-cycle engine
that runs end-to-end on real hardware, an assembler that targets
it, a loader that ships it, and a result ring that brings the
evidence back. The next four posts fill in the inside.
