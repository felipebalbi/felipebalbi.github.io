+++
title = "CRC-16 is not a specification"
date = 2026-09-09T09:00:00
draft = false
description = "Hold the polynomial fixed at 0x1021, vary only the initial value, the output mask and the bit ordering, and the same nine bytes produce twelve different check values — eleven of which are catalogued standards. What a CRC is actually parameterised by, the four notations for writing down the same polynomial, and why none of it amounts to an integrity check in the security sense."
[taxonomies]
tags = ["crc", "embedded"]
+++

A datasheet says CRC-16. You implement CRC-16. The numbers do not match, and
neither implementation has a bug.

<!-- more -->

## Twelve answers, one polynomial

Start with the evidence, because it is more convincing than any amount of
explanation.

Fix the generator polynomial at `0x1021`. Hold the input fixed too, at the nine
ASCII bytes `"123456789"`. Now vary only three things: the value the register
starts at, the constant XORed into the result at the end, and whether bits are
fed in and read out reversed.

| Init     | XorOut   | Reflected | Check    | Catalogued name          |
|----------|----------|-----------|----------|--------------------------|
| `0x0000` | `0x0000` | no        | `0x31C3` | CRC-16/XMODEM            |
| `0x0000` | `0x0000` | yes       | `0x2189` | CRC-16/KERMIT            |
| `0x0000` | `0xFFFF` | no        | `0xCE3C` | CRC-16/GSM               |
| `0x0000` | `0xFFFF` | yes       | `0xDE76` | *(unnamed)*              |
| `0xFFFF` | `0xFFFF` | no        | `0xD64E` | CRC-16/GENIBUS           |
| `0xFFFF` | `0xFFFF` | yes       | `0x906E` | CRC-16/IBM-SDLC          |
| `0xFFFF` | `0x0000` | no        | `0x29B1` | CRC-16/IBM-3740          |
| `0xFFFF` | `0x0000` | yes       | `0x6F91` | CRC-16/MCRF4XX           |
| `0x1D0F` | `0x0000` | no        | `0xE5CC` | CRC-16/SPI-FUJITSU       |
| `0x89EC` | `0x0000` | yes       | `0x26B1` | CRC-16/TMS37157          |
| `0xB2AA` | `0x0000` | yes       | `0x63D0` | CRC-16/RIELLO            |
| `0xC6C6` | `0x0000` | yes       | `0xBF05` | CRC-16/ISO-IEC-14443-3-A |

Twelve different answers. One polynomial, one input string. Eleven of the twelve
are catalogued, named standards, and none of them is wrong.

The first eight rows are Appendix A of Greg Cook's [CRC RevEng
catalogue](https://reveng.sourceforge.io/crc-catalogue/legend.htm), a Karnaugh
map that sweeps Init and XorOut over `0x0000` and `0xFFFF` and toggles
reflection. It exists precisely because this happens to people. The last four
are the remainder of the `0x1021` row of Appendix B on the same page, and they
earn their place by being absent from that map for exactly one reason: their
initial values are not `0x0000` or `0xFFFF`. Appendix A is a map of *common*
algorithms, and common is not complete.

Two notes on reading the table. The `Reflected` column collapses two separate
parameters, input and output reflection, because all twelve models set them
together --- they do not have to be equal, and the catalogue has entries where
they are not. And twelve is a selection rather than a limit: these are the
configurations somebody wrote down and shipped, not the configurations `0x1021`
admits.[^space]

So "CRC-16" does not identify an algorithm. It identifies a register
width. Everything that determines the actual number is somewhere else.

## What a CRC is actually parameterised by

The model below is Ross Williams' parameter set from *A Painless Guide to CRC
Error Detection Algorithms* (1993), conventionally called the Rocksoft model, as
given definitions by the RevEng catalogue. Six parameters specify a CRC
completely. Two more are derived, and exist to check your work.

**Width** is the number of bit cells in the shift register. This is the *n* in
"n-bit CRC", and the width of the check value. It is the only thing the name
"CRC-16" tells you.

**Initial value** is what the register holds before the first message bit is
read. Because the whole computation is built out of exclusive-or, that starting
value propagates through and changes the result.

**Input reflection** decides whether each character is read into the register
most significant bit first, or least significant bit first.  Either way the
sampled bit is XORed with the bit leaving the top of the register, and the
result drives the feedback taps. The reason the parameter exists at all is that
serial protocols disagree about which end of a byte goes on the wire first, and
reflecting the input lets one register topology serve both conventions.

**Output reflection** decides whether the register contents are reversed after
the last message bit, before the value is presented.  The reversal swaps each
cell with the one an equal distance from the other end.

**Output exclusive-or** is a constant XORed into the result on the way
out. Setting it to zero disables the step.

**Polynomial** is the one that looks simplest and causes the most confusion,
because there are four notations for it.

### Four ways to write the same polynomial

A generator polynomial of degree *n* has *n+1* coefficients, and both the
highest and the lowest are always 1 in a valid generator. Since each is
guaranteed, either can be left implicit --- and different communities picked
different ones. A fourth form drops nothing and reverses the bit order instead,
to suit code that shifts right rather than left. For CRC-32, via Koopman's [CRC
Polynomial Zoo](https://users.ece.cmu.edu/~koopman/crc/crc32.html):

| Notation        | CRC-32 constant | Derivation                              |
|-----------------|-----------------|-----------------------------------------|
| Full polynomial | `0x104C11DB7`   | all *n+1* coefficients, nothing hidden  |
| Direct / normal | `0x04C11DB7`    | drop the implicit high term             |
| Koopman         | `0x82608EDB`    | drop the implicit low term, i.e. `>> 1` |
| Reflected       | `0xEDB88320`    | bit-reverse the direct form             |

All four rows are the same polynomial. `0x104C11DB7 >> 1` is `0x82608EDB`, which
is how the Koopman form is built and why it fits in 32 bits despite describing a
degree-32 polynomial. The RevEng catalogue's own `poly` field uses the direct
form, where it is the *highest* term that is omitted.

If you have ever seen the same CRC written as `0x04C11DB7` in one place and
`0xEDB88320` in another and assumed one was a typo: neither was.

### Check and residue

Two derived values come with every catalogued model, and both are for
verification rather than specification.

**Check** is what the model produces for `"123456789"`. It is the standard smoke
test --- an implementation that reproduces the published check value is very
probably computing the CRC it claims to be, though one test vector is a smoke
test and not a proof. It is also the column that makes the table at the top of
this post legible.

**Residue** is what the register holds after reading an error-free message with
its own CRC appended, after output reflection but before the output
exclusive-or. When the output mask is zero the residue is zero, and some
receivers use that directly: run the CRC across message-plus-CRC and check the
register drove to zero, instead of computing a CRC and comparing it.

## The order of the last two steps

The parameters apply in a fixed order:

1. Set the register to the **initial value**.
2. Read the message bits, most significant first or least significant first
   according to **input reflection**, XORing each with the bit leaving the top
   of the register and feeding the result to the taps selected by the
   **polynomial**.
3. Reflect the register if **output reflection** is set.
4. XOR with the **output exclusive-or** constant.

Steps 3 and 4 are written in that order, but they only *matter* in that order
some of the time, and the exception is worth knowing because it makes the bug
hard to find.

Reflection is a bit permutation and XOR is bitwise, so reflecting after masking
distributes: `reflect(x ^ k)` equals `reflect(x) ^ reflect(k)`. Whenever
`reflect(k)` equals `k` the two steps commute and their order is
unobservable. That covers `0x0000` and `0xFFFF` --- which is to say, it covers
every model in the table at the top of this post, and most of the catalogue.

With a mask that reflection does change, they diverge. Take `poly=0x1021`,
`init=0xFFFF`, reflected, and set the mask to `0x0001`: the two orderings give
`0x6F90` and `0xEF91`.

So an implementation that swaps steps 3 and 4 is not obviously broken. It agrees
with every other implementation until someone configures a mask that is not a
palindrome, and then disagrees for reasons that look unrelated to the change
that exposed them.

## None of this is a security property

Worth stating plainly, because a CRC field in a packet header looks like an
integrity check.

CRCs are designed to protect against the common types of error that occur on
communication channels. They are not designed to protect against malicious
alteration of data, for an immediate reason: an attacker who modifies a block
simply recalculates the CRC over the modified block and attaches that
instead. The only thing this requires is that the attacker knows, or can learn,
the parameters of the CRC in use.

Note that obscure parameters would not rescue it. The CRC is unkeyed --- there is
no secret in the computation, only configuration --- so its security rests
entirely on the attacker not knowing which of a small number of published
configurations you chose. That is not a security property; it is a delay.

A CRC can tell you that bits changed on the way, for the error patterns its
generator is chosen to catch. It tells you nothing about whether someone meant
them to.

## Where the mathematics lives

Nothing above explains *why* any of this works --- why a CRC is a polynomial
remainder, why the shift-and-XOR inner loop is division, or why appending a CRC
to its own message drives the residue to zero. That is worked through end to end
against a concrete CRC-8 implementation in [Tamal: The CRC
block](@/posts/tamal-crc/index.md).

This post is about the other half: knowing the mathematics does not help you if
the machine at the other end of the wire initialised its register to `0xFFFF`
and you initialised yours to zero.

[^space]: With the polynomial still pinned to `0x1021`, the free parameters are
a 16-bit initial value, a 16-bit output mask and two independent reflection
flags: 65,536 × 65,536 × 2 × 2, or 17,179,869,184 combinations. Combinations,
not answers. A check value is sixteen bits wide, so there are only 65,536
answers to go round, and the space collapses onto them exactly 262,144 to one.
The collapse is not mysterious. The last step is `register ^ XorOut`, and
`x ^ 0 = x`, so a zero mask is not a transformation but the absence of one ---
which is why the rows above with `XorOut = 0x0000` report the register
untouched, and why every other mask value merely translates an answer that Init
had already fixed. That is also why nothing is out of reach. Hold Init and both
reflections wherever you like, sweep XorOut across its sixteen bits, and the
check value takes all 65,536 values in turn. Every 16-bit number is some model's
check for `"123456789"`. A check value tells two *catalogued* models apart; it
is not evidence that a model was ever catalogued.
