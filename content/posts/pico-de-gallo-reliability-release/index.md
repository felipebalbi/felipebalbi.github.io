+++
title = "Pico de Gallo: Release Announcement"
date = 2026-06-22T09:00:00
description = "A coordinated, lockstep release across all seven Pico de Gallo crates. The wire schema bumped, so firmware and host upgrade together. Most of the work came out of a reliability review — bounded GPIO waits, a firmware watchdog, subscription-leak recovery, and stricter schema validation."
[taxonomies]
tags = ["rust", "embedded", "pico-de-gallo", "release", "embassy"]
+++

[Pico de Gallo][book] turns an RP2350 into a USB-attached bridge that
lets a host program drive real I²C, SPI, GPIO, PWM, ADC, UART, and
1-Wire from `std` Rust, C, or Python — so you can write and test
device drivers on your laptop instead of cross-compiling for an MCU
every time. Today's release moves the whole ecosystem forward at once.

This is a **lockstep release**. The wire protocol in
`pico-de-gallo-internal` went from schema 0.5 to 0.6, and under the
pre-1.0 schema-versioning rule that is a breaking change. **Firmware
and host must be upgraded together** — a 0.10 firmware will reject an
older host's RPCs, and a new host will refuse to talk to old firmware
rather than silently mis-decode bytes on the wire. The
[upgrade notes](#upgrade-and-compatibility) at the bottom have the full
version table.

Most of what landed here comes out of a reliability review that turned
up a handful of real bugs: a GPIO wait could wedge the entire firmware
dispatcher, a hung handler had no recovery path, GPIO subscriptions
leaked when a host process crashed, and a bumped schema *major* could
slip past validation and corrupt decoding silently. Each crate's share
of the fix is below.

[book]: /pico-de-gallo/

## `pico-de-gallo-internal` 0.6.0 — the wire protocol

This is the crate every other one depends on, and the breaking schema
bump that drives the lockstep.

- `GpioWaitRequest` gained a `timeout_ms: u32` field, used by all five
  `gpio/wait-*` endpoints (`wait-high`, `wait-low`, `wait-rising`,
  `wait-falling`, `wait-any`). A value of `0` preserves the old
  wait-forever behavior; a non-zero value bounds the firmware-side wait
  and returns the new `GpioError::Timeout` on expiry. This is the wire
  half of the fix for the dispatcher wedge — a wait on a pin that never
  transitions used to block *every other endpoint* until you
  power-cycled the board.
- New `system/reset-subscriptions` endpoint (request `()`, response
  `u8` count). GPIO subscriptions are server-side state that outlives
  the USB transport, so a host that crashed without sending
  `gpio/unsubscribe` would strand those pins until a power cycle. This
  endpoint is the recovery path.

Both changes are append-only on the wire, but the schema-version bump
itself is what requires the coordinated upgrade.

## `pico-de-gallo-lib` 0.6.0 — the Rust host library

- New `gpio_wait_for_{high,low,rising_edge,falling_edge,any_edge}_with_timeout`
  methods take a `std::time::Duration` and return
  `Err(PicoDeGalloError::Endpoint(GpioError::Timeout))` on expiry. The
  existing two-argument methods keep waiting forever by sending
  `timeout_ms: 0`.
- New `system_reset_subscriptions()` method returns the number of
  subscriptions it reset. The recommended connect sequence is now
  `new()` → `validate().await?` → `system_reset_subscriptions().await?`.
- `validate()` now checks `schema_major` in addition to
  `schema_minor`. Previously a firmware reporting a bumped major with a
  matching minor would pass validation and the host would then
  mis-decode wire bytes — silent garbage out. `ValidateError::SchemaMismatch`
  now carries `expected_major` / `actual_major`, and its `Display`
  shows the full `MAJOR.MINOR.x` skew.
- Fixed: `validate()` no longer mis-classifies transport,
  postcard-decode, and frame-size errors as
  `ValidateError::LegacyFirmware`. Only the postcard-rpc "no handler
  for that key" signals (`UnknownKey`, `KeyTooSmall`) map to
  `LegacyFirmware`; everything else routes to `Comms`, so you stop
  being told to upgrade firmware that is already current.
- `MAX_BATCH_OPS` and `MAX_TRANSFER_SIZE` are now re-exported, so you
  don't have to depend on the wire crate just to validate batch sizes.

## `pico-de-gallo-hal` 0.6.0 — the `embedded-hal` layer

- New `Hal::new_validated()` and `Hal::new_validated_with_serial_number()`
  constructors call `validate()` before returning, failing loudly on a
  disconnected device or a schema mismatch. The lazy `Hal::new()` still
  defers failures to the first RPC if that's what you want. A standalone
  `Hal::validate()` accessor lets you check after the fact.
- New `Hal::system_reset_subscriptions() -> Result<u8, SystemHalError>`
  exposes the subscription teardown that previously required dropping
  down to `pico-de-gallo-lib`. Recommended right after
  `new_validated()` in any app that uses GPIO subscriptions.
- New `Gpio::wait_for_*_with_timeout` inherent async methods accept a
  `Duration` and return `GpioError::Timeout` on expiry. They're
  inherent methods rather than trait methods because
  `embedded-hal-async`'s `Wait` trait has no notion of a timeout; the
  trait methods keep their wait-forever semantics.
- `AdcChannel`, `AdcConfigurationInfo`, `GpioDirection`, `GpioEdge`,
  and `GpioPull` are now re-exported — driver authors no longer need
  `pico-de-gallo-lib` in their `Cargo.toml` just for these types.
- New `HalInitError` and `SystemHalError` types, and a fix for a stale
  doc-comment that referenced a `Hal::uart_set_config` method that
  never existed.

## `pico-de-gallo-ffi` 0.7.0 — the C bindings

- New `gallo_init_strict()` and `gallo_init_strict_with_serial_number()`
  call `validate()` internally and return `NULL` on device-not-found,
  schema mismatch, or legacy firmware. Prefer these over the lazy
  `gallo_init` in production C — failures surface at construct time
  instead of on the first RPC.
- New `gallo_gpio_wait_for_{high,low,rising_edge,falling_edge,any_edge}_with_timeout_ms`
  functions. `timeout_ms == 0` keeps the wait-forever behavior;
  non-zero bounds it and returns `Status::GpioTimeout` (`-70`). These
  need firmware schema 0.6+; older firmware returns
  `Status::SchemaMismatch`.
- New `gallo_system_reset_subscriptions()` with
  `SystemResetSubscriptionsFailed` (`-69`).
- The high-throughput primitives `gallo_spi_transfer`, `gallo_spi_batch`,
  and `gallo_i2c_batch` are now reachable from C, via the tagged structs
  `GalloSpiBatchOp` / `GalloI2cBatchOp`. On a per-operation failure an
  optional `out_failed_op` receives the zero-based index of the failing
  op. New status codes: `I2cBatchFailed` (`-66`), `SpiBatchFailed`
  (`-67`), `SpiTransferFailed` (`-68`). The wire protocol is unchanged
  here — this is pure FFI surface over existing endpoints.
- All `gallo_*` functions now take `const PicoDeGallo *` for the device
  handle. The ABI is unchanged, but C consumers that previously cast
  away `const` on every call can drop those casts, and headers built
  with `-Wcast-qual` stop warning. The handle remains `Send + Sync` and
  interior-mutable.

## `pyco-de-gallo` 0.4.2 — the Python bindings

- New `pyco_de_gallo.open_strict()` and
  `open_strict_with_serial_number(serial_number)` call `validate()`
  before returning the handle and raise `RuntimeError` on
  device-not-found, schema mismatch, or legacy firmware. Prefer these
  over the lazy `open()` in production Python.
- New `gpio_wait_for_*_with_timeout(timeout_ms: int)` methods — `0`
  waits forever, non-zero raises `RuntimeError` on `GpioError::Timeout`.
  Requires firmware schema 0.6+.
- New `system_reset_subscriptions()` returns an `int`.

## `gallo` (CLI) 0.7.0 — the command-line tool

- `gallo` now calls `validate()` at the top of every subcommand except
  `list` and `version`. A schema-version mismatch is reported up front
  with an actionable message that points at `gallo version` and tells
  you to re-flash the firmware or install a matching `gallo`, instead
  of surfacing as a confusing `CommsFailed` on the first RPC. `list` is
  exempt because it doesn't touch a connected device; `version` is
  exempt because it *is* the diagnostic that reports schema skew.
- Everything else is unchanged. The existing `gpio` subcommands
  (`get`, `put`, `set-config`, `monitor`) keep working. The CLI doesn't
  expose `gpio wait-for-*` subcommands, so bounded waits stay available
  through the Rust, C, and Python libraries.

## `pico-de-gallo-firmware` 0.10.0 — the device

- `gpio_wait_for_*` handlers now honor the per-request `timeout_ms`.
  Non-zero values wrap embassy's `wait_for_*_edge()` future in
  `embassy_time::with_timeout(...)` and return `GpioError::Timeout` on
  expiry; `0` keeps the pre-0.6 wait-forever behavior.
- An embassy-rp watchdog is now enabled at a 2-second timeout, fed
  every 800 ms by a dedicated `watchdog_feeder_task`. It's a separate
  task on purpose — a wedged handler can't be trusted to feed a
  handler-based scheme — so the device recovers from any future handler
  hang. `pause_on_debug(true)` keeps debugger sessions from resetting
  the chip.
- `i2c_scan_handler` now wraps each per-address probe in a 50 ms
  timeout, so one slow-to-NAK address no longer burns the whole scan
  budget.
- New `system/reset-subscriptions` handler iterates the GPIO monitor
  slots, signals each live one to stop, awaits the pin back from its
  monitor task, and returns it to the context. It's idempotent and
  cheap when nothing is subscribed — the device-side half of the
  subscription-leak recovery.

Together these close the dispatcher-wedge regression (a `gpio_wait` on
a never-transitioning pin blocking every other endpoint), the
no-recovery-from-a-hung-handler gap, and the worst-case impact of a
flaky I²C bus on `i2c_scan`.

## Upgrade and compatibility

Because the wire schema changed, **flash the new firmware and update
your host crate in the same step**. Mixed versions won't talk to each
other — by design, the new validation refuses rather than mis-decodes.

| Crate                    | Old    | New     |
| ------------------------ | ------ | ------- |
| `pico-de-gallo-internal` | 0.5.0  | 0.6.0   |
| `pico-de-gallo-lib`      | 0.5.0  | 0.6.0   |
| `pico-de-gallo-hal`      | 0.5.0  | 0.6.0   |
| `pico-de-gallo-ffi`      | 0.6.0  | 0.7.0   |
| `gallo` (CLI)            | 0.6.0  | 0.7.0   |
| `pyco-de-gallo`          | 0.2.0  | 0.4.2   |
| `pico-de-gallo-firmware` | 0.9.0  | 0.10.0  |

After flashing, point the host at the device and confirm the schema
lines up:

```console
$ gallo version
```

For new code, reach for the validating entry points so a version skew
or a missing board fails at construct time rather than on the first
call:

- Rust library: `PicoDeGallo::new()` → `validate().await?` →
  `system_reset_subscriptions().await?`
- HAL: `Hal::new_validated()`, then `system_reset_subscriptions()`
- C: `gallo_init_strict()`
- Python: `pyco_de_gallo.open_strict()`

If you hit a schema-mismatch error after upgrading only one side, that
mismatch is the new validation doing its job. Re-flash or re-install so
both ends report the same schema, and you're good.
