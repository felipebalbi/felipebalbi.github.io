+++
title = "Lambda in types, part 8: Two things, no tuple"
date = 2027-01-13T09:00:00
draft = true
description = "A functional pair hands two fields to a consumer. We build concrete cells, select their fields with Church booleans, and discover a test probe that was rather too easily pleased."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

After seven posts, our language can produce six. Clearly it is time to
scale horizontally. We shall store **two things**.

Rust has tuples for this. They are excellent. We will instead write a
function that waits for another function and gives it the two things.
No, this does not improve the tuple. The tuple is fine. Please leave the
tuple out of this.

<!-- more -->

**Part 8 of 17.** [Previous: multiplication](/posts/lambda-types-07-multiplication/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: constructors and nil](/posts/lambda-types-09-lists/).

## A pair without a record

A [cons cell][wiki-cons] has two fields, traditionally called **car** and **cdr**.
The dotted notation `(A . B)` means a cell whose first field is `A` and
second field is `B`.[^names]

This is not necessarily a two-element list. Either field can hold any
value in our lambda language. The second can be another cell, a numeral,
or a function with no particular interest in being data. We haven't even
chosen an empty-list value yet.

How can we retain two fields without adding a cell primitive to the
evaluator? Make a function that waits for a consumer and applies that
consumer to both fields:

```text
CELL = λselect. select A B
```

For our first cell, use true and false as the fields:

```text
BooleanCell = λselect. select TRUE FALSE
```

This is a particular cell, not a constructor. Its fields are written into
the expression. Reusable construction comes next week, because apparently
even a pair needs a release train.

The only parameter is `select`, so its reference has index zero:

```rust
type BooleanCell = Lambda<
    App<App<Var<Z>, True>, False>
>;
```

Every piece is still syntax we already have. There is no `Cell<A, B>`
primitive, no new `Evaluate` implementation, and no runtime allocation
of a two-pointer record. The diagram of a pair is a diagram of the
encoding, not a statement about Rust memory layout.

## Our booleans have been waiting for this

We need a consumer that accepts two arguments and chooses the first.
That is true. We need another that chooses the second. That is false.
The hiring process is over; both candidates already work here.

```text
BooleanCell TRUE
  → TRUE TRUE FALSE
  → TRUE

BooleanCell FALSE
  → FALSE TRUE FALSE
  → FALSE
```

Don't let the repeated boolean names obscure the roles. The argument
supplied to the cell is the **selector**. The two values inside the cell
are its **fields**. They happen to be booleans in this example, but the
selector does not require that.

```rust
type FirstField = App<BooleanCell, True>;
type SecondField = App<BooleanCell, False>;
```

We haven't named these operations `Car` and `Cdr` yet. Today we're
performing the applications directly so there is nowhere for an alleged
projection primitive to hide.

## The consumer needn't pick a field

The cell doesn't perform a boolean test on its argument. It simply calls
that argument with both fields. We can supply `And`:

```text
BooleanCell AND
  → AND TRUE FALSE
  → FALSE
```

This is a useful way to understand the encoding, even though “useful”
should not be taken as a description of the overall project. The cell
offers two values to a consumer. Projection is just one possible consumer
behavior. A consumer that needs both fields may demand both; the pair is
not limited to a two-position switch.

## Put a cell in a cell

Our next value contains the numeral one and the boolean cell:

```text
NestedCell = λselect. select ONE BooleanCell
```

In dotted notation, its shape is `(ONE . (TRUE . FALSE))`. The second
field is a cell, not a nil-terminated list. In Rust:

```rust
type NestedCell = Lambda<
    App<App<Var<Z>, One>, BooleanCell>
>;
```

Select the first field for the numeral, or select the second and apply it
again to reach either boolean:

```text
NestedCell TRUE          → ONE
(NestedCell FALSE) TRUE  → TRUE
(NestedCell FALSE) FALSE → FALSE
```

No special nested-cell rule appears. The selected inner value is itself a
function, and application remains application. This is the entire trick
again, now with furniture inside the furniture.

## The test that liked the wrong answer

As usual, selected fields can expose closures with extra captured
environments. Comparing them to a literal's exact closure type would be
too strict. We need behavioral observations.

For boolean fields, offer the familiar two function markers. For `One`,
use the exact-count wrapper probe: one iteration wraps identity once,
and one peel must expose identity again.

There is a catch. `BooleanCell` also passes that one-wrapper probe.

Work through it. The probe supplies `True` and `Identity` to its candidate:

```text
BooleanCell TRUE Identity
  → TRUE Identity
```

After the cell first selects its stored `True`, the extra `Identity`
argument leaves a constant-function wrapper. One peel exposes identity.
Our very specific observation can't distinguish that behavior from one.
If we only used this probe, swapping `NestedCell`'s fields could escape
detection. The test was exact about one observation and insufficient
about the value's intended use.[^probes]

The numeral field therefore gets another check: use it to iterate `Not`
from false. `One` must produce true. `BooleanCell` does not agree on that
observation. Together with the nested-field checks, this catches the swap.

This doesn't invalidate the wrapper tests for our numeral fixtures. It
does invalidate the fantasy that one successful probe is a universal
type classifier. We have an untyped language, not an unusually verbose
passport office.

## The unselected field can be terrible

[Call-by-name][wiki-name] application lets a consumer ignore a field without evaluating
it. These cell literals can safely expose the other field:

```text
FirstOnly  = λselect. select FALSE Ω
SecondOnly = λselect. select Ω TRUE

FirstOnly TRUE   → FALSE
SecondOnly FALSE → TRUE
```

`Ω` is our divergent self-application term. The tests place it on both
sides, so selecting either field is checked independently. A consumer
that demands the divergent field will still fail to finish. Functional
packaging isn't a containment policy for infinite computations.

## A pair, not yet a list

The [source archive](source.tar.gz), snapshot `3fd4f3c9c0d9`, contains the
concrete cells, the consumer using both fields, the nested checks, and
the lazy projections. Extract it and run `cargo test --offline` and
`cargo run --offline` in `rust-lambda-types`.

The program still prints selected observation types, not dotted pairs.
There is no generic `Cons`, no named projections, and no `Nil` in this
snapshot. In particular, the false field at the end of `NestedCell` is
ordinary data, not an empty-list marker we forgot to introduce.

Next week we will abstract the constructor and choose an empty case.
Then we'll have lists, and the privilege of reaching the end of one.
It is important to establish that endings are possible in this series.

[wiki-cons]: https://en.wikipedia.org/wiki/Cons
[wiki-name]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_name

[^names]: The historical names `car` and `cdr` come from IBM 704 register
    terminology used by early Lisp, not from an essential property of pairs.
    For the original discussion, see McCarthy's [1960 Lisp paper](https://www-formal.stanford.edu/jmc/recursive.html).
    We borrow the names and dotted-pair notation, not that machine's layout
    or a complete Lisp implementation's semantics.

[^probes]: Agreement under a finite set of observations is weaker than
    general equivalence of untyped terms. Our tests need to distinguish
    the plausible wrong values in these examples. They do not prove that
    an arbitrary function belongs to an exclusive “numeral” or “cell” class.
    The overlap is real, not something a more emphatic alias name fixes.
