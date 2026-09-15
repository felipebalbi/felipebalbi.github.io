+++
title = "The Spec Outlived the Session"
date = 2026-09-30T09:00:00
draft = true
description = "More than half the lines in a recent driver change were the design document and the implementation plan, not the Rust. What that bought: a later commit, in a later session with no memory of the first, could say 'the shape is as approved' and mean something checkable. Also the two things the agent got wrong, the attribution trailer OpenDevicePartnership requires, and why an agent is forbidden from signing off."
[taxonomies]
tags = ["rust", "embedded", "drivers", "agents", "documentation", "process", "ina4230"]
+++

Here is the trailer at the bottom of a commit in the [`ina4230`][ina]
driver:

```
Assisted-by: OpenCode:claude-opus-5 [pdftotext]

Co-authored-by: Felipe Balbi <febalbi@microsoft.com>
```

Two things about it are unusual. It is not `Co-authored-by`, which is
what most projects reach for. And it has a list of tools in brackets,
which is the part that does the work.

<!-- more -->

## What is actually in the repository

The change that added alert configuration to the driver touched
eleven files. Two of them were prose:

| File | Lines added |
|---|---|
| `docs/superpowers/specs/2026-09-14-ina4230-alert-limits-design.md` | 372 |
| `docs/superpowers/plans/2026-09-14-ina4230-alert-limits.md` | 1,561 |

Out of 3,524 insertions in the whole commit, 1,933 were the design
document and the implementation plan. More than half of the change,
by line count, was the argument rather than the code.

Both are committed into the driver repository, next to the source.
Neither ships: `docs/` sits outside the `include` list in
`Cargo.toml`, which the change verified with `cargo package --list`
rather than assuming. Someone who runs `cargo add ina4230` gets none
of it. Someone who clones the repository gets all of it.

That split is deliberate, and it took me a while to be comfortable
with it. A design document is not documentation. It is not for users
and it does not describe the crate as it exists --- it describes an
argument that happened once, at a particular time, about a change
that was not yet made.

## The spec outlived the session

Section 9 of that design document is titled "Implementation order",
and it says to do the work in two steps: the alert slots and limits
first, carrying all of the risk and all of the breakage, and the
`CONFIG2` alert-pin configuration second, because deferring it costs
nothing in semver terms and keeps the first change reviewable.

Step 1 shipped. Three commits later, step 2 shipped, and the commit
message for it says:

> This is step 2 of the design approved during #14 and recorded in §6
> of `docs/superpowers/specs/2026-09-14-ina4230-alert-limits-design.md`,
> held back then to keep that change reviewable, and the shape is as
> approved.

That was a different session. No memory of the first one, no
conversation history, nothing carried over. The only thing connecting
the two was a file in version control that the second session read.

I think this is the actual answer to working with an agent across
more than one sitting, and it is disappointingly unglamorous. Not
memory features, not a longer context window, not a vector database
of past conversations. A document, in the repository, that the next
session reads because you told it to.

Which is, of course, exactly the answer for humans. The same document
does the same job when the collaborator is a person who was on
holiday, or me in March. The agent case is just the one where the
amnesia is total and reliable, so the failure shows up immediately
instead of six weeks later.

The clause that matters is "and the shape is as approved". That is a
falsifiable claim. Anybody can open §6, read the struct definition it
specifies, and compare. Without the committed document that sentence
would be a reassurance. With it, it is a diff.

## Writing down the roads not taken

Section 2 of the design lists two alternatives that were considered
and rejected: a separate `AlertFunction` enum paired with an
`AlertThreshold` union, validated at the call site; and a typestate
builder. Each gets a sentence on what it would have bought and a
sentence on why it lost.

Nobody asked for those paragraphs. No reviewer was going to propose
either one.

They are there because the next session will propose them again. An
agent starting fresh on this code will re-derive the same small set
of options every time, because they are the obvious options --- that
is why they are obvious. Without a record, every future conversation
about alert configuration starts by re-litigating the union type, and
I pay for that rediscovery in review attention, every time, forever.

The naming argument is the cheapest example and my favourite. Alert
slots are named `One` through `Four`, with no prefix, because the
datasheet calls the registers `ALERT1..ALERT4` and never calls them
S-anything. Channels keep theirs --- `Channel::Ch1` --- because *that*
prefix is not invented either: the datasheet labels channels CH1 and
bakes it into register names like `SHUNT_VOLTAGE_CH1`.

Two sentences. Without them, the prefix question comes back every
time somebody looks at the file, including when that somebody has no
memory and infinite patience.

## Two things it got wrong

The useful part of this post, and the reason I am reasonably
confident the process is doing something rather than just producing
impressive-looking text.

Both of these surfaced between "design approved" and "start editing"
--- during the writing of the implementation plan, which is the step
that turns nine numbered sections of prose into fourteen tasks with
tests attached. Both are recorded in the commit message, because a
self-review that finds nothing worth reporting is a self-review that
did not happen.

**The first was a missing conversion.** The generated
`ALERT_CONFIG.CHANNEL` field takes its own `AlertChannel` enum ---
not the `device::Channel` used to index the per-channel register
block. The design had been written from the datasheet, where there is
only one notion of a channel, and had not called for a second `From`
impl. From the commit:

> Without it the `set_alert` task would not have compiled.

So the failure mode was benign: the very first task would have
stopped. But it is a clean illustration of where a datasheet-derived
design goes wrong --- the datasheet does not know what the code
generator did, and a document written only from the datasheet
inherits that blind spot.

**The second was a rounding helper that overflows.** The plan needed
round-to-nearest division, and the first version was written the way
everybody writes it:

```rust
(n + d / 2) / d
```

Power thresholds are full-range `u64`. Near `u64::MAX` that sum
wraps, and the function quietly returns a small number instead of a
large one.

This is the one that would have hurt. It is correct in almost every
context anybody has ever used it in, it is the idiom you recognise
rather than read, and it would have sailed through review --- mine
included --- precisely because the shape is so familiar. The only
reason it did not is that the plan step forced someone to state the
input domain out loud, and `u64` thresholds are a full-range input
domain.

I want to be careful about the size of this claim. Two problems were
caught. I have no idea how many were not, and no way to find out
except by shipping and waiting. The honest version is: writing the
plan is a cheap step that found two real bugs before any code
existed, and that is worth doing. It is not a guarantee of anything.

## What the trailer should say

The `Assisted-by` format is not something I invented for this crate.
It is an [OpenDevicePartnership][odp] convention, defined in the
repository's `.github/copilot-instructions.md`:

```
Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
```

Three of its rules are worth pulling out, because each encodes a
judgement I would not have arrived at on my own.

**The bracketed tools are specialised tools only.** The instructions
are explicit that git, cargo and editors do not belong there. So
`[pdftotext]` in that trailer means something specific: the datasheet
PDF was converted and read, rather than recalled. `[device-driver-cli]`
on an earlier commit means the register layer was regenerated rather
than hand-edited.

That is an evidence claim rather than a credit claim, and it is the
part a reviewer can act on. If I want to check a datasheet citation,
`[pdftotext]` tells me the document was actually opened. If I want to
check a change to the generated register layer, `[device-driver-cli]`
tells me to re-run the generator and diff rather than reading 658
lines of generated Rust.

**The agent has to check which model it is.** From the instructions:

> AI agents **must** verify their own identity (agent name and model
> version) before composing the `Assisted-by` trailer --- do not
> assume or hard-code a model name from a previous session.

Which sounds fussy until you notice that a model confidently writing
down the name of a different model is exactly the kind of error this
whole trailer exists to prevent.

**And an agent may not sign off.** This is the sharpest line in the
document:

> AI agents **MUST NOT** add `Signed-off-by` tags. Only humans can
> certify the Developer Certificate of Origin.

`Signed-off-by` is not credit. It is a legal certification about
provenance and the right to submit. There is no coherent sense in
which a model certifies anything, so the boundary is not a matter of
taste or of how much you trust the output --- it falls out of what
the tag means.

I find that a much better organising principle than the usual
argument about how much of the code an AI "wrote". `Co-authored-by`
implies a peer you could ask about the change next year. `Assisted-by`
with a tool list implies a process that produced the change, and
tells you what to re-run to check it. The second is true, and more
useful.

## What I would not claim

The commit messages in this repository read well. Several of them
explain a datasheet subtlety more clearly than the datasheet does.

That is not evidence they are right, and I want to say so plainly,
because a confident, well-structured, correctly-cited *wrong*
explanation is the specific failure mode of this way of working. It
is more dangerous than a badly written one, for the obvious reason:
it survives review. Fluency is not accuracy, and here it is
uncorrelated enough that treating one as a proxy for the other would
be a mistake.

So the load-bearing artifacts are not the prose. They are the things
a machine can check: the exhaustive domain walks, the properties
asserting that a given failure is the *only* failure, and above all
the test vectors taken from TI's worked examples rather than from the
implementation. A test transcribed from the code it tests proves only
self-consistency, and a test transcribed from the code by something
that also wrote the code proves rather less than that.

One practice is worth more than the rest, and it is not glamorous
either. From the plan:

> Each checked against a reverted fix to confirm it catches the
> regression.

Write the test, break the code on purpose, watch the test fail, put
the code back. That is the only step in this entire process that
establishes a test is capable of failing --- and a test that cannot
fail is indistinguishable, in a green CI run, from one that works.

And the limits of all of it: one part, one crate, one person doing
the reviewing, over four days. Nothing here is a study. It is a
description of what the artifacts looked like, which is the most I
can honestly offer and rather more than most write-ups of this kind
bother to include.

The agent wrote the argument. The tests are what make the argument
falsifiable, and those I still read line by line.

[ina]: https://github.com/OpenDevicePartnership/ina4230
[odp]: https://github.com/OpenDevicePartnership
