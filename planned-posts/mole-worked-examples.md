+++
title = "Mole, in anger: four worked examples"
date = 2026-09-07T09:00:00
description = "Four mole programs: a clean I²C read to establish the harness, two canonical failure-path tests (SCL stretch past the timeout, STOP injected mid-byte), and the post-1 reproducer running in a tight loop at quarter-bit clock — turning a verdict-by-soak campaign that took days into one that runs in minutes."
[taxonomies]
tags = ["embedded", "rust", "fpga", "spinalhdl", "mole", "testing", "i2c", "drivers"]
+++

The first five posts in this series built up the *why* and the
*how*. Post 1 framed the verdict-by-soak campaign that motivated
the whole thing — an I²C target whose `respond_to_write` returned
`Restarted(0)` on a boundary-aligned `write_read`, where I needed
to drive the provoking pattern hard enough to tell a benign API
surfacing from a real hardware race. Post 2 explained what shape
of tool that called for. Posts 3 through 5 took the engine apart
in increasing detail: the repo, the pipeline, the bit-cycle clock.

This post is the in-anger demonstration. Four programs. One that
establishes the harness, two that probe canonical I²C failure
paths a transport-level analyzer cannot produce, and one that
brings the series full circle by running [post 1's][p1]
`write_read` reproducer as a mole program in a tight loop on the
engine's own clock.

[p1]: /posts/i2c-target-restarted-zero/

These are the tests I wish I'd had during the campaign in post 1.
Three of them did not exist as anything I could run on the bench
when I started writing this series. They exist now because the
engine, the assembler, and the loader exist now. The fourth — the
clean read — is the baseline that makes the other three legible.
Read in order; the arc from "this is a normal mole program" to
"this is the program that closes out the verdict" is the post.

## Example 1 — a clean I²C read

The baseline. A 16-bit read of the TMP108's temperature register
at address `0x48`, in standard-mode I²C at 100 kHz: the kind of
transaction every I²C driver does correctly without trying. The
point is the *shape*. Once you can read this program top to
bottom, the vocabulary the three failure-path examples rely on is
already in your head.

```text
.equ slow_div, 59           ; 100 kHz at the 24 MHz fabric

start:
        LOAD_TIMING       reg=0, divider=slow_div
        SET_BUS_MODE      i2c
        SET_ROLE          controller

        MARK              label=0x10            ; "begin read"

        ; START: 4 idle + 2 SDA-fall + 2 SCL-down (canonical 8 quarters)
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant

        ; addr 0x48 << 1 | W = 0x90, expect ACK
        EMIT_BYTE_IMM     imm=0x90 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, nak

        ; pointer register 0x00 (temperature), expect ACK
        EMIT_BYTE_IMM     imm=0x00 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, nak

        ; Repeated START (10 quarters: 2 hold + 2 SDA-release + 2 idle + 2 SDA-fall + 2 SCL-down)
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant

        ; addr 0x48 << 1 | R = 0x91, expect ACK
        EMIT_BYTE_IMM     imm=0x91 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, nak

        ; MSB: 8 capture bits + controller ACK
        LOAD_LOOP         8
msb:    EMIT_BIT_IMM      tx=recessive capture=1
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, msb
        EMIT_BIT_IMM      tx=dominant            ; controller ACK

        ; LSB: 8 capture bits + controller NACK
        LOAD_LOOP         8
lsb:    EMIT_BIT_IMM      tx=recessive capture=1
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, lsb
        EMIT_BIT_IMM      tx=recessive           ; controller NACK

        ; STOP (6 quarters: 2 hold + 2 SCL-release + 2 SDA-release)
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive

        MARK              label=0x11             ; "end read"
        HALT              status=0

nak:    MARK              label=0xFE             ; "device did not ACK"
        HALT              status=1
```

The preamble — `LOAD_TIMING`, `SET_BUS_MODE`, `SET_ROLE` — is the
same three opcodes every controller-role program starts with.
START, Sr, and STOP are not opcodes; they are `EMIT_QUARTER_IMM`
sequences spelled out edge by edge, because the level where you
spell them is the level your fault-injection knobs live at.
Compile-time-known bytes (the I²C address, the register pointer)
ride on `EMIT_BYTE_IMM`, which clocks eight data bits MSB-first
plus a ninth `hiz`-SDA ACK slot and carries the slave's ACK
through the `expect=0 mask=1 capture=1` triple — one instruction,
one wire-level byte, one captured ACK observation. Slave-sourced
data bits stay bit-by-bit so each one lands as its own CAPTURE
record. The `MARK label=0x10` / `label=0x11` bracket lets a host
decoder chunk the result ring into "everything between these is
the read" without ambiguity.

After this program halts cleanly, the result ring contains a
REVISION word at slot 0, the `0x10` MARK, three CAPTURE records
for the three ACK observations, sixteen CAPTURE records for the
two read bytes, the `0x11` MARK, and a HALT word at the tail with
`status=0 mismatch=false overflow=false`. That trace is the
artifact. The other three examples produce different traces; the
program that produces the trace and the trace itself are both
committed alongside the test.

## Example 2 — SCL stretch past the driver's timeout

A target may pull SCL low between bits to ask the controller to
wait. Every controller imposes its own cap on how long it is
willing to wait before declaring the bus stuck and recovering.
That cap is rarely tested. Most controller drivers have it; most
have never had it tripped against them on purpose.

This is a target-role test. Mole emulates the slave; the
device-under-test is the controller driver. After receiving a few
data bits at the canonical SCL pattern, mole stretches SCL low
for long enough that the controller's stretch timeout has to fire:

```text
.equ slow_div, 59

start:
        LOAD_TIMING       reg=0, divider=slow_div
        SET_BUS_MODE      i2c
        SET_ROLE          target

        ; Wait for the controller to put a START on the wire.
        WAIT_ON           START_SEEN, 0

        ; Sample 8 address bits + R/W on the controller's SCL.
        LOAD_LOOP         9
addr:   SAMPLE_BIT_ON_SCL capture=1
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, addr

        ; ACK the address.
        DRIVE_BIT_ON_SCL  tx=dominant

        ; Receive 4 data bits cleanly.
        LOAD_LOOP         4
data:   SAMPLE_BIT_ON_SCL capture=1
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, data

        ; --- stretch: hold SCL low for 1500 quarter-bits ---
        MARK              label=0x20             ; "stretch begin"
        STRETCH_SCL_IMM   1500
        MARK              label=0x21             ; "stretch end (mole released)"

        ; ... whatever happens next is the controller's recovery
        ; path or its absence; capture whatever bits arrive ...
        HALT              status=0
```

`SET_ROLE target` flips the engine into follower mode for the
rest of the program. `SAMPLE_BIT_ON_SCL` and `DRIVE_BIT_ON_SCL`
slave to the external SCL — they wait for the controller's
edges, never produce them. `STRETCH_SCL_IMM` in target role is
the one path by which a follower may actively pull SCL low: the
controller-under-test sees SCL stretched and either waits, fires
its timeout and recovers, or hangs. 1500 quarter-bit-times at
100 kHz is about 3.75 ms, comfortably past the 1 ms-class
stretch timeouts most controller drivers will tolerate.

The test asks one question: when mole stops stretching, does the
controller driver recover cleanly, or does it leave the bus
wedged? A working driver fires its stretch timeout, issues a
bus-recovery sequence, and is ready to talk again. A driver
whose recovery path has never been exercised hangs on a
transaction that should have errored. The result ring captures
what mole observed; the controller-side log captures what the
driver did. Together they answer a question that — until you can
drive the stretch from your own follower, with sub-bit precision,
on a programmable timer — you cannot ask at all.

## Example 3 — STOP injected mid-byte

The other canonical failure mode: a STOP condition that arrives
before the ninth (ACK) bit of a data byte. By the spec, the
target should reset its receive state machine to idle and not
interpret the partial byte as data. By the bench, plenty of
target drivers have an implicit "a byte will always have nine
bits" baked into a couple of otherwise unrelated places, and
discover that assumption only when something violates it.

Mole violates it by spelling the byte out at the bit level and
substituting a STOP for the last few bits:

```text
        ; addr 0x90 + ACK (clean)
        EMIT_BYTE_IMM     imm=0x90 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, nak

        ; --- malformed data byte: 4 bits, then STOP mid-byte ---
        EMIT_BIT_IMM      tx=dominant            ; bit 7
        EMIT_BIT_IMM      tx=recessive           ; bit 6
        EMIT_BIT_IMM      tx=dominant            ; bit 5
        EMIT_BIT_IMM      tx=recessive           ; bit 4

        ; STOP injected mid-byte (SDA released while SCL is high)
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive

        MARK              label=0x30             ; "fractional byte + STOP"

        ; ... then a fresh START + a clean transaction the target
        ; should answer normally if it has reset to idle ...
```

The first four bits drive normally on the canonical SCL pattern.
Then, instead of bits 3-0 and an ACK, we run a STOP sequence:
SDA rises while SCL is high. Whatever was on the wire during the
half-byte is, by I²C definition, not a byte. A correct target
discards the partial state and waits for the next START. An
incorrect target may have latched something, may have decremented
a counter it should not have decremented, may answer the next
transaction with stale state.

The next transaction is exactly the test. After the `0x30` MARK
we issue a fresh START and a clean read, and the result ring
records whether the address ACKed cleanly and whether the data
bytes match what the device should be returning. The pre-STOP
fragment, the recovery, and the post-STOP transaction all live
in the same ring, separated by MARKs, deterministically replayed
on every run.

## Example 4 — the post-1 reproducer

The series's payoff. [Post 1][p1] left a verdict-by-soak campaign
open: a `write_read` with an 8-byte boundary-aligned write into
an 8-byte slave buffer, followed by a repeated start and an
8-byte read, was returning `Restarted(0)` and might or might not
have been a race. The hypothesis test needed many millions of
iterations of that exact transaction, with the spacing between
the STOP-equivalent edge and the Sr controlled. On a second MCU,
that took days per data point. The mole version is
[`i2c-soak.moleasm`](https://github.com/felipebalbi/mole/blob/main/mole-asm/tests/fixtures/i2c-soak.moleasm)
in the repo; the boundary-case shape is the one below, with a
finite outer counter swapped in so the program eventually halts
and the host can drain the ring:

```text
.equ fast_div, 14            ; 400 kHz at the 24 MHz fabric
.equ iter_count, 1000        ; iterations of the boundary case

        LOAD_TIMING       reg=0, divider=fast_div
        SET_BUS_MODE      i2c
        SET_ROLE          controller

        LOAD_IMM          R5, iter_count       ; outer counter

loop_top:
        ; -- START --
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant

        ; -- addr 0x2A + W = 0x54 + slave ACK + fail-fast --
        EMIT_BYTE_IMM     imm=0x54 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, wedge

        ; -- write 8 bytes (the boundary payload); pattern from R7 --
        LOAD_LOOP         8
        LOAD_IMM          R7, 0x55
write_byte:
        EMIT_BYTE_REG     expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, wedge
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, write_byte

        ; -- Repeated START --
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=dominant
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant

        ; -- addr 0x2A + R = 0x55 + slave ACK --
        EMIT_BYTE_IMM     imm=0x55 expect=0 mask=1 capture=1
        BRANCH_ON         MISMATCH, wedge

        ; -- read 8 bytes (7 + ACK, 1 + NACK) --
        LOAD_LOOP         7
read_ack:
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=dominant            ; controller ACK
        DEC               R6
        BRANCH_ON         NOT_REG_ZERO, read_ack
        ; last byte: 8 bits + NACK
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive capture=1
        EMIT_BIT_IMM      tx=recessive           ; controller NACK

        ; -- STOP --
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=dominant
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=dominant  scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive
        EMIT_QUARTER_IMM  sda=recessive scl=recessive

        ; -- tBUF: hold idle before the next START --
        STRETCH_SCL_IMM   3

        ; -- back-edge --
        DEC               R5
        BRANCH_ON         NOT_REG_ZERO, loop_top

        MARK              label=0xAA              ; "soak complete"
        HALT              status=0

wedge:  MARK              label=0xFE              ; "slave did not ACK"
        HALT              status=1
```

The program is committed alongside its expected result-ring trace
in the repo. The iteration rate is bounded by the engine's
quarter-bit clock and the bus's `tBUF` recovery time, not by host
scheduling — at 400 kHz with a three-quarter `tBUF`, one
`write_read` cycle is about 250 µs of wire time, so 1000
iterations run in roughly a quarter of a second of engine time;
scaling the outer counter to the millions is just changing one
14-bit immediate. Each iteration produces the same byte sequence
on the wire because the engine has no runtime randomness; the
trace is reproducible across machines and rebuilds.

The verdict-by-soak campaign from post 1 took days per data point
on a second MCU. The same campaign as a mole program runs in
minutes. That, more than anything else, is what the engine is
for: a program plus a counter, on its own clock, hammering one
specific waveform shape until the question has been asked enough
times for the answer to mean something.

At time of writing the soak is still climbing and the verdict on
the `Restarted(0)` hypothesis is not yet in. The shape of the
answer, when it lands, is one of two things: a clean pass over a
number of iterations I am willing to put my name to, which
promotes `Restarted(0)` to benign API surfacing the trait was
designed to expose; or a captured failing iteration whose trace
in the result ring lets me hand the driver code a real bug
report. The program above is what generates either outcome. The
verdict isn't in the post yet; the *means by which the verdict
will arrive* is.

## Reading the result ring

The toolchain on the host side is small and obvious. `mole-asm
assemble program.moleasm --frame` produces a `.mole.bin` artifact:
the bytecode plus the wire-frame preamble and CRC. `mole-loader
-p /dev/ttyUSB0 program.mole.bin` ships that frame to the engine,
waits for `HALT`, drains the result ring, decodes its
REVISION + CAPTURE + MARK + HALT records, and prints them in a
form a human can read and a test runner can diff. Pass `--dry-run`
to validate the frame without touching the wire. Pass
`--dump-ring <path>` to keep the raw evidence alongside the
decoded report. The decoded report is the test artifact you
commit next to the assembly: assemble, ship, drain, diff.
Reproducibility is structural — the engine has no runtime
randomness, so the trace either matches the committed expectation
or it doesn't.

## Where this goes next

Three short pointers and the series is done.

**More tests, less assembly.** Everything above is written in
Layer 0 assembly because that is what ships today. A higher-level
SDK on the host is on the roadmap and will make the same tests
considerably shorter — the four examples in this post would all
collapse into something closer to a sentence each. Layer 0 is not
going anywhere; it is the substrate the SDK compiles down to, and
the tests above will keep running against it bit-for-bit. The SDK
is what turns "I want to soak this driver against a STOP-mid-byte
fault" from forty lines of assembly into one call.

**More targets.** The iCEbreaker UP5K runs the engine today. The
higher tiers in the product line — bigger FPGAs, deeper capture
rings, faster timing — extend what the engine can hold, not the
ISA. Tests written today carry forward unchanged. I³C target
drivers are the same shape with a different SDK namespace on top;
the engine knows about quarter-bit-time and sticky flags, not
about SDR or HDR-DDR, so the techniques in this post extend
directly. Worked examples for I³C target failure paths will
follow.

**More tests of yours.** The engine is bench-validated. The
assembly is documented in [the mole book][book] and reference
[ISA spec][spec]. The host toolchain compiles, loads, runs,
drains. If you have an I²C target driver and an iCEbreaker, the
loop is closed. Mole does not replace your logic analyzer; you
still want the wire-level trace. It does not replace your unit
tests; you still want pure-software regression coverage. What it
adds is the layer in between: the previously-impossible failure
paths, driven on demand, recorded deterministically, committed
alongside their evidence.

[book]: https://github.com/felipebalbi/mole/tree/main/book
[spec]: https://github.com/felipebalbi/mole/blob/main/docs/MOLE-0.2-SPEC.md

Post 1 asked, "is this a race?" The answer to that question takes
the form of a committed program plus a committed trace — the kind
of evidence a driver bug report can be argued from, with a
verdict no one has to take on faith. That, more than anything
else this series has covered, is what mole is for.
