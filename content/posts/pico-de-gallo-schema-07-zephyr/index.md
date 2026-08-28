+++
title = "Pico de Gallo: Schema 0.7, and a Zephyr Module That Grew Up"
date = 2026-08-28T09:00:00
draft = false
description = "Eight crates tagged in lockstep for wire schema 0.7. The Zephyr module got the work that turns it from a demo into somewhere you can actually write the driver for a part that does not have one yet — an MFD parent, a real GPIO controller, standard cs-gpios, i2c_burst_write, and its first CI — and the review underneath it turned up four genuine bugs, including an i2c/batch that wrote to a register the caller never named and an empty I²C write that wedged the whole board while the watchdog kept feeding it."
[taxonomies]
tags = ["rust", "embedded", "pico-de-gallo", "zephyr", "release", "drivers"]
+++

[Pico de Gallo][book] turns an RP2350 into a USB-attached bridge so a
host program can drive real I²C, SPI, UART, GPIO, PWM, ADC and 1-Wire
without cross-compiling or flashing anything. Yesterday all eight
wire-coupled crates moved at once, for wire schema 0.7.

The cycle started as housekeeping. The [Zephyr module][zephyr-post] I
wrote about nine days ago worked, and it had one honest wart I said I
would fix: chip select forwarded a devicetree `reg` straight to the
firmware as a GPIO pin index. Fixing that properly meant restructuring
the module around a multi-function device, which meant writing a real
GPIO controller, which meant reading the firmware's chip-select path
carefully — and that is where the bugs were. Four of them, in code
that had nothing to do with Zephyr. One had been silently writing to
the wrong register since April. Another had been panicking on every
single invocation for just as long.

<!-- more -->

This is a **lockstep release**. `pico-de-gallo-internal` went from
schema 0.6 to 0.7, and under the pre-1.0 schema-versioning rule that
is breaking, so **firmware and host must be upgraded together**. The
[upgrade notes](#upgrade-and-compatibility) at the bottom have the
version table and the list of things that will fail to compile.

The Zephyr module is not in that table. It has no release vehicle —
it is not a crate, it carries no tag, and its `CHANGELOG.md` is still
one large `## [Unreleased]` section. That is deliberate, and it is
also where most of the work went, so it goes first.

## The Zephyr module

The demo that gets attention is the one where an unmodified upstream
driver runs against real silicon from a laptop — a TMP117 read through
Zephyr's sensor API, a GD25Q16 brought up by the stock
`jedec,spi-nor`. That demo matters, but only as evidence. Those
drivers already existed and already worked; running them proves the
bridge is faithful and nothing more.

The point is the drivers that do *not* exist yet.

Say you have a part nobody has written a driver for. A new sensor, an
odd flash, an LED matrix controller whose datasheet reads like a
translation. The normal loop for writing that driver is:
cross-compile, flash, run, watch it do nothing, add a `printk`,
cross-compile, flash, run. Every iteration costs a toolchain, a
bootloader, a board and usually a debug probe — and when it fails you
get to wonder whether the fault is in your driver, your devicetree,
your Kconfig, your wiring, or the part.

Through this module that loop is:

```console
$ west build -b native_sim/native/64 -- -DSHIELD=pico_de_gallo
$ ./build/zephyr/zephyr.exe
```

The driver under development is an ordinary process on your laptop.
You can break on `pin_configure` in gdb the way you would in any
program, `printf` from anywhere, or run the whole thing under ASan.
Meanwhile the bus below it is real, the part is real, and the bytes on
the wire are the bytes the datasheet is describing. The only MCU
involved is the one inside the bridge, and it already has its
firmware — you never compile for it, flash it, or think about it.

The same argument is what the Rust side of the project has always been
for; that is the `embedded-hal` implementation and the reason a
`no_std` driver crate can be exercised from a `std` host binary. The
Zephyr module extends it to the other ecosystem where writing a driver
means writing scaffolding: bindings, Kconfig, an API vtable, init
priorities. That scaffolding is normally debugged at the same time as
the hardware, which is exactly why it is so unpleasant. Here the bus
is known-good, so a failure is yours.

And that is why this release looks the way it does. A module that only
demonstrates existing drivers can get away with a bespoke devicetree
and a private property for chip select. A module you *author* against
cannot. Everything you learn on it has to be true of real hardware, or
you will write a driver that works beautifully on my bridge and dies
on a real SPI controller — which is worse than no bridge at all,
because it costs you the debugging twice. Nearly every change below is
that one principle applied.

Nine days ago the module was, in its own words, "coming along, and not
done." It had no CI, no tests, one structural wart, and two of four
samples that could not build.

It now has a devicetree shape that is recognisably Zephyr's rather
than mine, a GPIO controller, standard `cs-gpios`, an I²C driver that
accepts the message shapes Zephyr's own helpers emit, a build gate,
twister metadata, and a hardware-free fake. It has also acquired a
great many documented refusals, which I think is the more interesting
half — every `-ENOTSUP` that names itself is something a driver author
learns in a second instead of an afternoon.

### One board, one parent, three children

The old shape put two controllers at the devicetree root:
`/pdg-i2c` and `/pdg-spi`, siblings of nothing, each carrying its own
`serial-number` and each opening its own connection to what was
supposed to be the same physical board. Deduplication happened one
layer down in a registry, so the *physical* USB opens were already
one — but three references were taken, three strict opens could be
attempted, and nothing in the devicetree said these two nodes
described one object.

They now do. There is a multi-function-device parent,
`odp,pico-de-gallo`, representing one physical USB-attached board,
with the peripheral controllers as its direct children. The shield
ships the whole tree disabled:

```dts
/ {
	pdg0: pico-de-gallo {
		compatible = "odp,pico-de-gallo";
		status = "disabled";

		pdg_gpio0: gpio {
			compatible = "odp,pico-de-gallo-gpio";
			gpio-controller;
			#gpio-cells = <2>;
			ngpios = <4>;
			status = "disabled";
		};

		pdg_i2c0: i2c {
			compatible = "odp,pico-de-gallo-i2c";
			clock-frequency = <400000>;
			#address-cells = <1>;
			#size-cells = <0>;
			status = "disabled";
		};

		pdg_spi0: spi {
			compatible = "odp,pico-de-gallo-spi";
			#address-cells = <1>;
			#size-cells = <0>;
			status = "disabled";
		};
	};
};
```

The parent is now the sole owner of the board's USB handle. The
children borrow it and never close it. `serial-number` moved up to the
parent, where it belongs: one selector for one board, applying to
everything hanging off it. It is no longer declared on either
controller binding, so a leftover child property is not silently
ignored — devicetree processing fails with an undeclared-property
error naming the node and the binding.

What changes in practice is failure behaviour. One parent validation
now gates both children, so a parent that cannot open its board fails
both controllers coherently, at
`POST_KERNEL/CONFIG_MFD_PICO_DE_GALLO_INIT_PRIORITY` rather than at
the controllers' priority. The worst-case boot latency falls from as
many as three independent five-minute strict opens to one attempt plus
two fast child failures. The cost, stated plainly because it is a real
cost: a controller can no longer initialize independently of its
parent.

Being a child is now enforced rather than assumed. A controller at the
devicetree root, under a disabled parent, under an unrelated parent,
or nested more than one level deep is rejected **at build time** with
an explanatory static assertion. Before, the same mistakes produced an
unresolved `__device_dts_ord_N` at link time, which is a genuinely
awful thing to read.

The reparenting is a breaking change for anyone who already had
overlays. Absolute paths moved — `/pdg-i2c` is now
`/pico-de-gallo/i2c` — and the generated identifiers and dependency
ordinals derived from them moved with it. The `pdg0`, `pdg_i2c0` and
`pdg_spi0` labels are unchanged, so `&pdg_i2c0` still resolves; only
absolute paths need editing. Every overlay must now also enable
`&pdg0`.

### A real GPIO controller

`odp,pico-de-gallo-gpio` is new. It is a direct child of an enabled
parent, borrows the parent's connection, never releases it, and
initializes at `POST_KERNEL/45` — after the parent
(`KERNEL_INIT_PRIORITY_DEFAULT`, currently 40) and before I²C and SPI
(50). That ordering is load-bearing and I will come back to it.

`ngpios` is required, bounded to 1–32 at build time, and checked at
initialization against the firmware-reported
`device/info.num_gpios`. A mismatch is a local configuration error —
your devicetree disagrees with the board you plugged in — and fails
`-EINVAL` rather than proceeding with a wrong pin count. The parent of
every enabled GPIO child **must** define `serial-number`; a missing
selector is rejected at build time, because GPIO actuates physical
pins and a selector-less connection cannot report which attached board
it chose. Presence is not uniqueness: two parents naming the same
serial still alias, and nothing here rescues that.

Six API slots are implemented — `pin_configure`, `port_get_raw`,
`port_set_masked_raw`, `port_set_bits_raw`, `port_clear_bits_raw` and
`port_toggle_bits`. Every operation that reaches hardware is a
blocking USB round trip, so a call from interrupt context returns
`-EWOULDBLOCK` rather than blocking a handler, and transport failure
is `-EIO`.

Flag mapping is a positive allow-list. That is the design decision I
am happiest with in this driver: nothing is silently ignored.
`GPIO_DISCONNECTED`, `GPIO_INPUT | GPIO_OUTPUT`, single-ended,
open-source and open-drain, every interrupt-mode flag including
`GPIO_INT_WAKEUP`, and any unknown bit all return `-ENOTSUP`. Both
pulls, both output init levels, and an init level without
`GPIO_OUTPUT` return `-EINVAL`. `GPIO_ACTIVE_LOW` works, through
Zephyr's common GPIO layer, which handles it above the driver.

Three refusals are worth naming because they will bite someone:

Multi-pin writes and output initialization are explicitly
**non-atomic**. On a partial failure the acknowledged prefix
definitely changed, the failed pin is *indeterminate* — its request
may have executed with only the response lost — and later selected
pins were never issued. The driver logs the operation, the failed pin,
the requested mask and value, and the acknowledged prefix, and never
rolls back. Rolling back would require knowing the state of the pin it
just failed to learn the state of.

Reads are scoped to input pins. A pin the firmware records as an
explicit output contributes a zero bit and the scan continues, which
is exactly what Zephyr's reference `gpio_emul` controller does. This
is coupled to the rejection of `GPIO_INPUT | GPIO_OUTPUT` and the two
must not be changed independently.

And `port_toggle_bits` dispatches but returns `-ENOTSUP`. An explicit
output cannot be read back and no pin state is cached, so there is
nothing to toggle *from*. The consequence is concrete and I would
rather say it than have it discovered: blinky does not work with this
controller. Neither does the GPIO shell's toggle, the TPS382x
watchdog, or the LS0xx display. Interrupt configuration, callback
management and the pending-interrupt query are not implemented at all
and return `-ENOSYS`.

### Chip select, the Zephyr way

The wart is gone. `cs-gpios` is now **required** on every enabled SPI
controller — a missing property fails devicetree processing, because
this bridge has no native chip select to fall back to — and an SPI
child node's `reg` regains its ordinary Zephyr meaning as an index
into that array:

```dts
&pdg_spi0 {
	status = "okay";

	/*
	 * Chip select on firmware user GPIO0 (RP2350 GPIO8, header pin 11).
	 * The cell is a firmware user GPIO index, not an RP2350 GPIO number
	 * and not a header pin number.
	 */
	cs-gpios = <&pdg_gpio0 0 GPIO_ACTIVE_LOW>;

	nor: nor@0 {
		compatible = "jedec,spi-nor";
		reg = <0>;
		spi-max-frequency = <10000000>;
		jedec-id = [c8 40 15];
		size = <0x1000000>;
		status = "okay";
	};
};
```

Every `cs-gpios` entry must target an **enabled**
`odp,pico-de-gallo-gpio` controller under the **same** parent. A
foreign GPIO controller, a disabled sibling, and a Pico de Gallo GPIO
controller belonging to a *different* parent are each rejected at
build time with an assertion naming the offending array index. That
last case is the reason the check exists at all. It is not a typo
class — it is a real, enabled, perfectly valid Pico de Gallo GPIO port
on a *different physical board*, and without the check you would
assert chip select on one board while clocking data on another. Which
is precisely the failure the old `reg`-as-pin-index scheme could
produce, just with more devicetree in the way.

`GPIO_ACTIVE_HIGH` is permitted in `cs-gpios`. `SPI_CS_ACTIVE_HIGH`
remains `-ENOTSUP`; those are different questions and only one of them
is about the pin.

This came at a price, and the price is the most interesting thing in
the Zephyr changelog. Getting standard `cs-gpios` meant leaving the
firmware's `spi/batch` endpoint, which held chip select across the
data phase atomically. **Chip select is no longer atomic with the data
phase.** An ordinary successful transceive is now four USB round
trips — `spi/set-config`, `gpio/put` to assert, `spi/transfer`,
`gpio/put` to deassert — each independently fallible. Host death after
the assert leaves chip select asserted; recovery is a fresh session
that deasserts the pin, or a power cycle.

I went back and forth on this. When the temporary `reg` mapping was
written, full `cs-gpios` was explicitly *rejected* for exactly this
reason. What changed my mind is that atomicity was buying a guarantee
the module could not actually keep — only RPCs that return have
defined behaviour, and one that never returns leaves the call pending
forever with no errno, no cleanup and the SPI lock still held —
whereas standard `cs-gpios` buys correctness against a whole class of
misconfiguration, at build time, for free.

And it buys the thing the module is actually for. A driver written
against this controller now sees the same `cs-gpios` array, the same
`reg` semantics and the same `spi_context` it will see on a real SPI
controller. Nothing it learns here is a local dialect. A driver
authored on the bridge is a driver you can upstream — which was never
true while chip select was a property I had invented. The non-Zephyr `spi/batch`
APIs (the `gallo` CLI, Rust, C, Python, MCP) are unchanged and remain
fully supported; this trade is local to the Zephyr driver.

Two smaller behavioural differences fall out of the same move. Zephyr
collapses `spi-cs-setup-delay-ns` and `spi-cs-hold-delay-ns` into a
single `DIV_ROUND_UP(MAX(setup_ns, hold_ns), 1000)` microsecond value
applied at *both* edges, where the batch path honoured them
separately. And read-only and write-only transfers are now full-duplex
transfers of `max(tx_len, rx_len)` bytes with zero-filled TX or
discarded RX.

In exchange, two configurations that used to be rejected now work.
`SPI_LOCK_ON` is supported. `SPI_HOLD_ON_CS` is supported *but
requires* `SPI_LOCK_ON`, and returns `-ENOTSUP` without it — holding
chip select while another configuration could select a second slave
would leave two peripherals selected at once, which is not a state I
want reachable through a documented API. A successful hold commits
received data and retains both the asserted line and the bus lock
until `spi_release()` is called with that same configuration.

Then there is the fault latch, which exists because a failed deassert
is genuinely ambiguous. If a forced deassert returns an error, the
driver cannot tell whether the line went inactive. So the controller
latches, and every later transceive returns `-EHOSTDOWN` *before*
issuing any configuration, chip-select edge or clocking. Only a
`spi_release()` whose checked deassert succeeds clears it. A failed
release still releases software ownership, so nothing wedges, but
retains the latch and the exact configuration pointer so recovery can
be retried. Received data is committed only after an acknowledged
deassert or a successful deliberate hold — a transfer that succeeds
but whose deassert fails returns the deassert errno and does not
commit RX, because data read while chip select may still be asserted
is data of unknown provenance.

Finally, the priority chain. The SPI controller initializes at
`CONFIG_SPI_PICO_DE_GALLO_INIT_PRIORITY` (new, default 50) and
configures every declared chip-select pin as an explicit inactive
output at init — two USB round trips per pin, in ascending array
order. That couples SPI initialization to the GPIO child, so:

```text
MFD 40  <  GPIO 45  <  SPI 50
```

Kconfig cannot check that arithmetic, so runtime readiness is
authoritative: an inversion returns `-ENODEV` before configuring any
pin. There is no rollback on failure — earlier entries are
acknowledged inactive, the failing entry is indeterminate, later
entries were never issued. `-EBUSY` from this path means a firmware
GPIO event subscription owns the pin; reset it explicitly with
`gallo_system_reset_subscriptions()` after a strict open, or
power-cycle.

And one obligation that no code enforces: a declared chip-select pin
must be owned **exclusively** by SPI. The GPIO child being the sole
driver path for the pin's mode is not an ownership reservation. A
direct GPIO consumer can reconfigure or drive that pin between SPI
operations and nothing will detect it.

### `i2c_burst_write()` finally works

This one is a straightforward driver bug with a non-obvious fix.

The I²C controller accepted a `I2C_MSG_STOP`-delimited message group
only when it held one message, or a write followed by a
repeated-start read. Zephyr's `i2c_burst_write()` emits two *writes* —
`I2C_MSG_WRITE`, then `I2C_MSG_WRITE | I2C_MSG_STOP` — which matched
neither, and was rejected with `-ENOTSUP`. So was every hand-rolled
gather write, which is where most affected in-tree sensor, EEPROM and
display drivers actually live; `i2c_burst_write()` and
`i2c_burst_write_dt()` were the only `i2c.h` helpers that produced the
rejected shape, but the shape itself is everywhere.

A group's write messages now concatenate into a single payload and
reach the bus as one transaction: one START, one address phase, all
the bytes, one STOP. That is what a real controller emits for this
message sequence. Three shapes are supported — N writes, a single
read, and N writes followed by one read.

The rejections that remain are narrower but still explicit. A read
that is not the last message of its group, and more than one read in a
group, are still `-ENOTSUP`, because the FFI has no shape for reading
and then continuing within one transaction, and splitting the group
would insert a STOP the caller never asked for. Requiring
`I2C_MSG_RESTART` on the terminating read of a multi-message group is
unchanged.

The part I want to underline is what this was deliberately *not*
fixed with. The obvious route is the `i2c/batch` firmware endpoint,
which takes a list of operations and would express this directly. I
refused it. `i2c/batch` only became atomic in this very cycle (see
below), inside unreleased schema 0.7, and `validate()` cannot tell the
two firmware behaviours apart. Depending on it would have converted a
loud, honest `-ENOTSUP` into *silent register corruption* against a
firmware built before that fix. `gallo_i2c_write()` has no such
ambiguity, so the gather goes there.

While gathering, a second defect surfaced: the transfer size limit was
checked **per message**, not per group. Two 4096-byte writes each
passed the old check individually and would have concatenated to 8192.
The check is now an overflow-safe running total over a group's writes,
with the terminating read bounded separately, both against 4096, both
returning `-EMSGSIZE`. This narrows nothing that previously worked —
the reachable payload range through a single `i2c_msg` was `[0, 4096]`
and still is.

Concatenating needs a buffer, which needs a heap, which is how
`CONFIG_HEAP_MEM_POOL_ADD_SIZE_PDG_I2C` (default 8192) got added. The
driver `k_malloc()`s a merge buffer only for a group holding two or
more non-empty writes, at most 4096 bytes, at most one live at a time.
Declaring that contribution is mandatory rather than polite:
`k_malloc()` lives in `kernel/mempool.c`, which Zephyr compiles only
when the system heap is non-empty, and `CONFIG_HEAP_MEM_POOL_SIZE`
defaults to 0 — omitting it reproduces an earlier bug exactly, as an
undefined reference to `k_malloc` at link time.

Nothing allocates on the common paths. A single message, a single
write followed by a read, and a group whose writes are all empty are
each sent straight from the caller's buffers with no copy. Excluding
the all-empty case is deliberate: an empty payload has nothing to
merge, and allocating for it would introduce an `-ENOMEM` failure mode
on a call that can otherwise not fail locally.

### The scan that lied

This started as a small correctness item and turned into the most
satisfying find of the cycle.

Zephyr's I²C API permits `msg.buf == NULL` whenever `msg.len == 0`,
and both `i2c_write(dev, NULL, 0, addr)` and
`i2c_write_read(dev, addr, NULL, 0, rx, n)` produce exactly that. The
driver rejected every `NULL` buffer with `-EINVAL` regardless of
length. Easy fix, one would think.

Relaxing that check alone accomplishes nothing. The FFI rejects a
`NULL` pointer *unconditionally, before any length check*, because
`slice::from_raw_parts(NULL, 0)` is undefined behaviour in Rust — not
"returns an empty slice", not "probably fine", undefined. So the
pointer has to be kept away from the FFI rather than merely allowed
past the driver's own guard.

That forced a proper answer to a question the driver had been
answering by accident: what operation does a group of messages
actually *require*? A new `classify_group_()` judges a group by the
concatenated total of its writes and the length of its terminating
read, and no message carrying zero bytes is forwarded:

| group | operation |
|---|---|
| writes total > 0, read > 0 | write-then-read (unchanged) |
| writes total > 0, no read or read == 0 | plain write; the empty read phase is dropped |
| writes total == 0, read > 0 | plain read; the empty writes are dropped |
| writes total == 0, no read or read == 0 | address-only probe |

That last row is the interesting one. An address-only probe is refused
with `-ENOTSUP`, in the validation pre-pass — before the controller
mutex and before any group reaches the bus, preserving the "validation
is a complete pre-pass, no partial bus traffic" property the gather
work relies on.

The refusal is a hardware limitation, not a policy choice. The
RP2040/RP2350 `DW_apb_i2c` block drives the address phase only as a
side effect of pushing bytes into `IC_DATA_CMD`, so a bare
`START + ADDR + STOP` is physically unreachable. This is documented in
[rp-rs/rp-hal#678][rphal678] and [embassy-rs/embassy#4474][emb4474],
and it is the same silicon fact that produced the firmware bug two
sections down.

And here is what fell out. Zephyr's shell `i2c scan`
(`drivers/i2c/i2c_shell.c`) probes each address with a single
`{ buf = &dst, len = 0, flags = I2C_MSG_WRITE | I2C_MSG_STOP }`
message. `buf` is non-`NULL` there — it points at a real stack
variable — so the old `NULL` check never blocked it. It reached
`gallo_i2c_write(..., 0)` and, since the host-surface guard added in
this same release, returned `InvalidArgument` for every address.

**`i2c scan` prints `0 devices found` on a populated bus today.** Not
"fails", not "errors" — reports a clean, confident, entirely wrong
answer for every address in the range. At the new default it still
reports every address absent, but with `-ENOTSUP` and a stated reason
rather than silently.

Getting a correct answer needs `CONFIG_I2C_PICO_DE_GALLO_PROBE_WITH_READ`
(default `n`), which substitutes a 1-byte read to the same address:
an ACK means present, a NACK surfaces as `-ENXIO`. It costs one
blocking USB round trip per address — about 116 for the `0x04..0x77`
range the shell scans — and it is off by default for two reasons that
deserve to be documented rather than discovered.

A read probe is not semantically identical to a write probe: a
write-only device may acknowledge its write address while NACKing its
read address, so a scan performed this way can under-report. And the
substituted read *consumes a byte*, which can pop a FIFO or step an
EEPROM address pointer — Zephyr's own `i2c_shell.c` carries the same
warning, that scanning "can confuse your I2C bus, cause data loss, and
is known to corrupt the Atmel AT24RF08 EEPROM". A bus scan is a
deliberate act. A driver silently turning every zero-length write into
a read is not.

It is selected with `IS_ENABLED()` inside an ordinary `if`, never
`#ifdef`, so both arms compile in every configuration and neither can
bit-rot. And the supported way to scan through this bridge is still
`gallo i2c scan`, or the firmware's `i2c/scan` endpoint, which probes
the whole bus in one round trip.

### 1013

`PDG_SPI_MAX_BUFFER` is now 1013 bytes. Transfers above it return
`-EMSGSIZE` locally, before any allocation, controller lock,
set-config or chip-select edge. That is a hard breaking change for
anyone moving large SPI payloads through this bridge, and the number
looks arbitrary, so it is worth showing how it got there.

The constant was documented as `pico_de_gallo_internal::MAX_TRANSFER_SIZE`,
described as the "firmware single-transfer limit". That was wrong, and
the wrong mental model produced two wrong values in succession. The
constant is a **packet-buffer budget**. It must hold the payload
*plus* the postcard-rpc header, the length varint and the COBS
framing, and it must cover the **request** frame *and* the
**response** frame. Usable payload therefore sits strictly below it.

The ladder:

- **4096** (`MAX_TRANSFER_SIZE`) — a 4096-byte TX-only transfer passes
  the local check, reaches the transport, and fails `-ECOMM`.
- **3072** — a "conservative" guess reasoned from the firmware's
  `PacketBuffers<MAX_TRANSFER_SIZE + 1024>` headroom. 3072 **full
  duplex** also fails `-ECOMM`. That reasoning had considered only one
  direction.
- **1013** — the largest TX-only length measured to work on hardware.

Every observed failure was `-ECOMM` and never `-EMSGSIZE`, which is
the tell: the transport was always the limiter and the compiled
constant never was. The constant had been decorative.

1013 is measured, not derived, and the picture is incomplete. The
TX-only boundary is unresolved between 1013 and 1015 — 1014 was never
probed, and 1015 does something much worse than fail. Full duplex
succeeded at 512, failed at 3072, and was not tested from 513 through
1013. And while 1013 sits just under 1024 in a way that would be
consistent with a ~1 KiB budget and ~11 bytes of framing, there is no
evidence for that decomposition and it must not be relied on.
Applications needing a documented-safe duplex size must use 512 bytes
or less.

What is still owed is the real fix: derive the usable `spi/transfer`
payload ceiling from the worst-case request and response framing,
express it as one generated or shared contract instead of a constant
duplicated per consumer, and pin limit and limit+1 tests against it.
That requires a wire-crate change with schema and lockstep-release
implications, so it did not fit in this module's cycle.

### The module has CI now

Before this cycle, the Zephyr module had no automated gate of any
kind. That is worse than it sounds, because of the specific shape of
the hole: an FFI change could break the module while *every other gate
stayed green*. `check.yml` builds the FFI crate, which still compiles.
cbindgen regenerates the header without complaint. Nothing ever
compiled the Zephyr translation unit that consumed the removed symbol.
Two compile-time gates already sitting in the tree — `-Werror=switch`
over the Status-to-errno mapping in `drivers/common/common.c`, and the
`_Static_assert`s pinning mirrored FFI enums in the M5 test
harness — had therefore never once fired in CI.

`.github/workflows/zephyr.yml` now builds the module against a pinned
Zephyr revision (`26f811ee9d0d`, `v4.4.0-6123`) on
`native_sim/native/64`, across nine targets. A step fails the job if
`zephyr/README.md` does not record that same SHA, so the documented
revision and the tested revision cannot drift apart.

Three jobs. `selftest` runs `zephyr/scripts/ci-build.sh --self-test`
against checked-in fixtures and needs no Zephyr workspace at all — the
build driver has its own tests, including parsers exercised against
captured `undefined-ord.log` and `compile_commands.json` samples.
`build` creates the west workspace by blobless detached clone plus
`west init -l`, because `west init --mr` hands its argument to
`git clone --branch`, which accepts only a branch or tag name and
never an arbitrary commit SHA. `twister` runs the scenarios.

The build gate does something I have not seen elsewhere and would
recommend: it asserts on the *failures* too. `samples/spi_bridge` and
`samples/combined_i2c_spi_bridge` cannot build, because the
IS31FL3743B driver they want is not upstream. Rather than exclude
them, `assert_basefail()` pins their failure to *exactly one*
undefined `__device_dts_ord_N` resolving to an `is31fl3743b` node. If
they start failing for a different reason, that is a regression, and
CI says so.

The twister metadata (`tests.yaml`, eight applications) is mostly
redundant with the build script, and weaker — there is no twister
equivalent of the script's two-sided translation-unit and Kconfig
assertions. It earns its place for two things the script cannot do.
Twister forces `CONFIG_COMPILER_WARNINGS_AS_ERRORS=y` **and**
`--edtlib-Werror`, turning devicetree *binding* warnings into build
failures; this module ships four custom bindings and a plain
`west build` never checks them that way. And `type: unit` scenarios
run only under twister.

Seven of the eight scenarios are `build_only: true`, because twister
classifies `native_sim` as `type: native` and would otherwise execute
the binary, which reaches `gallo_init_strict()` and wants an attached
board. None declares `depends_on`: that key matches the board's
`supported:` list, and `native_sim/native/64` names neither `i2c` nor
`spi` — only the 32-bit `native_sim` does — so claiming it would
silently filter the scenario to nothing on the only platform this
module targets. Silent filtering is exactly the failure mode CI is
supposed to prevent.

`--jobs 1` is load-bearing, for a dull reason worth writing down:
Corrosion runs `cargo install cbindgen` per build, the builds share
`CARGO_TARGET_DIR`, and concurrent installs race to copy the binary.
One of them loses.

Two coverage experiments are recorded here because the negative result
is the useful part. A `type: unit` spike was tried and *retired*: the
`unit_testing` board cannot compile a driver translation unit at all,
because `cmake/modules/unittest.cmake` supplies the generated headers
as `file(TOUCH)`ed empty files. `syscall_macros.h` is empty,
`__syscall` expands to nothing, and `devicetree.h`, `ffs.h`,
`bitarray.h` and `mem_blocks.h` all fail together the moment
`<zephyr/device.h>` is pulled in. Two commits in, one commit out.

The eighth is the one that matters. `tests/pdg_fake` is a
weak-override fake bottom layer that substitutes for the real
host-side FFI calls, so the `i2c_burst_write` gather path can be
exercised with no board attached — and it is the only scenario in the
module that is *not* `build_only`, because nothing in it reaches
`gallo_init_strict()`. It is the first coverage this module has ever
had that CI actually runs.

There is also `tests/pdg_i2c_burst`, a board-attached regression image
covering `i2c_burst_write()`, a three-message hand-rolled gather, a
gathered write-then-read, the three rejection shapes, and the
per-group running total — including that the oversize rejection emits
no bus traffic at all. Every assertion compares against a reference
read-back rather than against the bytes written, so it holds on parts
that mask register bits. That one needs hardware, so CI builds it and
stops.

Keep the proportions in mind. **The nine-target build gate is
build-only** — it never executes anything, and it catches exactly the
class of defect that had been invisible and no other. The runtime
coverage is one fake-backed I²C suite. Everything this module does
against real silicon is still verified by hand.

## The bugs underneath

Reading the firmware's chip-select path carefully, in order to replace
it, turned up code that was wrong for reasons having nothing to do
with Zephyr.

### Two adjacent writes, and a register nobody named

`i2c/batch` takes a list of operations and executes them against one
target. It did *not* execute them as one I²C transaction. Each
operation got its own START and its own STOP.

That is not a batch. That is a loop with extra steps, and it produces
wrong answers on any target with a register pointer — which is most of
them.

Reproduced on hardware against a TMP102 at `0x48`. Seed TLOW to
`0x1230`, then:

```text
i2c_batch(0x48, [ Write[0x02], Write[0x03, 0x00] ])
```

This returned `Ok`. TLOW was still `0x1230`, unchanged. THIGH had gone
from `0x5000` to `0x0000` — **a register the caller never named** —
because the second write was reinterpreted as a fresh transaction, and
therefore as a pointer byte plus data. The identical bytes through
`i2c_write` set TLOW to `0x0300` correctly.

Success return, silent corruption, wrong register. This is the worst
category of bug I found this cycle and the one I am most glad to have
caught with an actual part on an actual bus rather than by reasoning.

`pico-de-gallo-hal` inherited the defect silently and completely. It
implements only `I2c::transaction`, so `embedded-hal`'s default
methods for `read`, `write` and `write_read` all route through this
endpoint. `write_read` — the single commonest idiom in every I²C
driver ever written — was affected.

The fix issues one `embedded_hal_async::i2c::I2c::transaction()`.
Adjacent same-direction operations concatenate, direction changes use
a repeated START, and only the final operation is followed by a STOP.
Implementation-wise it materialises the decoded list into a
`heapless::Vec<Operation, MAX_BATCH_OPS>`, carving disjoint read
slices out of the scratch buffer, with the context destructured so the
bus and the buffer are borrowed disjointly. No `unsafe`.

One consequence for callers: bus failures now report `failed_op = 0`,
because an atomic transaction fails as a unit and the failure cannot
honestly be attributed to one operation. Validation failures still
carry their exact operation index, because those are decided before
anything reaches the bus.

And one caveat I want on the record. The repeated-START-on-direction-change
behaviour is **vendor-documented, not analyser-measured**. The
DesignWare block's `IC_CON` resets to `0x00000065` with
`IC_RESTART_EN` set — that is the RP2350 SVD — and embassy-rp never
writes `IC_CON`. That is a strong argument. It is not a scope trace.

### The empty write that wedged the board

An I²C write with an empty payload used to hang. Not the call — the
*board*. Every endpoint on it, until USB re-enumeration.

The chain is four links long and each one is individually reasonable.

The RP2040/RP2350 `DW_apb_i2c` block drives the address phase only by
pushing bytes into `IC_DATA_CMD`. An address-only
`START + ADDR + STOP` is therefore physically unreachable on this
silicon — the same fact that produced the Zephyr `i2c scan` finding
above.

embassy-rp 0.10.0 knows this, and guards it in
`write_blocking_internal`. It does not guard it in
`write_async_internal`. With an empty iterator that function queues no
command, starts no transaction, and then still awaits a
`STOP_DET`/`TX_ABRT` interrupt that can never fire. The future never
completes.

postcard-rpc dispatches handlers serially. So one handler parked on an
interrupt that will never arrive does not park one endpoint — it parks
*every* endpoint on the device.

And the watchdog does not save you. It is fed every 800 ms by a
dedicated `watchdog_feeder_task`, precisely so a wedged handler cannot
starve it. That separation is what makes it useless here: the feeder
keeps feeding cheerfully while the dispatcher is stuck, and the 2 s
timeout never expires. A watchdog designed to survive a wedged handler
cannot detect a wedged handler.

Reachable from the C FFI, `pico-de-gallo-lib`, the `embedded-hal`
`I2c::write` impl, Python, MCP and Zephyr. Which is to say: every
surface.

Firmware now refuses an empty write payload with a new
`I2cError::ZeroLengthWrite`, appended at index 7 after `Other`. In a
batch the refusal happens during validation, so no earlier operation
in that batch reaches the bus.

Reusing the existing `BufferTooLong` was considered and rejected. Its
`Display` reads "buffer exceeds firmware limit", which is actively
misleading for a zero-byte buffer — on a defect whose whole difficulty
is that it presents as a hang with no diagnostic at all. The last
thing that failure mode needs is a wrong explanation. Four tests pin
the new variant: a compile-time index witness, an index-stability
assertion, a distinct-encoding check, and an unknown-index probe.

The host side then got the same guard, locally, in
`pico-de-gallo-lib`, `pico-de-gallo-ffi`, `pico-de-gallo-hal`,
`gallo-mcp` and `pyco-de-gallo`. This changes *which* requests
succeed not at all — firmware already refuses them — but it removes a
USB round trip spent being told no, and makes the refusal independent
of whatever firmware happens to be attached. A batch is validated in
full before anything is sent, so a rejected batch never drives an
earlier operation onto the bus.

`i2c_write_read` is deliberately unaffected. An empty write phase
there is legal, because that transfer does not terminate with a STOP,
so the address phase is driven by the read that follows.

In the HAL the error is `I2cHalError::I2c(ZeroLengthWrite)`, whose
`ErrorKind` is `Other`. Deliberately not `NoAcknowledge`, which would
wrongly imply the bus was driven and the target stayed silent. In the
FFI it maps to the existing `InvalidArgument = -5` rather than getting
a new code, because `Status` values are stable C ABI and a new value
would fall straight through the exhaustive
`switch ((enum Status)x)` that C consumers are explicitly told to
write.

There is also a documentation correction, which is the detail that
bothers me most. The `GalloI2cBatchOp` doc comment, and the book,
previously stated that `data` may be `NULL` when `data_len == 0`. The
documentation had been *advertising the exact call that wedged the
device*. A `Write` op now documents `data_len > 0` and a non-NULL
`data`. `GalloSpiBatchOp` still permits an empty payload, which is
legitimate for SPI.

Relatedly, `PicoDeGalloError::Endpoint` used to say the firmware had
processed the request. A locally refused request makes that untrue, so
the wording changed. Callers still need not distinguish the two cases,
but must not infer from this variant that the device was contacted.

### `gallo gpio put` had never worked

The `gallo` CLI has a `gpio put` subcommand for driving a pin. It
panicked on every invocation. Not on some pins, not on bad arguments —
on every invocation, since it was written.

```text
Command put: Short option names must be unique for each argument,
but '-h' is in use by both 'high' and 'help'
```

The subcommand declared `#[arg(short, long)] high: bool`, so clap
derived `-h` from `high`, colliding with the `-h` clap generates for
`--help`. That is a *builder* assertion rather than a parse error, so
it fired while the command tree was being constructed, before a single
argument was examined. No invocation could get far enough to parse
anything, let alone reach a device.

It survived CI because none of the 51 CLI tests already in this crate
parses a `gpio` subcommand, and clap builds a subcommand's arguments
only when parsing reaches it. The assertion was never evaluated. Fifty-one
tests, and the shape of the test suite meant they collectively could
not see it.

A second defect sat underneath the first. `high: bool` is a clap flag,
which takes no value, so `--high false` would have been rejected as an
unexpected argument even once the collision was resolved. The
subcommand had no way at all to drive a pin low. Fixing only the
collision would have produced a command that starts but silently
cannot express half of its purpose, which is worse than one that fails
loudly.

`--high` is replaced by a required `--level <high|low>`, a `ValueEnum`
with two explicit variants and deliberately no short option, so `-h`
stays with `--help` and the collision cannot be reintroduced by an
inattentive `short` attribute. Required rather than defaulted, because
this subcommand physically energises a pad on attached hardware and
should never guess which way.

The regression test is `cli_command_builder_is_well_formed`, which
calls `Cli::command().debug_assert()` — precisely the assertion that
was firing. Its value is that it is not specific to this bug:
`debug_assert` builds the command recursively rather than lazily, so
one test validates every subcommand in the tree and catches the whole
class — short-option collisions, duplicate argument ids, malformed
derives — at test time rather than at a user's first invocation. That
recursion is exactly the gap that let this ship.

### The MCP server that reported itself as the SDK

Smaller, and entirely cosmetic until you are the person reading the
logs. `gallo-mcp`'s `get_info` used rmcp's default
`Implementation::from_build_env()`, which expands
`env!("CARGO_CRATE_NAME")` *inside rmcp*. So the `serverInfo` of every
`initialize` result said `rmcp 2.2.0`. Clients displayed it, logs
recorded it, and nothing anywhere reported which version of
`gallo-mcp` was actually running. It would also have silently started
claiming `rmcp 3.1.4` after the dependency bump below. It now reports
`gallo-mcp` with `CARGO_PKG_VERSION`, so a release bump cannot leave
it stale.

That bump — rmcp 2.2.0 to 3.1.4 — closes a related honesty gap. rmcp
2.2.0 already listed `2026-07-28` in
`ProtocolVersion::KNOWN_VERSIONS`, and its
`negotiate_protocol_version` echoed any known version, so `gallo-mcp`
already answered `"protocolVersion": "2026-07-28"` to a client that
asked for it — while implementing none of that revision's semantics.
`result_type`, `ttl_ms` and `cache_scope` appear nowhere in the 2.2.0
source, and `server/discover` did not exist. A session that negotiates
that revision now actually receives the SEP-2322 `resultType`
discriminator and the SEP-2549 `ttlMs`/`cacheScope` cache hints, and
the server answers `server/discover`. Sessions on 2025-11-25 and older
keep their existing wire shape byte for byte.

All 43 tools — names, arguments, annotations, JSON payloads — are
identical, and no handler needed editing. rmcp 3.0's breaking change
with the widest blast radius, MRTR-aware handler return types, is
absorbed by `#[tool_handler]`, because this crate implements only
`get_info` by hand and never matches on `ServerResult`.

One nice ordering detail from the `num_gpios` work landed here too:
`spi_batch` parses its payload *before* calling `connect()`, because
connecting calls `system_reset_subscriptions()`, which tears down
every GPIO subscription on the board — including subscriptions owned
by other processes. Malformed hex should not be able to cause a
destructive cross-process side effect.

### The smaller half

The `reg`-as-pin-index problem had a host-side twin: nothing anywhere
knew how many GPIOs a board actually had. `NUM_GPIOS` was a
compile-time default, and chip-select indices were bounds-checked
against it or not at all.

`DeviceInfo` gained `num_gpios: u8` as its final field, and it is now
the runtime-authoritative pin count — it supersedes the compile-time
default and must never be synthesized or defaulted when `device/info`
decoding fails. That threads out to every surface:
`PicoDeGallo::num_gpios()` (cached), `Hal::num_gpios()`,
`gallo_num_gpios`, `PycoDeGallo.num_gpios()`,
`DeviceInfo.num_gpios`. `spi_batch` refuses `cs_pin >= num_gpios`
locally, without transmitting, and metadata failure, zero GPIOs, and
an invalid index stay three distinct outcomes rather than collapsing
into one.

`Hal::spi_device(cs_pin)` is consequently **fallible now**, which is
the one API break most likely to touch existing code.

On the firmware side, `spi/batch` applies three ordered refusals
before touching the chip-select pin: invalid index, monitored pin,
then `ExplicitInput`. Only `ExplicitInput` would corrupt tracked
state, so `LegacyAuto` and `ExplicitOutput` remain accepted — the
previous behaviour would silently convert an explicitly configured
input into a 3V3 push-pull output. Both pre-existing `.unwrap()` calls
were removed from that path while I was in there.

The FFI gained five appended status codes for the new outcomes:
`SpiInvalidCsPin = -71`, `SpiCsPinUnavailable = -72`,
`SpiCsPinMonitored = -73`, `SpiNoGpios = -74`,
`DeviceInfoTimeout = -75`. The Zephyr driver maps them to `-EINVAL`,
`-EACCES`, `-EBUSY`, `-ENODEV` and `-ETIMEDOUT` respectively, through
a `switch ((enum Status)status)` with no `default:` label, enforced by
`-Werror=switch` — so adding a status code without deciding its errno
is a build failure.

Two things arrived from outside the bug hunt. `pico-de-gallo-hal` now
supports **`embedded-io` 0.7**: `Uart` implements the blocking and
async `Read`/`Write` traits from `embedded-io` 0.7 and
`embedded-io-async` 0.7 under an additive `embedded-io-07` feature,
with the 0.6 impls still on by default behind `embedded-io-06`. Either
alone, both together, or neither — disabling both drops the dependency
entirely. Contributed by Matteo Tullo, who also cleared a clippy
`clone_on_copy` coming out of PyO3's `from_py_object` codegen.
`pico-de-gallo-lib` gained hardware-in-the-loop tests for the
zero-length guards, `#[ignore]`d by default:

```console
$ cargo test -p pico-de-gallo-lib -- --ignored --test-threads=1
```

And one more thing turned out to be broken, which I only found by
trying to release. Automated publishing to crates.io had not worked
since 2026-07-28: a commit removed the `env:` block that mapped the
stored `CARGO_REGISTRY_TOKEN` into the publish step, and nothing
replaced it, so `cargo publish` died with "no token found". It failed
that way on `application-v0.8.0`, and again on `internal-v0.7.0`.
Every release since July had been quietly published by hand, within
minutes of the failed run — which is exactly the habit that stops
anyone noticing the automation is dead.

The fix does not put the secret back. The six crates that go to
crates.io are now pushed via **Trusted Publishing**: the job requests
`id-token: write`, `rust-lang/crates-io-auth-action` exchanges the
resulting GitHub JWT for a short-lived crates.io token, and that
action's `post` step revokes it when the job ends. There is no stored
credential left to leak or rotate. One hazard worth repeating for
anyone copying the pattern: crates.io takes 30–60 seconds to index, so
pushing several crate tags at once can fail downstream with "no
version of `pico-de-gallo-internal` X.Y.Z found in registry". Push the
wire crate's tag first, wait, then the rest.

## What is not fixed

The changelogs for this cycle are unusually blunt about what was not
established, and I would rather repeat that here than let a release
announcement imply more than the work supports.

**A 1015-byte TX-only `spi/transfer` never returns and wedges the
firmware dispatcher device-wide.** Deterministic, reproduced across
two byte-identical consecutive runs on one board. Once triggered,
*every* subsequent RPC hangs — including from a freshly started host
process, and including `system/reset-subscriptions`, which is the
endpoint that exists to recover orphaned state. The condition survives
host process death entirely. The 2 s watchdog does not catch it, for
exactly the reason the zero-length write bug was not caught: the
feeder task keeps feeding while a request handler blocks. In the
reproduced tests the device resumed after USB re-enumeration — on
Windows/WSL that was `usbipd detach` followed by attach; on
Linux/macOS use cable reconnect or USB unbind/rebind, and power-cycle
if re-enumeration is unavailable. That is an observed procedure, not
proof that detach cancels the blocked handler.

Root cause is in the firmware/wire layer and is not fixed.
`PDG_SPI_MAX_BUFFER = 1013` puts the hang out of reach *through the
Zephyr driver* by rejecting 1014 and above locally, before any
transport call. That is **containment, not a fix**, and it does not
prove that no other hang window exists below 1013.

**The I²C 4096-byte bound remains unmeasured.** It is
`MAX_TRANSFER_SIZE`, the firmware's declared argument bound, not a
demonstrated end-to-end ceiling. `PDG_SPI_MAX_BUFFER` started from the
same figure and had to be lowered to 1013. `i2c/write` carries its
payload in the request frame exactly as `spi/transfer` does, so the
analogy is tempting. It was left at 4096 anyway, because a bound
measured on a differently framed endpoint is a guess, and lowering it
would reject single-message writes that work today. Guessing downward
is still guessing.

**The `i2c scan` and zero-length-buffer work is built-only, not
behaviourally verified.** No Zephyr environment on the authoring
machine, no board on the bench that week, the build gate is build-only
and builds only the default configuration, and the one suite CI does
run covers the gather path rather than this one — so the
`PROBE_WITH_READ=y` arm compiles by construction (`IS_ENABLED`, not
`#ifdef`) and is never exercised.

One thing *was* executed. `struct pdg_i2c_group`, `enum pdg_i2c_op`,
`validate_group_()` and `classify_group_()` were extracted **verbatim**
into a throwaway host harness and driven through **18 group shapes**:
the pre-existing shapes; the gather shapes crossed with zero lengths
(`W(1)+W(0 NULL)`, `W(0 NULL)+W(3)`, `W(0)+W(0)`, `W(0)+W(0)+R(2)`,
`W(2)+W(1)+R(0 NULL)`); the Zephyr shell scan probe; both `NULL`-buffer
forms; the three rejection shapes, which still reject; and the
per-group running total at and over the limit. All 18 matched, clean
under `-Wall -Wextra -Werror -Wswitch-enum -Wformat=2` at `-O0`,
`-Os`, `-O2` and `-O3`. The harness is not committed and is not a test
suite. It covers four items and says nothing about
`pdg_i2c_transfer()`, the gather itself, the Kconfig wiring, or any
transport behaviour.

**The upstream `spi_loopback` result is not a clean pass.** On
`native_sim/native/64` it is 41 PASS / 12 SKIP / 1 FAIL / 2 NOT BUILT,
and it must not be read as anything better.

The single FAIL is upstream's own `test_spi_complete_multiple_timed`,
and it is *unrunnable on this target rather than a driver defect*.
`spi.c:406` asserts `time_spent_us >= minimum_transfer_time_us` — a
**lower** bound, measured with the Zephyr clock, which on `native_sim`
does not advance while the host thread is blocked inside a USB call.
Every transfer measures 0 µs.
`CONFIG_SPI_IDEAL_TRANSFER_DURATION_SCALING` bounds only the **upper**
limit, so no multiplier value can affect this assertion. It fails on
SLOW and passes on FAST purely because SLOW's theoretical minimum
(432 µs) is larger. Structural, not flaky.

The 12 SKIPs are all expected and were verified individually: five
word sizes the driver rejects with `-ENOTSUP`, `test_spi_deinit` (no
`miso-gpios`/`mosi-gpios` on `zephyr,user`), and `test_spi_hold_on_cs`
(HOLD without LOCK). The 2 NOT BUILT are the async cases, which
require `CONFIG_SPI_ASYNC=y`; this driver `BUILD_ASSERT`s that off,
which is itself the fix for an earlier segfault-waiting-to-happen.

And the standing limitations: chip select is no longer atomic with the
data phase; exclusive ownership of a chip-select pin is an application
obligation that no code enforces; the module still wants Zephyr `main`
rather than a release; two of the four samples still cannot build; and
upstreaming the module proper is still tracked in [#98][i98].

## Upgrade and compatibility

Because the wire schema changed, **flash the new firmware and update
your host crate in the same step**. Mixed versions will not talk to
each other — by design, the validation refuses rather than mis-decodes.

| Crate                    | Old    | New     |
| ------------------------ | ------ | ------- |
| `pico-de-gallo-internal` | 0.6.1  | 0.7.0   |
| `pico-de-gallo-lib`      | 0.7.1  | 0.8.0   |
| `pico-de-gallo-hal`      | 0.6.1  | 0.7.0   |
| `pico-de-gallo-ffi`      | 0.7.1  | 0.8.0   |
| `gallo` (CLI)            | 0.8.0  | 0.9.0   |
| `gallo-mcp`              | 0.2.0  | 0.3.0   |
| `pyco-de-gallo`          | 0.4.2  | 0.5.0   |
| `pico-de-gallo-firmware` | 0.10.1 | 0.11.0  |

After flashing, confirm the schema lines up:

```console
$ gallo version
```

Things that will stop compiling or stop parsing:

- **`gallo gpio put --high` is gone.** Use `--level high` or
  `--level low`. There is no short option; `-h` is `--help`.
- **`Hal::spi_device(cs_pin)` is fallible.** It validates the index
  against the device-reported GPIO count, so it returns a `Result`.
- **`I2cError` and `SpiError` gained variants.** These enums are not
  `#[non_exhaustive]`, so exhaustive matches need new arms:
  `I2cError::ZeroLengthWrite`, and `SpiError::{InvalidCsPin,
  CsPinUnavailable, CsPinMonitored}`.
- **An empty I²C write is now an error everywhere**, not a hang. If
  you were using `i2c_write(addr, &[])` as a presence probe, use
  `gallo i2c scan` or the `i2c/scan` endpoint instead. `i2c_write_read`
  with an empty write phase is unaffected and still legal.
- **`gallo i2c batch` semantics changed** without the command line
  changing. It now sends one transaction. If you were relying on the
  old per-operation START/STOP behaviour, you were relying on a bug,
  and it was probably writing to registers you did not name.

And for the Zephyr module, which upgrades by pulling `main` rather
than by version:

- Every overlay must enable `&pdg0`, and `&pdg_gpio0` if you use SPI.
- Every enabled SPI controller needs `cs-gpios`, pointing at a
  same-parent GPIO sibling. A missing property is a build failure.
- `serial-number` moves from the controllers to the parent. Leaving it
  on a child is a devicetree error, not a warning.
- Absolute node paths changed: `/pdg-i2c` → `/pico-de-gallo/i2c`.
  Label references (`&pdg_i2c0`) are unchanged.
- SPI transfers above 1013 bytes now return `-EMSGSIZE`. Use 512 bytes
  or less for full duplex if you want a documented-safe size.
- A zero-length I²C write now returns `-ENOTSUP` rather than
  `-EINVAL`, and `i2c scan` needs
  `CONFIG_I2C_PICO_DE_GALLO_PROBE_WITH_READ=y` to produce a correct
  answer.

## Where to look

The Zephyr module still does not have a chapter in the book. Until it
does, the [module README][zephyr-readme] is the guide — prerequisites,
workspace setup, both working samples, and a frank list of driver
limitations that is now considerably longer and considerably more
accurate than it was nine days ago.

Everything in this release, in one place:

| Crate | Version | Package |
| ----- | ------- | ------- |
| `pico-de-gallo-internal` | 0.7.0 | [crates.io][internal] |
| `pico-de-gallo-lib` | 0.8.0 | [crates.io][lib] |
| `pico-de-gallo-hal` | 0.7.0 | [crates.io][hal] |
| `pico-de-gallo-ffi` | 0.8.0 | [crates.io][ffi] |
| `gallo` (CLI) | 0.9.0 | [crates.io][app] |
| `gallo-mcp` | 0.3.0 | [crates.io][mcp] |
| `pyco-de-gallo` | 0.5.0 | [PyPI][pypi] |
| `pico-de-gallo-firmware` | 0.11.0 | [GitHub release][firmware] |

The firmware release carries `firmware-rev1.uf2` and
`firmware-rev2.uf2` — pick the one matching your board revision, hold
BOOTSEL, and drag it across.

And the rest:

- source, hardware and case: [the repository][repo]
- docs: [the Pico de Gallo book][book] ·
  [the Zephyr module README][zephyr-readme]
- previously, on the Zephyr module:
  [Real Buses for Zephyr's `native_sim`][zephyr-post]
- on writing drivers without an MCU at all:
  [Writing Embedded Drivers Without an MCU][drivers-post]

[book]: /pico-de-gallo/
[zephyr-post]: /posts/pico-de-gallo-zephyr-native-sim/
[drivers-post]: /posts/writing-embedded-drivers-without-an-mcu/
[repo]: https://github.com/OpenDevicePartnership/pico-de-gallo
[zephyr-readme]: https://github.com/OpenDevicePartnership/pico-de-gallo/blob/main/zephyr/README.md
[firmware]: https://github.com/OpenDevicePartnership/pico-de-gallo/releases/tag/firmware-v0.11.0
[pypi]: https://pypi.org/project/pyco-de-gallo/
[ffi]: https://crates.io/crates/pico-de-gallo-ffi
[lib]: https://crates.io/crates/pico-de-gallo-lib
[hal]: https://crates.io/crates/pico-de-gallo-hal
[app]: https://crates.io/crates/gallo
[mcp]: https://crates.io/crates/gallo-mcp
[internal]: https://crates.io/crates/pico-de-gallo-internal
[i98]: https://github.com/OpenDevicePartnership/pico-de-gallo/issues/98
[rphal678]: https://github.com/rp-rs/rp-hal/issues/678
[emb4474]: https://github.com/embassy-rs/embassy/issues/4474
