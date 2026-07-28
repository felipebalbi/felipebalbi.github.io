+++
title = "Hi, I'm Norbert"
date = 2026-07-27T12:00:00
description = "Introducing Norbert — a small, patient CLI that programs SPI NOR flash chips over a Pico de Gallo bridge. It reads the datasheet, verifies its work, and refuses to guess."
[taxonomies]
tags = ["rust", "embedded", "flash", "pico-de-gallo", "norbert"]
+++

Meet **Norbert** — a small command-line tool I wrote to program SPI
NOR flash chips. Its defining trait is patience: it reads and parses
the SFDP tables, verifies its work, and would rather wait a second
than guess.

<!-- more -->

Hand Norbert a chip and it works out what the part is — reads the
JEDEC ID, parses the SFDP tables, or falls back to a table of chips it
already knows. From there it can erase the device, program an image,
verify every byte it wrote, and read the flash back. It drives a bare
flash chip over a [Pico de Gallo][pdg] bridge, so there's no adapter
board to wire up.

A normal session looks like this:

```console
$ norbert detect
Hmm... let's see what we've got here.
Found Winbond W25Q128JV.

$ norbert program firmware.bin
Programming firmware.bin — 512 KiB at 0x000000.

  erase    [██████████████████████████]  8/8 blocks
  program  [██████████████████████████]  512 KiB/512 KiB
  verify   [██████████████████████████]  512 KiB/512 KiB

Done. Have a nice boot.
```

That chattiness is the point of the name: Norbert has a personality,
but only in its output. Underneath, the engineering is deliberately
dull — programming flash should be predictable, transparent, reliable,
and, above all, boring. It won't skip verification because a write is
*probably fine*, and it won't assume every chip behaves the same.

It's early — version 0.1 — but it already recognizes a fair number of
chips --- anything supporting SFDP should just work ---, and `--quiet`
gives scripts machine-friendly output when you don't want the
commentary. The source is [on GitHub][repo]; if Norbert doesn't know
your chip yet, teaching it one is the easiest contribution to make.

[pdg]: /pico-de-gallo/
[repo]: https://github.com/felipebalbi/norbert
