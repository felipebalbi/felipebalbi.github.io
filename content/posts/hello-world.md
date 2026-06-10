+++
title = "Hello, world"
date = 2026-06-10T08:00:00
description = "A new home on the web."
[taxonomies]
tags = ["meta"]
+++

This is the first post on the new version of [balbi.sh](/). The
previous incarnation was a single HTML file with one link; this one is
built with [Zola](https://www.getzola.org/) and styled after
[ef-melissa-light](https://github.com/protesilaos/ef-themes) — a warm,
legible Emacs theme by Protesilaos Stavrou. Body text is set in
**Aporetic Serif**, headings and UI in **Aporetic Sans**, and code in
**Aporetic Sans Mono** — all from the same author as the colour
palette.

## Why now

I want a place to keep working notes — short writeups about the things
I run into while building firmware. The bar for publishing here is low
on purpose. If a problem took me more than an hour to figure out, it
probably deserves a post so I (or you) don't have to figure it out
again next time.

## What to expect

Mostly:

- **Embedded Rust** — Embassy, `embedded-hal`, async on tiny chips.
- **Bare-metal C** — when Rust isn't an option, which is more often
  than I'd like.
- **Tooling** — probe-rs, defmt, the linker, the assembler, the things
  that quietly hold everything together.
- **Occasional rants** about debugging hardware at 2am.

If you want to follow along, there's an [Atom feed](/atom.xml).

## Code, by the way

It looks like this:

```rust
#[embassy_executor::task]
async fn blinky(mut led: Output<'static>) {
    loop {
        led.toggle();
        Timer::after_millis(500).await;
    }
}
```

That's all for now. More soon.
