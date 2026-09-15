+++
title = "Six Entries, and the One I Argued Away"
date = 2026-09-23T09:00:00
draft = true
description = "A driver crate with a 'Not yet implemented' heading in its README, and what happened to it over three days: six entries down to one, plus a new heading for the item that turned out never to have been a gap at all. On documentation gaps that are two true sentences filed in different places, a caveat written with its own activation condition attached, and why 'not implemented' and 'not ours' have to be told apart by argument rather than by feel."
[taxonomies]
tags = ["embedded", "rust", "drivers", "documentation", "datasheets", "ina4230"]
+++

Most driver crates do not have a heading in the README that says
**Not yet implemented**. The ones that do usually grew it after
somebody filed an issue.

The [`ina4230`][ina] crate had one from the day the driver was
restructured, before anybody had asked for anything, because the
restructuring involved reading the whole datasheet and it seemed
dishonest to finish that and write down only the parts I had done.

Three days later the list is one entry long, and it has gained a
second heading underneath it for the item that turned out never to
have been a gap in the first place.

<!-- more -->

## What a completeness ledger is

The heading went in with six entries. Each one names the registers,
gives their addresses, and --- this is the part that turned out to
matter --- says why it is not done yet.

Here is the entry for alert limits, verbatim, from the first version
of the list:

> **Alert limits** (`ALERT_LIMIT1..4`, addresses `0x06`, `0x0E`,
> `0x16`, `0x1E`). The format follows the result register the
> selected alert function refers to: signed 16-bit for shunt limits,
> unsigned 15-bit for bus limits (bit 15 reserved), and unsigned
> 16-bit for power limits. Representing that reinterpretation safely
> is the interesting part of the design, and the reason these are not
> simply exposed as a `u16` setter.

That last sentence is the whole difference between a ledger and a
backlog. A backlog item says *this is missing*. This says what the
work actually is, why the obvious implementation is the wrong one,
and therefore what a reader should expect when it lands. Six months
from now, when I have forgotten all of it, that paragraph is the
thing that lets me start rather than re-derive.

It also has a less comfortable property. Having written down that a
`u16` setter would be wrong, I could no longer ship a `u16` setter
and call it progress.

## Three things the documentation did not say

Once the list existed, the obvious next move was to check it --- not
the code against the datasheet, which the restructuring had already
done, but the *documentation* against the datasheet. Where does
[SBOSAD4][ds] describe behaviour this crate exposes and the prose
does not explain?

Most of it came back clean. The register map, the signedness of every
measurement type, the LSB constants, the `CONFIG1` reset decode, and
the power and energy equations all checked out. That is worth saying
out loud, because an audit that finds nothing in five places and
something in three is a great deal more believable than one that
finds problems everywhere it looks.

Three things were missing, and the most interesting of them was not a
missing fact.

**The first was two facts that had never been introduced to each
other.** The README said, in one place, that the energy overflow bit
clears only through `CONFIG2.ACC_RST`. It said, in a different place,
that `ACC_RST` was not implemented. Both true, both written down, and
the conclusion --- *therefore an energy overflow is unrecoverable
through this crate* --- appeared nowhere, because no single paragraph
contained both halves.

Meanwhile the README's own worked example told the reader to check
`any_energy_overflow()` and act on it, and then offered no way to act.

That is the most expensive kind of documentation bug I know of. Every
individual sentence passes review. Nothing is factually wrong. A
reader with the whole document in their head could derive the
consequence, and no reader has the whole document in their head ---
that is what the document is *for*.

**The second was a caveat that could not yet bite.** The device
compares each alert limit against every conversion rather than
against the averaged result that reaches the output registers
(§6.3.5). So with averaging enabled, a limit flag can report an
excursion that appears in no value the crate can read back. A flag
disagreeing with the measurement registers is correct behaviour, and
somebody debugging that at two in the morning deserves to be told.

The note that went in recorded the caveat *and the conditions under
which it becomes reachable*:

> It cannot arise through this crate today, because `AVG` is not
> configurable here and the power-on default is a single sample, but
> it can if another controller on the bus has programmed `CONFIG1`.

Hold onto that one. It comes back.

**The third was silence, and silence turned out not to be neutral.**
High-speed I²C (§6.5.3) appeared nowhere in the repository. That
would be unremarkable except that the "Not yet implemented" list
deliberately enumerates the other two bus-level protocols the
datasheet describes --- SMBus Alert Response and General Call reset.

So a reader auditing the crate against the datasheet finds two of the
three bus protocols accounted for, and nothing at all about the
third. Having made a completeness claim, I had created an obligation
I did not know I had taken on: on a list that says what is missing,
an omission is indistinguishable from a gap nobody noticed.

## The entry that was a functional hole

Of the six entries, one was not a missing convenience. From the
commit that closed it:

> This was a functional hole rather than a convenience gap.

`CONFIG2.ACC_RST` resets a channel's energy accumulator and clears
its overflow flag. Without it, the trap closes like this. The
overflow bit is not read-to-clear. `ACC_RST` is the only thing that
clears it. So once a channel's accumulator wrapped, the flag was
stuck and the energy readings stayed wrong for the life of the
driver.

The only escape was `reset()`, which restores every register to its
default --- including `SHUNT_CAL`. And a channel with a zero
calibration register reports exactly zero current, indefinitely
(§8.1.2). So the recovery path for one channel's accumulator was: reset
the whole device, then recalibrate all four channels, or quietly
start reading zeros.

A full device reset and a four-channel recalibration, to clear
something the hardware clears in a single write.

The fix is one method:

```rust
pub async fn reset_energy_accumulators(
    &mut self,
    channels: &[Channel],
) -> Result<(), Ina4230Error<E>>;
```

A slice rather than a single `Channel`, because `ACC_RST` is a
four-bit mask and clearing three accumulators should be one bus write
rather than three. Duplicates fold together harmlessly. An empty
slice is an explicit no-op that does not touch the bus at all. And
there is deliberately no getter, because the bits self-clear --- this
is a command, not a setting, and a getter would only ever return
zero.

But the part worth stealing is not the method. It is what else had to
change in the same commit.

Three documentation claims became false the instant that code landed:
the rustdoc on `Flags::energy_overflow`, the rustdoc on `read_flags`,
and the README's "Reading flags" section all described energy
overflow as unrecoverable, or recoverable only through a full reset.
`examples/energy.rs` went further --- it *documented the dead end* as
part of the example, and now demonstrates the recovery instead,
invalidating its cached previous reading after the reset.

A ledger entry does not get deleted. It has edges. Everything that
referred to the gap has to stop referring to it in the same change,
or you have swapped a documented hole for an undocumented lie, which
is strictly worse than where you started.

The docs also picked up the arithmetic for avoiding the wrap in the
first place, since the answer depends on calibration. `ENERGY` is an
unsigned 32-bit accumulator whose LSB is $32 \times \mathrm{CURRENT\_LSB}$
joules. For a rail that can pull 2 A at 48 V and has to run for a
day, there are two independent floors --- one from the run time, one
from the current range --- and you take the larger and round up:

| Constraint | Floor |
|---|---|
| 24 h at 96 W | 60,350 nA/LSB |
| 2 A full scale | 61,036 nA/LSB |
| chosen | **62,500 nA/LSB** |

which buys 24.8551 hours before the accumulator wraps, and a maximum
current of 2.0479375 A. Both numbers are in the crate documentation,
because "size `CurrentLsb` appropriately" is not advice, it is a
homework assignment.

## The caveat that predicted its own activation

Remember the note about limit alerts disagreeing with averaged
readings --- the one that could not bite yet, because `AVG` was not
configurable.

Two entries later, `AVG` became configurable.

From that commit:

> `Flags::limit_alerts` documented that caveat as unreachable
> precisely because `AVG` was pinned at one sample. Making `AVG`
> configurable makes it live.

So the rustdoc and the matching README passage were rewritten in the
same change that made them wrong, and not because anyone remembered
they existed. They were found because the original note had recorded
*why* the caveat was dormant. The condition was written down next to
the consequence, so making the condition false pointed straight at
the text that needed to change.

This is the argument for citations in documentation, and it is not
the argument I expected to be making. Citing §6.3.5 is not about
looking rigorous. It is that a note carrying its own activation
condition is greppable, and a note that says "note: averaging may
affect alerts" is not. A `// TODO` would have done nothing at all
here: it names no condition, so nothing can ever make it fire.

## The one I argued away

The most interesting entry on a completeness ledger is the one you
take off it without implementing anything.

High-speed I²C is the third bus-level protocol in the datasheet, and
the natural move --- having listed the other two --- is to list it as a
third gap and feel thorough. That would have been wrong.

The device enters high-speed mode when a controller sends the
reserved master code `0b00001xxx`, and leaves it on the next stop
condition. Nothing in that sequence is addressed to the INA4230. The
part writes no register to participate. It switches its input filters
and gets on with it.

Which means participation is a property of whichever
`embedded-hal-async` implementation gets handed to `Ina4230::new`,
and not of this crate at all:

> It is a property of the bus, and therefore of whichever
> `embedded-hal-async` I²C implementation is passed to
> `Ina4230::new` --- not something this crate can offer or withhold.
> If the controller supports 2.94 MHz operation, this driver already
> works over it.

So it went under a new heading, **Out of scope**, which is the
accurate statement rather than a pending one.

The contrast is what makes this worth a section. SMBus Alert Response
and General Call reset stayed on the "Not yet implemented" list,
because those genuinely *are* this crate's to offer --- they are
addressed transactions the driver could issue and currently does not.
High-speed I²C is not. Three protocols, same datasheet chapter, same
apparent shape, and one of them belongs in a different category
entirely.

From the outside, "not implemented" and "not ours" look identical:
both are a feature you cannot call. A ledger that does not tell them
apart is just a list of things that make the crate look incomplete,
and it will accumulate items forever, because the set of things a
crate does not do is unbounded.

Telling them apart takes an argument each time. There is no rule. You
have to work out, for that specific feature, whether the crate is the
thing standing between the user and the capability --- and then write
the argument down, so the next person auditing against the datasheet
does not have to rediscover it and quietly re-add the entry.

## Done is a shape, not a number

The list today: one entry, holding the two bus protocols. Plus the
"Out of scope" section, holding its one argued-away item.

I do not think the value was in getting the number down. It was in
three other things.

**It is a map, and mostly for me.** Every entry that got closed was
closed starting from a paragraph explaining what the work was. The
alert-limits entry had already identified the hard part --- the
reinterpreting limit register --- which meant the design work started
from a real problem statement rather than from re-reading Table 7-8
to find out why I had skipped it.

**It turns "is this crate any good" into a diff.** Without the list,
that question is answered by vibes: how does the README feel, how
many downloads, is the author responsive. With it, the question is
mechanical. Take the datasheet, take the list, and see whether they
agree. That is checkable by someone who does not trust me, which is
the only kind of checkable that counts.

**It ends.** This is the part I did not anticipate and value most. A
backlog is unbounded, because features are unbounded. A datasheet is
finite. There is a last register, a last table, a last protocol, and
when you have accounted for all of them you are not "done" in any
grand sense but you are done *with that question*, and the question
stops consuming attention.

The honest limits, since this post has been fairly pleased with
itself. The ledger is exactly as good as the reading that produced
it, and it is a claim that I found everything --- checked against a
document that has its own errors, which is not a hypothetical
concern.[^rev] An audit against one revision of a datasheet says
nothing whatsoever about the next. And a list of gaps is not a list
of bugs: everything on it was, by construction, something I knew
about. The failures that hurt are the ones where the code and the
datasheet disagree and I read straight past it.

A datasheet is finite. Almost nothing else in this job is, and it is
worth taking the win.

[^rev]: The driver before this one had been decoding the TMP108
datasheet's own stated power-on value into a temperature the part
cannot produce, for about a year, which is the subject of
[the previous post](@/posts/thirty-six-registers/index.md). An audit
against a document is not an audit against reality. It is just
considerably cheaper than the alternative, and it is the one you can
do on a Sunday.

[ina]: https://github.com/OpenDevicePartnership/ina4230
[ds]: https://www.ti.com/lit/ds/symlink/ina4230.pdf
