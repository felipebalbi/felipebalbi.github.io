+++
title = "Mole, briefly: what's in the gap"
date = 2026-07-20T09:00:00
description = "Why none of the tools on my bench could drive the failure-path test the previous post sketched, and what mole's architecture looks like: a programmable bit-cycle engine on the FPGA, a host-side SDK that compiles down to it, and no opinion about what the bus is supposed to do."
[taxonomies]
tags = ["embedded", "rust", "i2c", "i3c", "fpga", "mole", "testing"]
+++

The [previous post](/posts/i2c-target-restarted-zero/) ended with a
wishlist masquerading as a paragraph. To reproduce that
`Restarted(0)` failure on demand, I wanted a controller I could
program at sub-bit-time resolution, that would do the same thing on
every run, that I could put in a tight loop without drifting, and
that I could describe a test to in something simpler than another
firmware project. I closed the post saying I'd written that tool. I
didn't say what it was.

It's called [mole](https://github.com/felipebalbi/mole). This post
is about why I ended up building it instead of buying or borrowing
something already on the bench, and what it looks like at the level
of "two boxes and an arrow." Nothing I had was wrong because it
was bad at its job. Each was the wrong *shape*. Mole is the shape
I needed; the rest of the series is the details.

## What the existing tools won't do

Transport-level analyzers — the Saleae, the Total Phase
Aardvark/Beagle family, sigrok-driven gear — are how I find out
what my bus actually did. They sit on the wires, sample fast
enough, and decode protocols I'd otherwise be counting clock edges
to read. When they include a controller side at all, that
controller side is honest: it generates well-formed I²C, because
the purpose of the tool is to be the trustworthy reference end of
the conversation, not the adversary. It will not produce a
back-to-back STOP-then-Sr with the gap shortened to sub-microsecond
spacing, will not inject a STOP between the eighth and ninth bit,
will not hold SCL low past the controller's stretch timeout to see
what happens. Those aren't bugs. The analyzer's job is to record;
it does that, and I have several.

Vendor IP test benches are the next thing I'd reach for. Every
silicon vendor whose I²C/I³C IP I've used ships an internal
verification rig. The problem is twofold: the rig is built around
the vendor's silicon and the vendor's bench setup, neither of which
is the system *I'm* trying to test, and the rig's verification
target is the vendor's IP, not my driver. "Does this silicon
conform to the spec?" is a different question from "does this
driver survive *non-conforming* traffic?" The first is what the IP
team has to answer to ship; the second is the one that keeps
showing up in my bug tracker.

Compliance-grade rigs that *can* generate arbitrary
legal-or-illegal sequences against a DUT do exist as a category.
They're also priced for the people who need them on a production
line — fabs, certification labs, the IP houses on the other end of
the previous paragraph. The gap between a $400 analyzer that
records what your bus did and a six-figure rig that will drive
whatever waveform you can describe is the gap mole sits in. The
capability isn't novel; the price point at which an individual
engineer can have it on their desk is.

## A programmable bit-cycle engine, not a faster I²C controller

Mole is not a better I²C controller. It is not a more
spec-compliant I²C controller, not a faster I²C controller, and
at the level of the hardware not even an *I²C* controller. It is
a programmable bit-cycle engine that happens to have its two
output pins wired to SDA and SCL.

That distinction matters because a fixed-function I²C controller —
the one in your MCU, in any USB-to-I²C dongle, on the analyzer's
transmitter side — knows what I²C is in the structural sense:
there's logic inside it that refuses to deviate from the spec. Ask
it to emit a STOP in the middle of a byte and at best you get an
error code; at worst it silently won't do it. Ask it to delay the
Sr after a STOP by 200 ns and either there's no API for that or
the silicon imposes its own floor. The IP is *doing its job*. Its
job is to be I²C-shaped, and non-I²C-shaped instructions are out
of scope.

The engine inside mole has the opposite property. It knows how to
drive SDA and SCL on a quarter-bit clock and how to sample what's
there; how to wait for an edge, compare a sampled value against an
expectation, and write a record to a result buffer. It does not
know what I²C is. It does not know what a START condition looks
like in the abstract — only that, if I write a program that drives
SDA low while SCL is high, that's what's going to happen on the
wires. The engine has no opinion about whether that's legal.
Legality is a property of the *program*; the engine will run any
program it can decode.

The other property that makes mole a test tool rather than a
fuzzer is that the engine has zero runtime randomness. Error
injection happens in the host-side compiler, not on the FPGA, so
`ratio = 0` produces a bytecode with literally zero errors written
into it, and `(seed, ratio)` reproduces a run byte-for-byte across
machines and across rebuilds. That's a longer story — it gets a
post of its own further down the series — but it's the property
that lets a failing test mean something specific instead of "it
happened sometimes."

## Two layers, briefly

The architecture is two layers, and the split is the load-bearing
design decision in the project.

**Layer 0** is the engine. It lives in the FPGA. It is small on
purpose: 26 opcodes across three live groups — 10 WIRE opcodes for
driving and sampling the bus, 8 CTRL opcodes for flow control and
configuration, 8 DATA opcodes for an eight-register 32-bit file
the engine uses to count loops — encoded as fixed-width 32-bit
instructions. It knows about quarter-bit timing, sticky flags, and
a result ring it can write samples and markers to. It does not
know about I²C, I³C, addresses, CCCs, T-bits, ACKs, NACKs, parity,
or any protocol-level concept above "drive a symbol on SDA at this
quarter, sample SDA at that quarter, compare it to this
expectation." A single bitstream contains both roles; controller
versus target is selected at runtime by a one-opcode role write.
The engine plays either; the program decides which.

**Layer 1** is a host-side SDK. All of the protocol knowledge —
every I²C mode, the I³C SDR and HDR-DDR primitives, the CCC
catalog, the peripheral emulators, the error-injection compiler —
lives here, as source that compiles down to Layer-0 bytecode. The
language Layer 1 is written in I'll leave for another post; what
matters here is shape, not syntax. The SDK is the diff-able,
version-able encoding of what the spec means, and changing how
mole understands the spec means changing source files, not
respinning the bitstream.

The point of two layers is that the engine is small enough to
reason about — eventually, small enough to formalize — and the
spec knowledge is in the place where specs actually live, which is
text files you can edit. When MIPI clarifies an edge case, the SDK
gets a patch. The bitstream doesn't move.

## What I²C failure-path testing looks like in this shape

Concretely: the `write_read` from the previous post, the one that
hands the controller IP a back-to-back boundary it doesn't like,
becomes a program I can run in a tight loop with the gap between
the STOP-equivalent boundary and the Sr controllable in
quarter-bit increments. I can sweep that gap, log which values
provoke `Restarted(0)`, and produce something a silicon vendor will
take seriously.

I can hold SCL high — or low — past the controller's stretch
timeout, on purpose, partway through a byte, and watch what the
driver does. Does the recovery path actually fire? Does the bus
come back to idle? Or do I end up wedged, with the target stuck
mid-bit waiting for a clock that's never coming back?

I can inject a STOP condition in the middle of a byte, before the
ninth (ACK) bit, and see whether the target's state machine resets
cleanly to idle or tries to interpret the fractional byte as data.
That last one is the kind of test that finds the assumption you
didn't write down — the implicit "a byte will always have nine
bits" baked into a couple of otherwise unrelated places in the
driver.

I³C is the same story with a different SDK namespace on top: SDR
and HDR-DDR are problems for Layer 1, and the techniques carry
across. The longer-term arc is I³C; the near-term one is I²C
failure paths.

## What this series will cover

This is post 2 of 6. The remaining four cover a repo tour and the
host-side toolchain, two deep dives on the engine itself — the ISA
and execution model in the first, the pipeline and timing
discipline in the second — and a worked-examples post that ends by
reproducing post 1's `Restarted(0)` case as a mole program at a
tight iteration rate. Each one stands alone; reading them in order
takes you from the *why* through the *how* to something you can
run on a Monday.

## What mole isn't

A few honest limits before this turns into a sales pitch.

It isn't HDR-Ternary. There's no analog I³C PHY in the design and
no plans to add one; SDR and HDR-DDR are the I³C scope, which is
the surface most embedded code interacts with anyway.

It isn't a substitute for the certification work that gates
shipping silicon to market. The compliance-grade rigs in the
earlier section exist because somebody needs to put their stamp on
the result. Mole is the tool an individual engineer reaches for to
test their driver as hard as they can, in their own workshop,
before any of that. It's the pre-screen, not the certificate.

And it isn't, today, a finished product. The engine is
bench-validated on iCEbreaker UP5K hardware; the host-side
toolchain is real enough to assemble bytecode and load it; a
higher-level SDK is on the roadmap, underway, not yet shipping.
What this series describes is what's running on my bench, plus the
shape of what's coming. The foundation has stopped moving in ways
that would make the writing obsolete a week later; the rest of the
posts are about what it's for.
