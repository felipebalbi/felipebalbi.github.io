+++
title = "Examples that run"
date = 2026-06-15T15:58:00
description = "What changed in my driver crates once pico de gallo moved the bus host-side: a small set of conventions for an examples/ directory that runs against real silicon from a normal dev machine, no MCU in the loop. Worked example: tmp108."
[taxonomies]
tags = ["embedded", "rust", "drivers", "pico-de-gallo", "tmp108"]
+++

Most driver crates I reach for ship an `examples/` directory that I
have to *port* before I can read it. Wrong PAC for my dev board,
wrong linker script, a `#[entry]` macro from a runtime I don't have
checked out, a `loop { wfi() }` at the end I need to delete because
I'm hosting the binary myself. By the time I've matched the example
to my hardware, I've spent an hour and I haven't learned anything
about the chip — I've learned about the crate author's bring-up
preferences for the board they happened to own.

A [previous post](/posts/writing-embedded-drivers-without-an-mcu/)
made the case for moving the bus host-side: a Pi Pico 2 acting as a
USB-attached I²C/SPI/GPIO adapter, so the driver crate itself stays
unchanged and runs from a `std` binary on your laptop. This post is
the other half. Once the bus moved, the *driver crate's* `examples/`
directory changed shape too, and the conventions that fell out are
worth writing down. The worked example is
[`tmp108`](https://github.com/OpenDevicePartnership/tmp108) — at the
time of writing, the only crate on crates.io that lists
`pico-de-gallo-hal` as a (dev-)dependency at all. Five example files,
five distinct chip behaviors, every one of them runs on a normal dev
machine as soon as you have the chip on a breadboard.

What follows is what I do, and why. None of it is novel; most of it
is the kind of thing that becomes obvious once the constraint
"every example must be runnable, by anyone, on the actual chip"
stops being aspirational.

## The opening line is the chip's pinout

Every example in `tmp108/examples/` opens with the same shape. The
`oneshot.rs` body, in full:

```rust
let hal = Hal::new();
let i2c = hal.i2c();

let mut tmp = Tmp108::new_with_a0_gnd(i2c);
let temperature = tmp.temperature().map_err(|_| anyhow!("Failed to read temperature"))?;
println!("Temperature: {temperature:.2} C");
```

The first two lines are the entire host-side preamble. They're
contract: ignore me, the rest of this file is only about the chip.
After a few examples, the reader's eye learns to skip past them and
read the rest as if it were library prose. There's no clock-tree
setup, no peripheral init, no `#[entry]` decorator, no panic handler.
The bus is just *there*, the way `std::fs::File::open` is just there
in a Linux program.

The third line is the one that does the work. `Tmp108::new_with_a0_gnd(i2c)`:
the constructor name *is* the pinout. A0 tied to GND means I²C
address `0x48`. The TMP108 takes one of four addresses depending on
the state of its A0 pin, and the driver exposes one constructor per
choice — `new_with_a0_gnd`, `new_with_a0_vplus`, `new_with_a0_sda`,
`new_with_a0_scl`. There is no `new(addr: u8)`. There is no way to
construct a `Tmp108` whose address doesn't correspond to a real
wiring choice on the part.

The example file tells you which jumper you're looking at by
*naming it in the constructor*. A reader who has the chip strapped
to V+ can see, without consulting the README, that this particular
example assumes A0 is grounded — and that swapping `_a0_gnd` for
`_a0_vplus` is the only change they need to make. The constructor
name is doing documentation work that would otherwise have lived in
a code comment, where I would have forgotten to update it the next
time I edited the file.

## One file per chip behavior, not per method

There are five files in `tmp108/examples/`: `oneshot.rs`,
`continuous.rs`, `alert_comparator.rs`, `alert_interrupt.rs`, and
`sensor_trait.rs`. Each one demonstrates a thing the TMP108 *does*
— a single-shot conversion, a continuous-conversion loop, the
ALERT pin in comparator mode, the ALERT pin in interrupt mode, and
the cross-crate `embedded-sensors-hal` trait integration. Not
`new_with_a0_gnd.rs`, `new_with_a0_vplus.rs`, `new_with_a0_sda.rs`
— that would be per-constructor, which is per-API, which is
library-shaped. Per-behavior is chip-shaped, which is the grain the
reader is here for.

The grain of the directory matches the grain of the datasheet's "Modes
of operation" chapter, not the grain of the Rust struct's `impl`
block. A reader who wants to know "how do I use the ALERT pin in
interrupt mode" goes to `alert_interrupt.rs`; a reader who wants to
know "what does the public API look like" can read the
[docs.rs](https://docs.rs/tmp108) page. Two different audiences, two
different artifacts, and they don't have to compete for space in the
same file.

The two ALERT examples are a good test of whether "chip behavior"
is genuinely the right axis. The TMP108's ALERT pin has two
operating modes that the [README's Gotchas
section](https://github.com/OpenDevicePartnership/tmp108#gotchas)
spells out: in comparator mode the pin stays asserted until the
temperature returns inside the hysteresis band; in interrupt mode
the pin clears as soon as the configuration register is read. Two
modes, two examples — not because the trait has two methods (it
doesn't; it has one `wait_for_temperature_threshold`), but because
the *chip* genuinely behaves differently in the two configurations.
The split exists because the reader who's debugging "why doesn't
my ALERT line release" needs to know which mode they configured,
and the example that demonstrates each mode is the most economical
place to learn it.

## Same file, both flavors

The TMP108 driver ships both a blocking `Tmp108` and an async
`AsyncTmp108`, gated on the `async` Cargo feature. The straightforward
thing to do would have been `oneshot_blocking.rs` and
`oneshot_async.rs`, two files, one per build configuration -- one can
certainly follow that model too. That isn't what I did. The
`oneshot.rs` file has *two* `main()` functions in it, picked between
by `cfg`:

```rust
#[cfg(not(feature = "async"))]
fn main() -> Result<()> {
    // ... blocking body
}

#[cfg(feature = "async")]
#[tokio::main]
async fn main() -> Result<()> {
    // ... async body
}
```

Why one file? Because a reader who wants to learn *what changes
when you flip to async* would, with two files, have to diff them in
their head. With one file, the diff is *in the file*, visible to the
eye, enforced by the compiler. The two preambles at the top are
identical. The chip operations are nearly identical. The only things
that change are the `.await`, the `#[tokio::main]` macro, and the
import that picks `AsyncTmp108` instead of `Tmp108`. The reader sees
it, side by side, in the smallest unit of code that demonstrates the
difference.

The trade-off is honest: the file is busier — every example that
supports both flavors carries two `main` functions and at least one
pair of `#[cfg]` arms. The payoff is that blocking/async parity is
*observable in the source*, not just claimed in the README. Building
with default features picks the blocking `main`; building with
`--features async` picks the async one. (`continuous.rs`,
`alert_comparator.rs`, and `alert_interrupt.rs` are async-only,
because the underlying API is; their blocking `main` stub prints
the required feature flags and exits, which is itself a form of
documentation — the file refuses to build silently into nothing.)

## A four-line header that tells you what you need

Every example opens with a doc-comment header that says, in the same
shape every time, who the example is for and what it requires.
`oneshot.rs`'s header in full:

```rust
//! TMP108 one-shot conversion example.
//!
//! # Hardware
//!
//! - Pico de Gallo USB-attached host adapter
//! - TMP108 on the default I2C bus, A0 → GND (address `0x48`)
//!
//! # Cargo features
//!
//! Works with default features (blocking). Building with `--features async`
//! produces the async variant.
//!
//! # Register interactions
//!
//! Single read of the temperature register at address `0x00`.
```

`# Hardware` says what pico de gallo connections the example needs:
USB-attached host adapter, TMP108 on the default I²C bus, A0
strapped to GND. `# Cargo features` says how to flip blocking/async
— default features for blocking, `--features async` for async.
`# Register interactions` names exactly which TMP108 register the
example touches, so the reader who is following along with the
datasheet open can find the relevant chapter without guessing. For
`oneshot.rs`, that's a single read of the temperature register at
address `0x00`. For `continuous.rs`, it's a five-step sequence
(read config, write config with M=Continuous, read config, loop on
temperature reads, restore M=Shutdown on exit). The header for each
example calls out the register interactions specific to that
example, in the order they happen.

This is the section the reader skims first to decide "is this the
example I want?" — a six-line table of contents per file, in the
place a Rust reader expects documentation to be. It costs nothing
to write and removes the most common pre-run question, which is
some flavor of "wait, what do I need to have plugged in for this
one?" The reader doesn't have to chase the answer through the body
of the file or the README; the answer is already at the top.

## What this doesn't cover

A few honest limits before I oversell any of this.

The pattern works because pico de gallo can host an I²C bus, an SPI
bus, and a handful of GPIOs from a laptop. Drivers that need a real
MCU peripheral — USB device, Ethernet MAC, anything DMA-bound,
anything where the chip itself drives timing the host can't
sustain — can't run this way. The conventions in this post are
opinionated *about* I²C/SPI/GPIO sensor and peripheral drivers; I
have not tried any of this on a driver that needs more than those
buses, and I would not assume any of it generalizes.

`tmp108` is also one crate. At the time of writing it is the *only*
crate on crates.io that depends on `pico-de-gallo-hal` — [the
reverse-deps
page](https://crates.io/crates/pico-de-gallo-hal/reverse_dependencies)
lists exactly one, and it's a dev-dependency at that. These
conventions are battle-tested on one chip[^mcxa], with one set of bus
requirements, by one author. Take them as one person's house style,
not as a recommendation that has survived contact with a population of
users and other crates.

What I do think holds up, on the evidence of this one crate, is the
shape: the constraint "every example must run on a normal dev
machine against real silicon, with no porting step in between"
forces an `examples/` directory that reads like the chip's
datasheet, not like the library's `impl` block — and the
datasheet's grain is the right grain for the audience the examples
are written for.

[^mcxa]: That statement is not exactly true as I have used Pico de
Gallo to run weekend long soak tests on MCXA I²C controller and target
drivers.
