+++
title = "Is this a race? Verdict by soak."
date = 2026-07-06T09:00:00
description = "An I²C target terminator that may or may not be a race: Restarted(0) on respond_to_write, only when the controller's write payload is an integer multiple of the slave's receive-buffer length. The verdict-by-soak campaign characterizing it, why a second MCU isn't fast enough, and what the right tool would have to do."
[taxonomies]
tags = ["embedded", "rust", "i2c", "drivers", "rt685", "embassy", "bring-up", "mole"]
+++

The example does this most of the time and nobody notices: the
slave drains a write, the controller stops, the next `listen()`
yields cleanly, life goes on. Then the controller does
`write_read(addr, &w[..k*S], &mut r[..m])` where `S` is the
slave's receive-buffer length and the write payload is an exact
integer multiple of `S` — eight bytes into an eight-byte buffer,
then a repeated start, then a read — and the slave's second
`respond_to_write` returns `Restarted(0)`. Zero bytes from the
new sub-transaction. The example tags that branch with a comment
that reads, in full, `RACE WATCH`.

I put that comment in. I do not yet know whether it earns its
caps.

What I'm chasing lives on the [`i2c-slave-trait`][pr565] branch of
my `embassy-imxrt` fork — the branch that implements
[`embedded_mcu_hal::i2c::target`][target-trait] on the existing
`I2cSlave` driver for the i.MX RT685. Most of that work is the
kind of thing the [previous RT685 bring-up post][rambo] already
hinted at: bumping into the chip, then writing down what you
found. This one is different. The terminator I'm watching could be
a perfectly correct readout of a buffer-boundary case the trait
was designed to surface, or it could be the visible edge of a real
hardware race in which the slave's view of the bus has slipped
past the controller's by exactly the post-Stop / pre-Sr window.
The two hypotheses produce identical waveforms on the bench. The
only way to tell them apart is to run the provoking pattern in a
tight loop, for many millions of iterations, and watch for either
a wedge or silent corruption.

[pr565]: https://github.com/OpenDevicePartnership/embassy-imxrt/pull/565
[target-trait]: https://docs.rs/embedded-mcu-hal/latest/embedded_mcu_hal/i2c/target/index.html
[rambo]: /posts/rambo-rom-collateral-damage/

## The setup

RT685-EVK on FC2, acting as the I²C target at address `0x20`,
running the new trait-based example loop. The controller produces
back-to-back `write_read` calls with a write payload sized to land
exactly on the slave's receive-buffer boundary, then a repeated
start, then a read of `m` bytes. The driver and the example both
live on the PR's branch. The trait surface is straightforward:
`listen()` to discover what the controller wants, then
`respond_to_write()` or `respond_to_read()` in a loop, with the
returned `WriteStatus` or `ReadStatus` telling the caller why this
particular sub-transaction ended. No register tables in this
section. The rest of the post is about one specific termination
on one specific side of one specific transfer shape.

## What "RACE WATCH" actually means

The terminator sequence is the whole point, so it's worth slowing
down on it.

On a boundary-aligned write the first `respond_to_write` call
returns `BufferFull(S)`. That means exactly what it says: the
slave's receive buffer filled to its declared length, and the
controller has not yet asserted Stop or a repeated Start. The
trait contract for `BufferFull` is "call me again with more
buffer." So the caller loops back into `respond_to_write`.

The second call into `respond_to_write` returns `Restarted(0)`.
That also means exactly what it says: while we were getting ready
to drain more write bytes, the controller asserted Sr on the wire,
the sub-transaction ended, and the count of new bytes received
since the previous boundary is zero. The next `listen()` will
report the read side of the `write_read`. Everything downstream
behaves.

Two competing hypotheses fit that sequence. The first is benign:
the boundary case is structural, the trait is reporting accurately
("zero new bytes since the buffer-full edge, terminator was
Restart"), the driver is correct, and the only oddity is the
`(0)` payload, which is what a faithful "bytes since last boundary"
counter is *supposed* to print when the controller stops talking
on the byte right after the boundary. The second is unkind: the
slave's bus state has briefly diverged from the controller's
across the very narrow post-Stop / pre-Sr window, and what looks
like `Restarted(0)` is actually a transient confusion the driver
recovers from quickly enough that nothing else notices — until
some particular alignment of the planets, on some particular
iteration, when something else does notice and the whole transfer
goes sideways.

I cannot pick between these from one trace. I cannot pick between
them from a hundred. I can pick between them by running the
provoking pattern hard enough that, if there is a race, the race
would show itself.

## The verdict-by-soak campaign

The shape of the experiment is small enough to describe in
prose. The controller side runs

```rust
master.write_read(addr, &w[..k * S], &mut r[..m]).await?;
```

in a loop, sweeping `k` over `1..=4` and `m` over `1..=S - 1`
round-robin. That gives twelve unique `(k, m)` pairs per cycle,
each one a slightly different way of teeing up the boundary case
— some land on the first boundary, some on the second, some on
the fourth, with the trailing read shorter than `S` by varying
amounts. Every awaited transfer is wrapped in
`with_timeout(250 ms)`, so any wedge freezes the RTT log with the
offending iteration's `(k, m)` captured rather than hanging the
example indefinitely. On the slave side, every terminator branch
gets counted: `Stopped`, `Restarted`, and `BufferFull` on the
write side; `Complete`, `NeedMore`, and `EarlyStop` on the read
side; `RepeatedStart` and `Stop` for the address-edge events that
`listen()` produces; and the zero-byte `Probe` case for
completeness. A one-line summary prints every 10,000 iterations,
so the log is small enough to leave running unattended for as
long as it takes.

The verdict comes out of the counters and the timeouts. Pass
signal: many millions of clean iterations, all twelve `(k, m)`
pairs hit, no `with_timeout` ever fires, no read-payload mismatch
ever surfaces. That promotes `Restarted(0)` to "benign API
surfacing" — the trait is doing exactly what it advertised, and
the boundary case is just one of the shapes it advertises. Fail
signal: any single timeout, or any single iteration whose read
buffer comes back not matching what the controller asked for.
That promotes it to a real bug, with the failing iteration's
`(k, m)` and the surrounding terminator history captured in RTT.

The load-bearing point of this section is that the experimental
design *is* the post. The soak isn't running because the code is
broken; it's running because the correct test for "is this a
race?" is "drive the boundary case hard enough that a race
would show up." The verdict is the iteration count.

## Why a second MCU isn't fast enough

The obvious alternative is to drive the soak from another MCU.
Plug a second board onto the bus, write a tight loop that does
the `write_read(addr, &w[..k*S], &mut r[..m])` pattern, let it
run. This works. I have done it. It just takes days per data
point, and that's the problem.

The boundary case only happens when the controller lands a Stop
or an Sr at a specific instant — the instant right after the
slave's buffer fills. Any latency between transactions reduces
the rate at which that instant occurs. Any jitter in the
controller's scheduling means the spacing varies, which means
fewer of the iterations land exactly on the alignment that the
soak is trying to provoke. Bus turnaround time, the controller's
own interrupt handling, the test's logging path — they all eat
into the rate. A second MCU running a tight async loop is still
a general-purpose CPU doing best-effort scheduling, not a tool
purpose-built for hammering one specific waveform shape over and
over.

The cost shows up not in the soak's wall-clock — that's the same
either way, you leave it running overnight — but in the cost of
*changing your mind*. If I want to sweep a different range of
`k`, or add a third axis, or tighten the timeout, with a second
MCU that's another day's wait for the next data point. In
practice that means I change my mind less often than the
investigation deserves. A tool that can land one round of
characterisation in minutes instead of days is a tool that lets
the investigation iterate at the rate of the hypothesis, not at
the rate of the alternative MCU's scheduler.

A second MCU is not the wrong tool. It is *a* tool, and for a lot
of bring-up work it is exactly the right one. It is the wrong
tool for a verdict-by-soak campaign where the verdict needs the
soak to be both fast and sweep-able.

## What I'd really want

The shape the right instrument has to take falls out of all of
the above. I'd want a controller I can program at sub-bit-time
resolution, so the Sr I'm trying to characterise lands exactly
where on the byte boundary I want it, not wherever the
controller's scheduler chose to put it. I'd want deterministic
timing — same program, same waveform, every single time, no
runtime scheduler in the loop introducing jitter that the
hypothesis doesn't care about. I'd want a tight loop with no
host involvement per iteration, so the iteration rate is bounded
by the bus and not by USB latency or interrupt handling on a
laptop. And I'd want it to be programmable in something simpler
than another firmware project — a soak should be a few dozen
lines of source, not a build pipeline with a linker script.

The first three together is what makes a verdict-by-soak campaign
finish in minutes. The fourth is what makes it possible to write
the next soak the same afternoon you change your mind about the
hypothesis.

## What I don't yet know

The soak is running as I write this. As of today there is no
verdict. The iteration counter is climbing, the terminator
histogram looks the way I'd expect it to look if the
`Restarted(0)` case were benign API surfacing, and no
`with_timeout` has fired. None of that is the verdict; the
verdict is the iteration count, and the count isn't there yet.
This post will get updated, or a follow-up will be written, when
the soak terminates with either a clean pass at a number of
iterations I'm willing to put my name to, or a captured failing
iteration that I can hand back to the driver code as a real bug
report.

The tool described in the previous section exists. It's called
[mole], and the next post in this series is about why nothing in
the existing tool landscape was the right shape for this kind of
verdict-by-soak work, and why I ended up building one. The
boundary case I have been chasing here is the example that
forced the question.

[mole]: /posts/mole-why/
