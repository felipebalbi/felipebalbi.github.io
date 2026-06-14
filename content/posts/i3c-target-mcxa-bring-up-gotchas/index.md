+++
title = "I3C target mode on MCXA: three things the datasheet didn't warn me about"
date = 2026-06-20T09:00:00
description = "A single-chip, single-driver bring-up story for I3C target mode on NXP MCXA. Three concrete gotchas, three diagnoses, three fixes, and the checklist I wish I'd had on day one."
[taxonomies]
tags = ["embedded", "rust", "embassy", "i3c", "drivers", "bring-up"]
+++

I spent the last several weeks bringing up an I3C *target* driver for
the NXP MCXA family (MCXA2xx and MCXA5xx share the same I3C IP) inside
the embassy HAL. "Target" is what I3C calls the role I²C calls
"*slave*": the device that sits on the bus, responds to a controller,
and can also raise an in-band interrupt (IBI) and service a directed
read without anyone toggling a sideband pin.  The use case was the
boring one: a Rust target talking to a Rust controller, raising IBIs
and returning responses for a few million iterations without
panicking.

The driver landed in [embassy-rs/embassy#6160][pr] and the soak rig
has now ticked past 50 million IBI-then-directed-read iterations at
16-byte payloads, bus at 1.5 MHz SDR, against an NXP SDK controller
on the other end. It got there by way of three corner cases that
each took longer to diagnose than to fix, and that each looked
nothing like what I'd assumed they were when I started chasing them.
This post is those three.

One caveat up front: this is one vendor, one HAL crate, one
bring-up. I have no idea whether these gotchas generalize to other
I3C silicon. The shape of *how the bugs hid* probably transfers;
the specific register names and timing numbers don't.

[pr]: https://github.com/embassy-rs/embassy/pull/6160

## The setup

The hardware is two MCXA boards wired together over their I3C0
peripherals: one running the Rust controller, one running the Rust
target, both on top of the `embassy-mcxa` HAL. The bus runs SDR at
1.5 MHz push-pull / 750 kHz open-drain. The exchange under test is the
obvious one: controller writes a few bytes, target raises an IBI,
controller responds with a directed read, target returns a pre-loaded
payload, repeat. The target's RX path is DMA-fed into a bbqueue ring
so the IRQ can drain `SRDATAB` at line rate while the consuming task
takes its time — that ring is what makes the soak loop possible at
all, but it isn't where any of these three gotchas live.

For the first few days everything worked. Then the rig started
panicking after twenty thousand to a few hundred thousand
iterations with `InvalidStart` or `SdrParity` on the target side,
and I'd go back to staring at register dumps.

## Gotcha 1: the target clock was 4× too slow, and the symptom was on the controller

The first failure mode looked like SDA contention around the IBI
handshake — once every few hundred thousand iterations, the
controller would see a frame with bad parity, or the target would
surface `SERRWARN.INVSTART` on what should have been a clean
repeated-Start. Not every IBI; not even most IBIs. Just enough to
be unmissable in soak and unreproducible at the bench.

I lost a day re-reading the IBI sequencing code for a race that
wasn't there before I gave up and added a `dump_registers()`
method on both sides — one defmt line each, every `M*` / `S*`
register as a raw `u32`. Diffed against the NXP SDK's target on
the same wires, every register matched byte-for-byte except
`SCONFIG`. I had `BAMATCH=1` on the mcxa5xx example and
`BAMATCH=2` on mcxa2xx; the SDK had `BAMATCH=11`.

BAMATCH is the count, in I3C functional-clock cycles, the target
waits after seeing bus-idle before declaring the bus "available."
The HAL computes it as `fclk_MHz - 1` — correct, but only if
`fclk` is in the range the I3C spec assumes. The MCXA examples
were feeding I3C0 at roughly 3 MHz (mcxa5xx) and 2.8 MHz
(mcxa2xx), giving bus-available times of 0.36 µs and 0.67 µs; the
spec puts the minimum at 1 µs and the SDK runs at 12 MHz
(0.92 µs). With BAMATCH expiring sub-microsecond, the target was
declaring the bus available before the controller had finished
releasing SDA, and very occasionally both ends would drive the
line at the same time. That was the parity error and the
`INVSTART`: SDA contention manifesting one full transaction
downstream of the actual race.

The fix is one line in the example clock-tree setup: route
I3C0_FCLK from FRO_LF (12 MHz exact) with a /1 divider, and the
HAL's math produces `BAMATCH=11`. `SCONFIG` becomes `0x140b0019`
exactly, matching the SDK. The soak loop went from failing inside
half a million iterations to running indefinitely clean.

The lesson: when a vendor's reference and yours run on the same
wires and only one is reliable, dump every register on both sides
post-init and diff them — the difference is almost never where
you're looking. And if a peripheral's clock contributes to a
*bus-timing* parameter rather than just a baud rate, check that
field's spec minimum before you trust the formula the HAL uses to
compute it.

## Gotcha 2: raising the IBI before the FIFO was loaded

With the clocks corrected, the soak rig hit a different failure:
`SERRWARN.INVSTART` on the target, during the IBI-payload phase
itself this time. The trace was unambiguous — the controller ACK'd
the IBI, issued a repeated-Start with the target's dynamic address
and the read bit, started clocking SCL — and the target's TX FIFO
was empty when the first byte was demanded. The cause was an
ordering bug in `dma_respond_to_read_with_ibi`: arm TX DMA, set
`SCTRL.EVENT = Ibi`, then wait for DMA to drain into the FIFO.
The IBI arbitration plus the controller's auto-IBI ACK plus the
repeated-Start plus the read bit takes about 640 ns at 1.5 MHz —
on a quiet executor DMA setup wins that race; under realistic
Embassy load it doesn't.

The intuitive fix is to invert the order — drain DMA synchronously,
push the end-marker via `SWDATABE`, *then* raise `EVENT = Ibi`.
This works perfectly up to the TX FIFO depth, which on this IP is
8 bytes. The moment you try a 9-byte payload it deadlocks: DMA
blocks waiting for FIFO space; the FIFO won't drain because the
controller hasn't been told there's anything to read; the
controller hasn't been told because the IBI hasn't gone out. The
shape the fix has to take is the one the NXP SDK uses, and isn't
the one I'd have guessed: arm DMA, raise the IBI *immediately*,
then await DMA completion while the controller drains the FIFO at
the same rate DMA fills it.

```rust
// Arm DMA first so the first transfer is in flight.
self.dma_arm_tx(buf, end_marker).await?;
// Raise IBI immediately. Controller starts clocking; FIFO drains
// concurrently with DMA filling it.
self.regs.sctrl().modify(|w| w.set_event(Event::Ibi));
// Now wait for DMA. The FIFO is active throughout.
self.dma_wait_complete().await?;
```

The lesson I keep relearning: when "obviously correct order" and
"actually correct order" disagree, the hardware is usually right.
If a peripheral's FIFO is meant to be filled concurrently with
being drained, you can't fill it sequentially before signalling
the drain, however much safer that feels.

## Gotcha 3: the post-IBI Stop turned my repeated-Start into a fresh frame

The third one took longer to believe than it did to find. With
clocks correct and the IBI ordering fixed, Rust↔Rust soak still
failed — much later — and the failure mode was that the target's
directed-read response was misaligned by a byte, or surfaced as
`InvalidStart` immediately after a successful IBI.

The IBI itself was clean. The handshake worked. The TX FIFO was
loaded correctly. The problem was that the controller side, in
`async_wait_for_ibi`, was emitting an `async_stop` once the IBI
completed. That turned the next `async_read` the caller issued
into a fresh Start at the dynamic address, rather than the
repeated-Start the target's pre-loaded response was sitting there
expecting. The target was set up for Sr→addr→R; the controller
handed it Stop→Start→addr→R; the framing went sideways.

The fix is to delete the `async_stop`. After a successful IBI the
bus is already in `NORMACT` and the controller can issue a
repeated-Start directly into the directed-read. The function's doc
comment now spells out at some length why putting the Stop back
would be wrong, because the temptation to "clean up after yourself"
by trailing every transaction with a Stop is real.

The lesson is shorter than the bug: I3C, like I²C, distinguishes
Start, repeated-Start, and Stop, and the *target's* response state
machine cares which one comes next. Pre-load a response for a
repeated-Start, hand the target a fresh Start instead, and you
don't get a parity error — you get a perfectly framed read that
returns the wrong bytes. That's the worst kind of bug to have in
soak: the symptom looks like data corruption, not a protocol
violation.

## What I'd do first next time

If I were starting an I3C target bring-up on this family of silicon
tomorrow, in this order:

First, **set the I3C functional clock to 12 MHz exactly** and
verify `SCONFIG.BAMATCH` post-init against the spec's 1 µs
bus-available minimum. The HAL's formula is correct; whether the
input clock makes it produce a legal value is on you. FRO_LF /1
gives you 12 MHz exact and matches the SDK.

Second, **add a `dump_registers()` method to both controller and
target from day one** and call it after init in the examples. One
defmt line each, every `M*` / `S*` register as a raw `u32`. The cost
is trivial; the diagnostic value the first time something goes weird
against a known-good reference is disproportionate. Of course,
remember to remove that method prior to producing a PR. That method is
easy to write and only useful when things go awry. In general, we
don't need it.

Third, **mirror the vendor SDK's ordering on anything that combines
DMA, FIFO, and bus signalling** even if it looks wrong: arm DMA,
signal the bus, *then* wait for completion. Don't drain the FIFO
before raising the IBI. Don't emit a Stop after an IBI if the next
thing you expect is a directed read.

Those three would have saved me the bulk of the bring-up time.

## What this doesn't cover

One developer, one family of silicon, two boards on a bench. I have no
idea how I3C target mode looks on other vendors' IP, and I'd be
surprised if specific register names, FIFO depths, or clock-tree
dependencies transferred. The PR also contains a handful of smaller
fixes I didn't write up — a stale `MERRWARN` warning leaking across
frames, a racy `state == Slvreq` guard in the IBI wake path, a
controller-side ODHPP-vs-baud-rate calculation race — each of which
fixed a real failure but wasn't structurally interesting enough to
earn a section. Read the PR if you want the full list.

If you're bringing up I3C target mode on MCXA, I hope this saves you
the half-week. If you're doing it on something else and one of these
patterns rhymes with what you're seeing, I'd genuinely like to hear
about it.
