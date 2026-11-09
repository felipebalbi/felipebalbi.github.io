+++
title = "Pico de Gallo: UART Framing, Zephyr PWM, and Better Recovery"
date = 2026-11-09
draft = false
description = "Schema 0.8 brings configurable UART framing, Zephyr UART and PWM controllers, firmware build identity, and recovery from stuck dispatchers. The coordinated Rust crates are on crates.io."
[taxonomies]
tags = ["rust", "embedded", "pico-de-gallo", "zephyr", "release"]
+++

The next [Pico de Gallo][book] release is out. All six published Rust
crates are on crates.io, with wire schema **0.8** and firmware version
**0.12.0**. The project still does the same job: put real peripheral
buses behind a USB connection, so you can develop drivers on your laptop
instead of rebuilding MCU firmware for every experiment.

This round adds UART framing, two more Zephyr controllers, and some
important work on what happens when a call does not come back.

<!-- more -->

## UART beyond 8N1

UART configuration now includes data bits, parity, and stop bits, not
just baud rate. The CLI, Rust library, C bindings, Python bindings, and
MCP tools expose the new settings. For example, 9600 baud, seven data
bits, even parity, and two stop bits:

```sh
gallo uart set-config --baud-rate 9600 \
    --data-bits 7 --parity even --stop-bits 2
```

The supported range is five to eight data bits, none/odd/even/mark/space
parity, and one or two stop bits. UART requires revision 2 hardware.
Configuration replaces the whole setting, and the transition is not
atomic: stop traffic before changing it. Omitting the framing flags
selects 8N1 rather than preserving the previous framing.

## More Zephyr, less scaffolding

The [Zephyr module][zephyr] now has a **polling UART controller** and a
**PWM controller**, alongside I²C, SPI, and GPIO. There are UART bridge
and PWM LED-fade samples, plus recording-fake tests that run without a
board attached.

PWM exposes four channels and accounts for the RP2350's shared slices:
asking for conflicting periods on sibling channels is refused rather
than silently changing another output. The UART controller is polling
and thread-context-only, not an interrupt-driven or DMA interface.

The point remains the same as in the [earlier Zephyr post][previous]:
develop an ordinary Zephyr driver as a host process while the hardware
on the other side of the bus is real.

## Calls that finish, and builds you can identify

- **Bounded host calls.** Every RPC now has a deadline. Long-running
  operations get appropriate bounds rather than all sharing one short
  timeout. A timeout does not prove a write never executed; do not retry
  a state-changing operation blindly.
- **Dispatcher-progress supervision.** The firmware watchdog no longer
  treats a running executor as proof that requests are making progress.
  An expired dispatch budget forces a reset. That recovery was tested
  against a deliberately wedged handler; it is not a promise to detect
  every possible hang.
- **Transfer-size checks before transmission.** Oversized reads and
  batches now get useful size errors instead of truncated responses or
  silence. Batch response limits are checked before bus side effects.
- **Firmware build identity.** `gallo version` reports a git-derived
  build ID, also available through C, Python, and MCP. It answers which
  image is running, not merely which version it claims to be.

## Upgrade both sides

This is **not a backwards-compatible wire upgrade**. Install matching
host tools and firmware together. In particular, adding build identity
changed the `device/info` endpoint key: mixing schema 0.7 and 0.8 can
produce a validation timeout instead of a helpful version mismatch.
Rust UART configuration signatures and the C UART ABI changed too; C
consumers need to rebuild against the new header and library.

The [release and compatibility guide][compatibility] lists the complete
version set. The [repository releases][releases] carry the artifacts.

[book]: /pico-de-gallo/
[zephyr]: https://github.com/OpenDevicePartnership/pico-de-gallo/tree/main/zephyr
[previous]: @/posts/pico-de-gallo-schema-07-zephyr/index.md
[compatibility]: /pico-de-gallo/internals/releases.html
[releases]: https://github.com/OpenDevicePartnership/pico-de-gallo/releases
