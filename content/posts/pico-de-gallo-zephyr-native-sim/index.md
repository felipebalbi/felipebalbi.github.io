+++
title = "Pico de Gallo: Real Buses for Zephyr's native_sim"
date = 2026-08-19T09:00:00
draft = false
description = "A work-in-progress Zephyr module that gives native_sim real I²C and SPI over USB, so unmodified upstream Zephyr drivers run as an ordinary process on your laptop against actual silicon — an in-tree TMP117 read through the sensor API, and a GD25Q16 brought up by the stock jedec,spi-nor driver."
[taxonomies]
tags = ["rust", "embedded", "pico-de-gallo", "zephyr", "drivers"]
+++

[Pico de Gallo][book] turns an RP2350 into a USB-attached bridge so a
host program can drive real I²C, SPI, UART, GPIO, PWM, ADC and 1-Wire
without cross-compiling or flashing anything. Zephyr has always had
real buses, of course. But `native_sim` — the board you build when you
want a Zephyr application to run as an ordinary process on your laptop
— has not. There is now a Pico de Gallo module that gives it some.

<!-- more -->

## The shape of it

The module is on `main`. It ships a `pico_de_gallo` shield whose I2C
and SPI drivers forward Zephyr's subsystem calls straight through
[`pico-de-gallo-ffi`][ffi] to a board plugged into your laptop. The
Zephyr board is `native_sim/native/64` — nothing is cross-compiled,
nothing is flashed. Corrosion builds the FFI as a `staticlib` during
the Zephyr build and links it into the native_simulator runner, so the
`zephyr` binary carries the USB transport inside it: no shared object
to keep alongside it, no runtime library path to get right.

## Unmodified upstream drivers

That is the part worth the effort. `samples/i2c_bridge` reads a TI
TMP117 through Zephyr's sensor API, using the in-tree `tmp117` driver
as-is:

```text
Temperature: 23.500000 C
Temperature: 23.507812 C
```

`samples/spi_nor_id` does the same on SPI, bringing up a GigaDevice
GD25Q16 with the stock `jedec,spi-nor` driver and reading SFDP through
the generic flash API:

```text
<inf> spi_nor: nor@0: SFDP v 1.0 AP ff with 2 PH
<inf> spi_nor: nor@0: 2 MiBy flash
Flash device ready: nor@0
size:     2048 KiB
@000000:  FF 00 00 FF 7E AA 99 7E 51 00 01 05 92 00 20 62
```

Those bytes are an iCE40 bitstream, read off real flash, by a Zephyr
application running as a native process on the host. The sample is
deliberately read-only: its overlay omits every write-capable
devicetree gate, so initialisation issues nothing but `RDID` and
`RDSFDP`. Both status registers read `0x00` before and after, and the
flash contents were byte-identical across the run.

## What reviewing it turned up

Reading the module carefully produced six issues, and all six are now
closed and merged. The sharpest was [#103][i103]: the SPI driver left
`transceive_async` and `iodev_submit` NULL. Zephyr's I2C subsystem
NULL-checks its optional ops; SPI does not. That was a segfault waiting
for anyone who enabled `CONFIG_SPI_RTIO` — which arrives transitively
via `CONFIG_SENSOR_ASYNC_API`, so without asking for it. It is now a
`BUILD_ASSERT`, which turns an unsupported configuration into a build
failure that names itself instead of a crash at runtime. [#111][i111]
had the SPI driver calling `k_malloc` while depending on nothing that
provides a heap, so any application that did not set
`CONFIG_HEAP_MEM_POOL_SIZE` by hand failed to link. [#105][i105] was a
pair of format-string defects in I2C error paths.

The other three were seams rather than code. [#108][i108]: the build
hard-coded `libpico_de_gallo_ffi.so` and assumed an ELF host, which is
how the `staticlib` decision above got made — macOS and Windows are
supported `native_sim` hosts too. [#107][i107]: the module fetched the
FFI crate *from crates.io* despite living in the same repository as it,
so in-tree FFI edits changed nothing and the pin was invisible to
`cargo-deny` and the lockfile job. [#106][i106]: the generated C header
exported no constants and no config enums, so every C consumer
hard-coded magic numbers — `GALLO_NUM_GPIOS` and friends are exported
now.

## Where it stands

Coming along, and not done. There is no CI and no test suite yet. It
wants Zephyr `main` rather than a release, because the SPI driver reads
`spi_config` fields that no tagged version has. Two of the four samples
cannot be built at all, since the IS31FL3743B driver they want is not
upstream.

Chip select is the honest wart. Today the SPI driver forwards the child
node's `reg` straight to the firmware as a GPIO pin index, so every
transaction reconfigures whichever board GPIO that number happens to
name — regardless of what you have wired there ([#104][i104]). The work
in flight restructures the module around a multi-function device: an
`odp,pico-de-gallo` parent owning the single USB handle, with `-i2c`,
`-spi` and a new `-gpio` child beneath it, and chip select moved to
ordinary `cs-gpios` pointing at that GPIO sibling. That earns Zephyr's
own CS handling for free, and makes a devicetree wiring chip select to
a *different* board fail the build rather than assert CS on one board
while clocking data on another. Not merged yet.

What is merged works against real silicon, with drivers nobody wrote
for this. Upstreaming the module proper is tracked in [#98][i98].

## Where to look

The module does not have a chapter in the book yet. Until it does, the
[module README][zephyr-readme] is the guide: prerequisites, workspace
setup, both working samples, and a frank list of driver limitations.

- docs: the [Pico de Gallo book][book] · source: [the repo][repo]
- the C API the Zephyr drivers call: [`pico-de-gallo-ffi`][ffi]
- underneath it: [`pico-de-gallo-lib`][lib] ·
  [`pico-de-gallo-internal`][internal]

[book]: /pico-de-gallo/
[repo]: https://github.com/OpenDevicePartnership/pico-de-gallo
[zephyr-readme]: https://github.com/OpenDevicePartnership/pico-de-gallo/blob/main/zephyr/README.md
[ffi]: https://crates.io/crates/pico-de-gallo-ffi
[lib]: https://crates.io/crates/pico-de-gallo-lib
[internal]: https://crates.io/crates/pico-de-gallo-internal
[i98]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/98
[i103]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/103
[i104]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/104
[i105]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/105
[i106]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/106
[i107]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/107
[i108]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/108
[i111]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/111
