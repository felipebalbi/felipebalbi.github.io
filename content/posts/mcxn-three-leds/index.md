+++
title = "All that work to blink three LEDs"
date = 2026-11-18
draft = false
description = "Initial MCXN support has landed in Embassy. Behind the three-LED blinky are peripheral models, debugger targets, conservative clock setup, and a discussion about how much of NXP's hardware really belongs in one HAL."
[taxonomies]
tags = ["embedded", "rust", "embassy", "nxp", "mcxn", "drivers"]
+++

The application has three outputs and a loop. Turn an LED on, wait half a
second, turn it off, try another color. The sort of program you write to
prove the board is alive before doing anything interesting.

Getting to that program involved changes to a peripheral access crate,
debugger target descriptions, clock initialization, GPIO, and the code
that generates the HAL's peripheral implementations. It also prompted a
discussion about whether we should have one HAL for rather more of NXP's
microcontrollers.

[Initial MCXN support landed in Embassy yesterday][hal-pr]. The visible
result is an RGB blinky on an MCXN556S. The interesting part is everything
the blinky does not tell you.

<!-- more -->

## First, describe the thing you want to program

There are several different meanings of "support" in an embedded Rust
stack, and they do not arrive together.

A peripheral access crate, or PAC, describes registers: their addresses,
fields, and the values those fields accept. A HAL turns that description
into operations an application can use. The debugger needs its own
description of the target so it can connect and program it. Having one of
these does not conjure the others into existence.

The MCXN work included [peripheral models in `nxp-pac`][models-pr], followed
by [MCXN246, MCXN556, and MCXN557 MetaPAC selections][chips-pr]. The latter
is a useful example of why adding a part number is not just copying a
file and changing its name: the N556/N557 descriptions need **DAC1 instead
of PLU**, with the corresponding peripheral and interrupt availability.

That work brought the PAC to **14 numeric MCXN chip selections**. Numeric
is important here. Reusing register definitions for suffix variants with
matching maps does not say that their boot policy, memory capacity,
electrical limits, or errata are interchangeable. Nor does a chip feature
tell you which pins are bonded out on your package. The application's
linker layout and pin choices still have to match the actual device.

On the debugger side, [probe-rs gained MCX N target support][probe-pr],
followed by [package-variant descriptions][packages-pr]. Those are
separate contributions from the Embassy HAL. A target being recognizable
to the debugger is useful, but it is not evidence that an async driver
for one of its peripherals exists.

This is the infrastructure underneath the line that looks innocuous in
the application:

```rust
let p = hal::init(hal::config::Config::default());
```

It is a short line with a fairly large dependency on reality.

## The clock configuration is deliberately uninteresting

The initial MCXN profile uses **FRO12 at 12 MHz**, a **3 MHz slow bus**, and
the **1 MHz OSTIMER** clock for Embassy's time driver. No ambitious clock
tree configuration, no voltage scaling, no MCXN deep-sleep support in
this first step.

That is not a statement about what the chips can do. It is the boundary
of what this implementation currently offers.

The [clock implementation][clock-source] checks the numeric device type
against the selected chip and, on dual-core parts, refuses to proceed
unless CPU1 is held in reset. The initial port is for the primary core,
not an experiment in coordinating two running cores through a shared
clock controller.

Startup waits also have a finite polling budget. An oscillator or divider
that never reaches the expected state produces a phase-specific error
rather than an infinite loop with no indication of which step stopped.
This is a polling budget, **not a wall-clock timeout**: there is no
initialized Embassy timer to consult while establishing the clocks that
will feed it.

The reported frequencies come from the selected sources and divider
readback. They are nominal frequencies, not measurements against an
independent reference. That distinction matters even when the answer is
the thoroughly unsurprising number twelve million.

The other deliberate choice is what initialization leaves alone. It
retains inherited flash wait states, RAM ECC settings, and
security/calibration state. The boot environment must already provide
usable SRAM, including whatever ECC initialization its memory layout
requires. This HAL does not turn a minimal clock profile into an excuse
to rewrite every vaguely related register.

## Share the GPIO operations, not the assumptions

The data side of GPIO is a good candidate for reuse. Setting, clearing,
toggling, and reading a pin can share the same driver wrappers when the
underlying register interface permits it.

Pin control is where that simple picture stops being sufficient.

The port separates [shared GPIO types and operations][gpio-source] from
the family-specific code. MCXN has [native per-pad PCR accessors][pads-source],
rather than simply using the MCXA uniform PORT/PCR model everywhere.
That dispatch stays in the MCXN pin-control implementation, where it
belongs, instead of spreading family checks through every public output
method.

For this first step, the implemented MCXN GPIO pins are **P0_10, P0_27,
and P1_2**: the three RGB outputs used by the example. MCXN23x lacks
P0_10, so that selection gets only the other two. The generator does not
advertise a GPIO implementation for every pin just because pin metadata
exists. Nor does this initial MCXN path claim async GPIO interrupt
support.

There is a similar restraint during initialization. GPIO and PORT clocks
are enabled without resetting the whole blocks. Resetting an entire
block to prepare three LED pins would also discard inherited protection
and pad state outside those pins. The implementation requires the blocks
to be out of reset already, and it does not override access policy to
make a denied pin usable.

The resulting application still gets the familiar interface. From the
[merged blinky example][blinky-source]:

```rust
let mut red = Output::new(p.P0_10, Level::High, DriveStrength::Normal, SlewRate::Fast);
let mut green = Output::new(p.P0_27, Level::High, DriveStrength::Normal, SlewRate::Fast);
let mut blue = Output::new(p.P1_2, Level::High, DriveStrength::Normal, SlewRate::Fast);
```

After all that, the loop really does mostly consist of setting levels
and calling `Timer::after_millis(500).await`.

I would like the application to be boring. I do not want the code below
it to achieve that by pretending the differences are not there.

## Does this need another HAL?

[Kevin Lannen asked the obvious question][family-question]: should MCXN
live inside `embassy-mcxa`, or should it have its own crate? The families
share some peripherals, but certainly not all of them. Kevin also linked
[his MCXN947 GPIO and blinky work][kevin-pr] in the discussion.

[My answer was to keep the work together][family-answer]. If the approach
works well, renaming the crate to `embassy-mcx` would be a reasonable
follow-up. That is a direction, not a rename this PR performed: the
merged example still imports `embassy_mcxa`.

Then [9names suggested going further][nxp-question]: why not put the NXP
families under `embassy-nxp`? Kevin [listed concrete overlaps][overlaps],
including USB controller relationships across MCXN, i.MX RT, LPC55, and
RT600, and shared peripherals such as CTIMER, OSTIMER, WWDT, PINT, and
ADC between MCXN and LPC55.

That is a much better basis for a shared HAL than the vendor logo on the
front of the datasheet. It is also not a reason to do the whole merger
in one patch.

[I want to move in that direction][caution], but we already have working
deep sleep on MCXA. Moving everything immediately risks regressing
behavior that is useful today in exchange for an architecture we hope
will be useful later.

The [sequence I proposed in the discussion][sequence] starts below the
HAL: move toward the MetaPAC approach in `nxp-pac`, review the peripheral
models so corresponding hardware produces consistent helpers, update
the existing HALs to those accessors, and then tackle bringing them
together.

There is already a small piece of that work in [the MCXA/MCXN fieldset and
enum consolidation][sharing-pr]. It shares compatible descriptions while
preserving layouts, sparse pin capabilities, access policies, defaults,
and driver identities. The qualification is the work. Sharing a field
description is useful; erasing a hardware distinction to make two files
look alike is not.

Even compilation cost appeared in the review. Kevin [asked about
building representative families][build-discussion] to reduce memory
use, and [reported a local comparison][memory-discussion] in which
disabling `defmt` reduced a PAC build from roughly 2.1 GB to 1.5 GB.
Those are his benchmark figures, not a general memory guarantee, but
they are a useful reminder that generated APIs have a host-side cost,
too. [Removing unnecessary one-bit enums][enums-pr] was another recent
change explicitly aimed at compilation memory usage.

## What actually landed

The [merged support notes][support-source] describe a deliberately narrow
starting point:

- Fourteen numeric MCXN chip selections, covering the N23x, N24x, N52x,
  N53x, N54x, N55x, and N94x device groups.
- A fixed primary-core clock profile, OSTIMER time driver, and the small
  GPIO subset above. Other MCXN peripherals are not supported yet.
- **Hardware validation on MCXN556S only.** Other chip selections are
  not a claim of testing on those devices.
- The ordinary `embassy-executor/platform-cortex-m` executor. The HAL's
  `executor-platform` and `time-driver-lptmr` features are not supported
  on MCXN.

The example is also a **plain, unsigned image**. Whether it can start
autonomously depends on the device's boot policy. A secure-boot-enabled
device needs an appropriately authenticated image; HAL initialization
does not change that policy. Debug-mailbox mass erase is a separate,
destructive maintenance operation, not a hidden step in making the
example run.

These are not apologetic qualifications attached to an otherwise broad
claim of "MCXN support." They are the claim. The port gives the next
peripheral a defined place to start, without making the application
responsible for discovering which existing MCXA assumptions do not
apply.

Three LEDs are not much of an application. They are enough to exercise
the path from a chip selection through generated register access,
initialization, GPIO, and an async timer to something visible on the
board.

That seems like a useful first step. The rest can arrive without having
been promised by the blinky.

[hal-pr]: https://github.com/embassy-rs/embassy/pull/7520
[models-pr]: https://github.com/embassy-rs/nxp-pac/pull/146
[chips-pr]: https://github.com/embassy-rs/nxp-pac/pull/147
[probe-pr]: https://github.com/probe-rs/probe-rs/pull/4628
[packages-pr]: https://github.com/probe-rs/probe-rs/pull/4691
[sharing-pr]: https://github.com/embassy-rs/nxp-pac/pull/149
[enums-pr]: https://github.com/embassy-rs/nxp-pac/pull/150
[clock-source]: https://github.com/embassy-rs/embassy/blob/41d7b34f69fa530d30439516dced774eedebcd02/embassy-mcxa/src/clocks/mcxn.rs
[gpio-source]: https://github.com/embassy-rs/embassy/blob/41d7b34f69fa530d30439516dced774eedebcd02/embassy-mcxa/src/gpio/mod.rs
[pads-source]: https://github.com/embassy-rs/embassy/blob/41d7b34f69fa530d30439516dced774eedebcd02/embassy-mcxa/src/gpio/mcxn.rs
[blinky-source]: https://github.com/embassy-rs/embassy/blob/41d7b34f69fa530d30439516dced774eedebcd02/examples/mcxn556/src/bin/blinky.rs
[support-source]: https://github.com/embassy-rs/embassy/blob/41d7b34f69fa530d30439516dced774eedebcd02/embassy-mcxa/README.md#initial-mcxn-support
[family-question]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6859101616
[family-answer]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6860533357
[kevin-pr]: https://github.com/embassy-rs/embassy/pull/7521
[nxp-question]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6935882247
[overlaps]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6938098403
[caution]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6957947139
[sequence]: https://github.com/embassy-rs/embassy/pull/7520#issuecomment-6958018934
[build-discussion]: https://github.com/embassy-rs/embassy/pull/7520#discussion_r4592411847
[memory-discussion]: https://github.com/embassy-rs/embassy/pull/7520#discussion_r4595066404
