+++
title = "From 'it works' to 'I trust it': two torture loops on a NOR driver"
date = 2026-06-28T09:00:00
description = "A FlexSPI NOR driver that passed its self-test still wedged on a cancelled async future and silently zeroed a byte before an unaligned write. This is the story of the two soak rigs that caught both, and what trusting a driver actually takes."
[taxonomies]
tags = ["embedded", "rust", "embassy", "mcxa", "flexspi", "drivers", "testing"]
+++

The FlexSPI NOR driver for the NXP MCXA5xx already worked. The
blocking self-test was green, the examples ran, I could erase a
sector, program a page, read it back, and watch the same bytes come
out of the memory-mapped window. By the usual bar for "the driver
works," it worked.

That bar is too low. A driver that passes its own happy-path
self-test has demonstrated exactly one thing: that it can succeed when
nothing goes wrong. It has said nothing about what happens when an
async operation gets cancelled half a microsecond into a command
shift, or when a caller does a partial-page write at an address the
controller quietly dislikes. The distance between "it works" and "I
trust it" is made entirely of those questions, and you don't answer
them by running the happy path again.

You answer them by building something whose only job is to be nasty to
the driver and to notice when the driver flinches. This post is about
two such things — a pair of self-checking soak rigs for the
`embassy-mcxa` FlexSPI driver — and the two bugs they caught on the
way. The work landed in [embassy-rs/embassy#6389][pr], validated on an
FRDM-MCXA577 with its on-board Winbond W25Q64 (8 MiB), and it builds on
the register-level fixes ([AHBCR][ahbcr], `DLLCR`, `FLSHCR2`) from
[#6386][pr6386] — more on how *those* got found in a moment, because it
was the same rig.

[pr]: https://github.com/embassy-rs/embassy/pull/6389
[pr6386]: https://github.com/embassy-rs/embassy/pull/6386
[ahbcr]: https://github.com/embassy-rs/embassy/pull/6386

## What a torture loop has to be

A soak test that only loops the happy path faster is not a soak test;
it's a slow way to confirm what you already believed. To actually buy
trust, the loop has to have a few properties, and getting those
properties right was most of the work.

It has to be **adversarial**: every round erases a fresh window,
programs it with randomly-sized, randomly-placed sub-page writes and
full pages, and then reads it back through *two independent paths* —
the IP command path and the memory-mapped AHB window — asserting they
agree with each other and with the pattern it wrote. A bug that lives
in only one path can't hide when the other path is watching.

It has to be **reproducible**: every round is driven by a logged
xorshift seed, so a failure three hours in isn't a ghost — it's a seed
I can replay from a clean boot.

It has to **protect itself from itself**: a torture loop that programs
random addresses is one off-by-one away from scribbling on the sector
it's verifying, or wearing a single sector to death. So the window
rotates by a prime stride to spread wear, a per-sector erase budget
caps the damage, a wall-clock limit ends the run, and a **never-erased
canary sector** sits off to the side. If a wild write ever lands in
the canary, the canary stops matching, and I learn that the driver
wrote somewhere it was never told to.

Stripped of the bookkeeping, each round looks like this:

```rust
loop {
    let seed = rng.next();          // logged, so any failure replays
    let window = next_window(seed); // prime-stride rotation, wear-spread

    erase(window)?;
    verify_erased(window)?;

    for write in random_writes(seed, window) {
        program(write.addr, write.bytes)?;
    }

    // The load-bearing assertion: two independent read paths must
    // agree with each other and with what we wrote.
    assert_eq!(read_ip(window),   expected(seed));
    assert_eq!(read_mmap(window), expected(seed));

    assert_canary_intact()?;        // nobody wrote where they shouldn't
    if elapsed() > LIMIT { break; }
}
```

The first time I pointed this at the driver, it didn't get to a clean
round. The IP path and the memory-mapped path disagreed, which is how
the `AHBCR.AFLASHBASE` addressing bug surfaced — I fixed that
separately in [#6386][pr6386] before any of the rest of this made
sense. With that fixed, the rig got much further, and then found
something stranger.

## What it found first: the byte before the write keeps going to zero

The failure was small and specific, which is the worst kind. Most
rounds were clean. But on rounds where the random writes happened to
place one sub-page write starting at an address with `addr % 8` of 5 or
7, and *another* write followed in the same page, the byte immediately
*before* the second write's start address came back as `0x00`. Not the
bytes I wrote — those were fine. The byte *before* them, which I hadn't
touched in that operation at all.

My first assumption was that I had a bug in the FIFO-fill loop: an
off-by-one in how the driver packs the caller's buffer into the
controller's TX FIFO. I went and read that loop very carefully, then
did the thing I should have done first and diffed it, byte for byte,
against NXP's `FLEXSPI_WriteBlocking` in their SDK. It was identical.
The driver was filling the FIFO exactly the way the vendor does.

That reframed the whole problem. If the loop is byte-identical to the
reference and the reference is trusted, the corruption isn't in the
loop — it's in the controller's handling of an unaligned write boundary
between two consecutive writes in a page. A single write of any length,
at any alignment, is fine. It's specifically the seam between two
sub-page writes when the second one starts unaligned that the IP
mangles. This is a controller alignment quirk, and no amount of
re-reading my code was going to fix a property of the silicon.

So the fix isn't to make the unaligned write work — it's to make the
unaligned write *impossible to ask for*, loudly, instead of letting it
silently corrupt an adjacent byte. `page_program` now enforces a
write-size contract: the start address and the length must both be
8-byte aligned, and the write must stay within a single page. Anything
else returns an error before a single byte reaches the flash.

```rust
// The 8 is a property of the FlexSPI IP/DMA FIFO window,
// not of the flash chip. Name it so nobody "optimizes" it away.
const WRITE_GRANULARITY: usize = 8;

fn check_program(addr: u32, len: usize, flash_size: u32) -> Result<(), IoError> {
    if addr as usize % WRITE_GRANULARITY != 0 || len % WRITE_GRANULARITY != 0 {
        return Err(IoError::Misaligned);
    }
    match addr.checked_add(len as u32) {
        Some(end) if end <= flash_size => {}
        _ => return Err(IoError::OutOfBounds),
    }
    // ...plus the single-page check...
    Ok(())
}
```

Two things made this an easy call rather than a regression. First, it's
the exact granularity the DMA write path already required (`len % 8 ==
0`), so the async/DMA side of the driver had always lived under this
contract — I was only making the blocking side honest about it. Second,
it maps cleanly onto `embedded-storage`'s `WRITE_SIZE` model, so a
future trait impl falls out naturally rather than fighting the
contract. The same pass also bounds-checks `read`, `erase_sector`, and
`program` against the configured flash size, returning
`IoError::OutOfBounds` instead of wrapping around. Both new variants
went onto an already `#[non_exhaustive]` `IoError`, so none of this
breaks callers — the only behavioural change is that a sub-page write
now has to align to 8 bytes, where before it would silently eat the
byte next door.

Partial-page writes still work. They just work at 8-byte granularity,
the way the hardware can actually deliver them. I updated the bundled
examples to match (a `100`-byte partial write became `96`; the stress
example's program sizes got rounded to multiples of 8), which was a
nice forcing function for confirming the contract was livable and not
just defensible.

## Pushing the same idea at the async path

The blocking driver was now hardened and soaked. But the interesting
half of an Embassy driver is the async half, and async brings a failure
mode that blocking code simply doesn't have: **cancellation**.

In async Rust, the idiomatic way to bound an operation is to wrap it in
`with_timeout`, or race it in a `select!`, or cancel the task it runs
in. All three do the same thing under the hood: they *drop the future
mid-flight*. The operation was in progress, and now it isn't, and it
never got to run whatever cleanup it might have wanted to run. Any
async driver that can't survive that is a driver with a landmine in it,
waiting for the first caller who puts a timeout around a flash write.

So the second rig, `flexspi-cancel-soak`, does that on purpose,
thousands of times. It runs the same erase/program/verify integrity
checks over the async + DMA path, and then it does something deliberately
hostile: it takes a real operation, wraps it in `with_timeout` with a
delay swept across a wide range — from about a microsecond (mid
command-shift, the operation has barely started) out to a couple of
milliseconds (mid write-in-progress, the flash is busy) — so the future
gets dropped at every interesting phase of the transaction. Then it
issues a *fresh* operation and asserts that one returns correct data.

The elegant part of testing cancel-safety is that you barely need an
assertion. A driver that isn't cancel-safe doesn't return wrong data
after a botched cancellation — it *hangs the next operation forever*. So
the test simply *reaching the next round*, across thousands of
in-flight cancellations, is itself the pass signal. If the rig is still
counting rounds, the driver is recovering. If it stops, it wedged.

It stopped.

## What it found second: a dropped future wedges the controller

The symptom was a hang, deep in `prepare_ip_transfer`, spinning in
`wait_idle()` waiting for a sequence engine that was never going to
become idle. The diagnosis, once I dumped the controller state at the
hang, was clear: the cancelled operation had left the FlexSPI sequence
engine non-idle — chip-select still asserted, the TX FIFO in underrun —
and the driver had no notion that it should clean any of that up. The
*next* operation walked in, asked the engine to be idle, and waited for
a condition that the *previous, cancelled* operation had made
permanently false. One cancelled future, and the controller was bricked
until reset.

The fix is to stop trusting that the engine is idle just because the
last operation "finished," and instead make `prepare_ip_transfer`
self-healing. On entry, if the engine isn't idle — the fingerprint of a
previously cancelled op — force it idle with a software reset before
doing anything else:

```rust
fn prepare_ip_transfer(&mut self) -> Result<(), IoError> {
    // A cancelled op leaves the sequence engine non-idle: CS still
    // asserted, TX FIFO underrun. Detect that fingerprint and recover.
    //
    // One SEQIDLE sample is authoritative here: SEQIDLE only deasserts
    // on a fresh IPCMD trigger, and none is pending at this point.
    if !self.seq_idle() {
        // Force idle, de-assert CS, reset the instruction pointer,
        // flush the FIFOs. The LUT and controller config survive a
        // software reset -- the init path already relies on that.
        self.software_reset();
    }
    self.wait_idle();
    // ...load the IP command, trigger IPCMD...
    Ok(())
}
```

There's a deliberate design decision hiding in what this fix *doesn't*
do. It does not add a timeout. The driver imposes no timeout of its own
and keeps `embassy-time` out of its hot path entirely. Timeout *policy*
— how long is too long, and whether to give up — belongs to the
application, which already has `with_timeout` for exactly that. The
driver's job isn't to decide the policy; it's to make the policy
*safe*. The self-healing recovery is precisely what lets a caller wrap
any operation in `with_timeout` without fear: when their timeout
elapses and drops the future, the driver un-wedges itself on the next
call instead of staying broken. The cancellation that used to be a
landmine is now just a thing that happens.

## The payoff

With both fixes in, the two rigs ran clean:

- **`flexspi-soak`** (blocking IP + memory-mapped): 384 rounds, zero
  mismatches between the IP and AHB paths, and every out-of-contract
  call — misaligned, page-crossing, out-of-range — returning the error
  it should.
- **`flexspi-cancel-soak`** (async + DMA): 388 rounds, **5,044
  in-flight cancellations**, 3,880 clean recoveries, zero wedges, zero
  data corruption.

Those 5,044 cancellations are the number I care about most, because
every one of them is a `with_timeout` firing at a moment that, a week
earlier, would have bricked the controller until the next reset.

## What I'd carry to the next driver

A few things generalize past this one chip:

A happy-path self-test proves a driver *can* work. It says nothing
about whether you can trust it. Those are different claims, and only the
second one matters once real callers show up.

**Cancel-safety is a first-class property of an async driver**, not an
edge case. If dropping an in-flight future can leave your peripheral in
a state the next operation can't recover from, you don't have a working
async driver yet — you have one that works until someone uses a timeout.
And in Embassy, *someone always uses a timeout.*

**Make illegal states loud.** The unaligned write didn't fail — it
succeeded and corrupted a neighbour. A contract that turns "silently
wrong" into "loudly refused" is worth more than it costs, every time.

**Seed your randomness.** A fuzzing soak loop that can't replay its own
failures is a slot machine. The xorshift seed turned "it broke
somewhere in three hours" into "it broke on this seed, here's the
repro."

## A note on trusting it

I trust this driver today — on this board, with this flash part,
against everything the two rigs throw at it. That's a real and specific
claim, and it's also a smaller one than "it's correct." Both bugs in
this post were invisible until the moment a rig was nasty enough to
provoke them, and the honest conclusion to draw from that is *not* that
I've now found the last one. It's that the next rig, or the next caller
with a workload I didn't imagine, may well surface something these two
didn't. Soak tests buy confidence proportional to how mean they are, and
no finite amount of meanness reaches a proof. If you run this driver and
it does something neither rig caught, I'd genuinely like to know — that's
the rig I haven't built yet.

## What this doesn't cover

The usual caveats, and they're real ones. This is one board, one flash
part, one controller IP. The unaligned-write corruption is a quirk of
*this* FlexSPI block, and I'd be unsurprised if it looked nothing like
that on other silicon — the *shape* of the lesson (diff against the
vendor reference; if your loop is identical, suspect the controller)
transfers better than the specifics. The driver also doesn't implement
the `embedded-storage` traits yet, though the write-size contract was
designed so it maps straight onto `WRITE_SIZE = 8` / `ERASE_SIZE =
4096` when it does.

The full set of fixes, the two soak rigs, and the hardware run logs are
in [embassy-rs/embassy#6389][pr]. If you're bringing up FlexSPI on
MCXA, I hope the rigs save you the half-week. If you're doing it on
something else and one of these patterns rhymes with what you're seeing,
I'd like to hear about it.
