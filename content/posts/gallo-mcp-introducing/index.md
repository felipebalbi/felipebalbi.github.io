+++
title = "Agentic Gallo: Introducing gallo-mcp"
date = 2026-07-28T20:00:00
description = "gallo-mcp is a new Model Context Protocol server that hands a Pico de Gallo board to the AI agent writing your driver — one tool per peripheral operation across I²C, SPI, UART, GPIO, PWM, ADC, and 1-Wire, over stdio. Plus the pico-de-gallo-lib 0.7.1 plumbing — fallible constructors and nameable comms errors — that made it possible."
[taxonomies]
tags = ["rust", "embedded", "pico-de-gallo", "mcp", "agents", "release"]
+++

[Pico de Gallo][book] turns an RP2350 into a USB-attached bridge that
lets a host program drive real I²C, SPI, UART, GPIO, PWM, ADC, and
1-Wire straight from your laptop — so you can write and test device
drivers in `std` Rust, C, or Python instead of cross-compiling for an
MCU every time. Today there's a new way to reach that hardware, and it
isn't for you: [`gallo-mcp`][crate] is a [Model Context Protocol][mcp]
server that hands the board to the AI agent writing your driver.

<!-- more -->

## One tool per peripheral, over stdio

`gallo-mcp` runs an MCP server over stdio, wraps
[`pico-de-gallo-lib`][lib], and exposes one tool for every peripheral
operation across all seven buses. Point an MCP-capable agent at it and
the model can probe and drive real silicon through the same board the
`gallo` CLI talks to — read a sensor register, scan a bus, toggle a
pin — while it's writing the driver, without cross-compiling or
flashing anything.

That closes a loop that used to need a human in the middle. Instead of
you running `gallo i2c scan`, pasting the output into the chat, and
asking "now what?", the agent can:

- explore an unfamiliar device interactively,
- generate register-level driver code and validate it against the
  actual chip, and
- turn "what does this chip return?" into a tool call it makes itself.

## Wiring it into a client

Install the server:

```console
$ cargo install gallo-mcp
```

`gallo-mcp` speaks MCP on **stdout** and logs to **stderr**, so it's
meant to be launched by a client, not run by hand. Add it as a local
(stdio) server. These config files are safe to commit per project, so
the tools show up only in the repos that opt in:

```json
// opencode.json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "pico-de-gallo": { "type": "local", "command": ["gallo-mcp"], "enabled": true }
  }
}
```

```json
// Claude Code — .mcp.json
{ "mcpServers": { "pico-de-gallo": { "command": "gallo-mcp", "args": [] } } }
```

```json
// Cursor — .cursor/mcp.json
{ "mcpServers": { "pico-de-gallo": { "command": "gallo-mcp", "args": [] } } }
```

One detail worth calling out: the server holds **no persistent USB
claim**. Each tool call opens the board, runs, and releases it, so the
device stays free for the `gallo` CLI or another host process between
calls. There's a small fixed connection cost per call in exchange, but
it also means the server starts fine with nothing plugged in — you can
attach the board mid-session and the tools start working. If you have
more than one board attached, `--serial-number <SN>` picks one.

## The tool surface

Tools are grouped by peripheral, and every one is annotated: reads
carry MCP's `readOnlyHint`, writes and actuation carry
`destructiveHint` (more on why that matters below).

| Peripheral | What the agent can do |
| --- | --- |
| device | list boards, check attach state, read firmware/schema version, ping |
| I²C | read, write, write-then-read, scan, batch, get/set frequency |
| SPI | read, write, full-duplex transfer, flush, batch, get/set config |
| UART | read, write, flush, get/set config |
| GPIO | read a pin, drive a pin, set direction/pull, bounded edge waits |
| PWM | get/set duty cycle, enable/disable, get/set config |
| ADC | read a sample, read config |
| 1-Wire | reset, read, write, parasitic-power write, ROM search |

The [full catalog is in the book][mcp-chapter]. A couple of conventions
are worth knowing up front. Byte payloads go **in** as hex strings —
comma-separated or bare, `"0x00,0x10"` or `"0010"` — and come **back**
as both hex and a decimal array:

```json
// i2c_write_read {"address":72,"data":"0x00","count":2}
{ "hex": "0x0B,0xCF", "bytes": [11, 207] }
```

And the GPIO edge waits are **timeout-bounded only**: each requires a
non-zero `timeout_ms`. This first release deliberately leaves out
infinite waits and push-based edge subscriptions — a wait that never
returns would wedge the stdio session, and event streaming is out of
scope for now.

## Seeing the same truth as the shell

The server was validated on real hardware: a Pico de Gallo (firmware
v0.10.0, schema v0.6.0 — the [reliability release][reliability]) with a
TMP108 temperature sensor on I²C.

Over stdio, `status` reports the attached board:

```json
{ "attached": true, "firmware_version": "0.10.0", "schema_major": 0, "schema_minor": 6 }
```

`i2c_scan` finds the sensor at `0x48`:

```json
// i2c_scan {"include_reserved":false}
{ "addresses": ["0x48"], "raw": [72] }
```

And `i2c_write_read` pulls its two temperature bytes:

```json
// i2c_write_read {"address":72,"data":"0x00","count":2}
{ "hex": "0x0B,0xCF", "bytes": [11, 207] }
```

Those bytes are byte-for-byte identical to what the CLI returns:

```console
$ gallo i2c write-read -a 0x48 -b 0x00 -c 2
0b cf
```

Turning `0x0BCF` into degrees Celsius is between you and the TMP108
datasheet — the point is that the MCP round-trip returns exactly what
the shell does. An agent driving the board through `gallo-mcp` sees the
same truth you would at the prompt.

## A word on safety

`gallo-mcp` does **not** gate writes itself. It annotates them and
delegates approval to the MCP client: read tools are marked
`readOnlyHint`, write and actuation tools are marked `destructiveHint`.
A well-configured client uses those hints to prompt you before it
drives a pin or writes a bus.

That delegation cuts both ways. Under a permission-less or
blanket-allow client, an agent can actuate hardware — toggle GPIOs,
write I²C/SPI, change bus configuration — with no confirmation at all.
If the board is wired to anything you care about, run the server behind
a client that honors `destructiveHint`, and set your permissions
accordingly. The bytes are real, and so are the volts.

## The plumbing: `pico-de-gallo-lib` 0.7.1

Handing the board to an agent turned up two rough edges in the host
library, and smoothing them out is the other half of today's release.

**`try_new()`.** The existing `PicoDeGallo::new()` (and
`new_with_serial_number()`) enumerate USB at construction and **panic**
when no matching board is present or the interface can't be claimed —
the old "constructing never fails" doc comment was simply wrong. That's
fine for a CLI that's about to exit anyway, but a long-lived MCP server
that connects per call can't panic just because you haven't plugged the
board in yet. So 0.7.1 adds fallible `try_new()` and
`try_new_with_serial_number()`, returning `Result<PicoDeGallo, String>`
so the caller can report "no device attached" or retry a transient
claim failure. The panicking constructors stay exactly as they were.

**Nameable comms errors.** 0.7.0 re-exports `HostErr` and `WireError`
(and makes the `host_client` path public), so a downstream crate can
name the transport error types when it maps `PicoDeGalloError` into its
own representation — without pulling in `postcard-rpc` directly. That's
what lets `gallo-mcp` match on `PicoDeGalloError::Comms(HostErr::Closed)`
and turn a dropped USB connection into a clean "no device attached"
message instead of an opaque error.

Both changes are additive and non-breaking; `hal`, `ffi`, `gallo`, and
the Python bindings resolve 0.7.1 without any changes of their own. If
you're building your own host tooling on the library, `try_new()` is
the constructor you want.

## Get it

```console
$ cargo install gallo-mcp
```

- crate: [`gallo-mcp`][crate] · docs: [docs.rs/gallo-mcp][docs]
- library: [`pico-de-gallo-lib` 0.7.1][lib]
- everything else: the [Pico de Gallo book][book] and the [repo][repo]

Wire up a board, point your agent at it, and let it read the chip
itself.

[book]: /pico-de-gallo/
[mcp-chapter]: /pico-de-gallo/crates/mcp.html
[mcp]: https://modelcontextprotocol.io/
[crate]: https://crates.io/crates/gallo-mcp
[docs]: https://docs.rs/gallo-mcp
[lib]: https://crates.io/crates/pico-de-gallo-lib
[repo]: https://github.com/OpenDevicePartnership/pico-de-gallo
[reliability]: /posts/pico-de-gallo-reliability-release/
