+++
title = "What did the boot ROM just do to my RAM?"
date = 2026-06-11T09:00:00
description = "rambo: a small CLI that primes a Cortex-M's SRAM with a known pattern, resets, halts before user code, and tells you which RAM the boot ROM clobbered."
[taxonomies]
tags = ["embedded", "rust", "rambo"]
+++

A while back I was trying to get [teleprobe](https://github.com/embassy-rs/teleprobe)
to run a test suite on an RT685-EVK. Teleprobe's trick is that it takes
your test, compiled as a standalone firmware binary linked to execute
directly from SRAM, loads it into the target's RAM over the probe, and
runs it from there — flash never gets touched. On every board I'd used
it with before, it just worked. On the RT685, every run ended in a
HardFault before the first test got a chance to do anything.

<!-- more -->

The diagnosis took longer than the bug deserved. The RT685's boot ROM
was power-cycling almost every SRAM partition on the way to user code —
including the one the test binary was linked into. The instructions
were being scribbled over between the load and the first fetch, so the
core would dutifully jump to what *had been* code, hit garbage, and
fault. Nothing in the reference manual said which partitions the ROM
touches at startup, and the HardFault itself only told me where
execution died, not why the instructions there were no longer the ones
I'd loaded. I worked it out by reading whatever I could find, writing a
few throwaway scripts, and squinting at probe dumps.

That experience is why I wrote [`rambo`](https://github.com/felipebalbi/rambo).
It's a small CLI whose only job is to answer the question I should have
been able to look up: *which parts of this chip's RAM does the boot ROM
clobber before my first instruction runs?*

## What rambo actually does

The core loop is short enough to describe in a paragraph. For every RAM
region probe-rs knows about for a given chip, rambo writes a deterministic
pattern across the whole region — each 32-bit word stores its own
address. Then it issues a reset and halts the core on the vector catch,
*before* any user firmware runs. It reads the region back and classifies
every block as one of four things: SAFE (the pattern survived), ZERO
(filled with `0x00000000`), ONES (filled with `0xFFFFFFFF`), or CHANGED
(rewritten with something else — ROM scratch, stack, or working data,
and sometimes just garbage from a partition the ROM power-cycled).
Output is a colored heatmap, a run-length-encoded summary of contiguous
regions sharing a class, and per-region totals.

It is deliberately a plain stdout program with ANSI colors. Not a TUI.
Not a simulator. Not a fancy GUI. It's something you can read in your
terminal, pipe to a file, or paste into a bug report when you ask a
vendor why their ROM is doing what it's doing. The only thing it depends
on at runtime is a debug probe and a chip that probe-rs supports — which,
in practice, is most of the Cortex-M world.

It does not flash anything. It does not modify your firmware. It writes
a pattern, resets, reads back. That's it.

## Why writing the address as data matters

The "each word stores its own address" pattern is the kind of thing that
looks arbitrary until you've tried the alternatives. A solid bit pattern
like `0xDEADBEEF` repeated everywhere will tell you whether RAM was
clobbered, but it won't tell you whether the readback came from the
address you think it did — if the chip aliases memory or has unmapped
windows in a reserved range, you can read the same `0xDEADBEEF` back
from two different addresses and not notice. With `addr-as-data`, every
location is uniquely identifiable. A SAFE word is unambiguous. A ZERO
or ONES word is unambiguous. A CHANGED word that *also* happens to
equal some *other* valid address is a strong hint that something is
aliased. The pattern is essentially free to generate, and it carries
its own coordinate system.

## A small tour of the optional modes

The default survey answers the headline question. The optional modes are
there for when the answer to "what is the ROM doing here?" is more
interesting than "nothing" or "everything."

**`--fingerprint`** classifies the *kind* of clobber inside CHANGED
blocks. Is the block mostly zeros with a few non-zero words at the top?
That looks like a stack frame. Is every word the same non-zero value?
That's a constant fill. Is it an ascending counter, a repeating motif,
or an address-plus-offset pattern? Each of those points at a different
ROM behavior — a memset, a struct initialization, a copy with a fixed
offset, an uninitialized scratch buffer the ROM forgot to clear. None
of these tell you exactly what the ROM is doing, but they let you skip
straight past "is this even structured data" and into "this looks like
a 16-byte header followed by a counter."

**`--dual-pattern`** writes `addr` in one reset cycle and `~addr` in a
second. Both passes get classified independently and compared. If a
block came back SAFE in both passes — pattern survived both times —
then the ROM genuinely didn't touch it. If a block came back CHANGED
in both passes but with *the same* value, the ROM is actively writing
there regardless of what was already in memory. This disambiguates two
flavors of "the ROM didn't touch this" that single-pass surveys can't
tell apart: *undriven* (the ROM left it alone) versus *coincidentally
the same as my pattern* (extraordinarily unlikely with addr-as-data,
but possible in pathological cases).

**`--write-readback`** is the opposite trick: skip the reset entirely.
Write the pattern, immediately read it back, classify what comes out.
On normal RAM, this is boring — everything classifies as SAFE. On
reserved address ranges, on code-bus aliases of system RAM, on
peripheral windows that look like memory in the chip description but
aren't, this is where you find out. Aliasing shows up as the same
content at two addresses. Unmapped windows show up as bus faults or
all-ones reads. It's worth running once on any new chip just to see
what its memory map actually does, as opposed to what the documentation
claims.

**`--reset-cycles N`** re-runs the read after each of N resets without
re-writing the pattern in between. If the ROM is deterministic — same
chip, same reset, same post-ROM RAM contents every time — the
classification is identical across cycles. If anything drifts, you've
got nondeterminism worth investigating: timing-sensitive scratch,
uninitialized stack frames bleeding in from whatever was happening
microseconds before the reset, or RAM that hasn't fully settled.
This one is mostly useful when something *should* be the same every
boot but isn't, and you need evidence before you can start arguing
about it.

## The CI bit

The mode I'm most happy with isn't in the survey — it's in the two flags
that turn rambo into a regression gate.

`--json <path>` writes a stable, schema-versioned JSON report of the
entire run. Use it as a CI artifact, archive it across firmware
versions, diff it when something changes.

`--expectations <path>` takes a small declarative file — a "RAM
contract" — and evaluates each clause against the survey. Each entry
names a range and one of three claims:

```json
{
  "name": "main_sram_crash_dump_area",
  "range": { "start": "0x20030000", "end": "0x20031000" },
  "expect": "safe",
  "rationale": "Reserved for crash-dump recovery after watchdog reset."
}
```

The clauses are `expect: <class>` (every block must be that class),
`expect_any_of: [<class>, ...]` (every block must be one of these), and
`expect_not: <class>` (no block may be that class). Exit code is 0 if
every expectation holds, 1 if any fails. Ranges are checked for
alignment and bounds *before* any probe I/O happens, so a typo can't
brick a run.

What this gets you is a small, version-controlled answer to "the
bootloader assumes these regions survive the ROM" that runs on every
PR and on every new silicon rev. The next time a chip vendor patches
their ROM and quietly starts touching memory they didn't touch before,
your CI tells you. It's a tiny, boring thing. It's also the kind of
thing that would have saved me a few days on the RT685.

## What rambo isn't

A few honest limits before you reach for it.

It's only as accurate as probe-rs's chip database. If a chip's
`memory_map` omits a region, rambo can't survey it. Most of the time
this is fine; occasionally a vendor leaves a peripheral-attached SRAM
out of the description and you'll need to add it yourself (or
`--chip-description-path` your own).

Some CMSIS-Pack descriptions list code-bus *aliases* of system-bus
RAM as separate regions. Rambo treats every region in the map as
independent, so you'll see the same physical RAM surveyed twice under
different addresses. That's almost always what you want — the
aliases sometimes behave differently — but it can look surprising the
first time.

And "SAFE" only means "the ROM didn't write here on *this* chip rev,
with *this* fuse configuration, on *this* boot mode." It does not
mean the ROM will never write there. That gap is exactly why the
contract file and CI gate exist.

## Try it

`cargo install rambo`, or grab a pre-built binary from the
[releases page](https://github.com/felipebalbi/rambo/releases) for
Linux, macOS (Intel + Apple Silicon), or Windows. Source is on
[GitHub](https://github.com/felipebalbi/rambo).

If you run it on a chip I haven't tested with — so far that's
RT685, RT633, MCXA266, MCXA276, and MCXA577 — I'd genuinely like
to hear what came back. Especially if it surprises you.
