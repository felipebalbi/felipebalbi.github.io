+++
title = "Lambda in types, part 9: Nothing, carefully implemented"
date = 2027-01-20T09:00:00
draft = true
description = "Reusable cons cells, named projections, and a compatible empty list. Nil finally exists, and it is not false, zero, or permission to project a nonexistent field."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

Last week we hand-built two cells. This week we will manufacture them
on demand and introduce the empty list. At long last, the project has
an explicit representation for what it has accomplished in practical terms.

Our `Nil` will be a lambda term. So will its emptiness test. We will not
ask Rust to recognize a special empty type, because that would deprive us
of a perfectly good opportunity to make a function ignore another function.

<!-- more -->

**Part 9 of 16.** [Previous: cells](/posts/lambda-types-08-cells/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: navigation](/posts/lambda-types-10-navigation/).

## The constructor builds the function we already know

A cell containing `a` and `d` has this shape:

```text
λselect. select a d
```

Instead of writing the fields into every cell, accept them as arguments:

```text
CONS = λa. λd. λselect. select a d
```

Supply two arguments and we get a function still waiting for its selector.
That is precisely the protocol of last week's concrete cells. The closure
remembers the fields using its environment; we need no constructor rule
in the evaluator.

Inside the body, `select` is index zero, `d` is one, and `a` is two:

```rust
type Cons = Lambda<Lambda<Lambda<
    App<App<Var<Z>, Var<S<S<Z>>>>, Var<S<Z>>>
>>>;
```

The order matters: the selector is applied to `a` first and `d` second.
We have now automated our manual inconvenience.

## Give the projections their traditional names

Selecting a field meant applying a cell to true or false. Put a lambda
around each pattern:

```text
CAR = λcell. cell TRUE
CDR = λcell. cell FALSE
```

```rust
type Car = Lambda<App<Var<Z>, True>>;
type Cdr = Lambda<App<Var<Z>, False>>;
```

These work with both constructed cells and the old literals:

```text
CAR (CONS A B) → A
CDR (CONS A B) → B
```

They don't inspect a Rust struct. They apply a function to another
function. Their names are becoming pleasantly Lispy while their
implementation remains aggressively unhelpful to a debugger.

## Design nothing and its detector together

We want to distinguish an empty list from a cons cell without inspecting
the cell's fields. Give a candidate this consumer:

```text
λa. λd. FALSE
```

A cell calls it with two fields, after which it yields false. Choose
`Nil` to ignore the consumer and yield true instead:

```text
NIL    = λignored. TRUE
IS_NIL = λcell. cell (λa. λd. FALSE)
```

The two cases behave differently under the same observation:

```text
IS_NIL NIL
  → NIL (λa. λd. FALSE)
  → TRUE

IS_NIL (CONS A B)
  → (λa. λd. FALSE) A B
  → FALSE
```

The consumer needs *two* lambdas before false. A cell offers two fields;
we must accept both before supplying the answer. Simply choosing a field
would report whatever happened to be stored there. That would be less an
emptiness test than a random survey of the contents.

In Rust:

```rust
type Nil = Lambda<True>;
type IsNil = Lambda<App<Var<Z>, Lambda<Lambda<False>>>>;
```

No special `Nil` struct. No type comparison. No runtime tag.

This particular nil is not our false or our zero. Those two were the
same lambda term; nil has a different consumer protocol. Borrowing Lisp
terminology doesn't obligate us to reproduce every Lisp dialect's truth
conventions.[^dialects]

## A container of nothing still contains something

Consider `CONS NIL NIL`. It is a nonempty cell. Its first field holds
nil; its second field holds nil. Viewed as a proper list, it has one
element, which is an empty list:

```text
IS_NIL (CONS NIL NIL)       → FALSE
IS_NIL (CAR (CONS NIL NIL)) → TRUE
IS_NIL (CDR (CONS NIL NIL)) → TRUE
```

The distinction is the presence of the cell, not whether its fields
contain exciting news. This example catches a surprisingly plausible
mistake: implementing the emptiness test as a projection.

The tests also use cells with boolean fields and divergent fields.
An emptiness test should answer about the outer shape without interviewing
either occupant.

## At last, a proper list

A proper list is either nil or a cell whose cdr is another proper list.
The car can hold any lambda value. For example:

```text
CONS TRUE (CONS FALSE NIL)
```

The source names that expression:

```rust
type BooleanList = App<App<Cons, True>, App<App<Cons, False>, Nil>>;
```

Following its fields gives:

```text
CAR BooleanList                → TRUE
CAR (CDR BooleanList)          → FALSE
IS_NIL BooleanList            → FALSE
IS_NIL (CDR BooleanList)       → FALSE
IS_NIL (CDR (CDR BooleanList)) → TRUE
```

The false element is data. The nil tail ends the list. Treating them as
interchangeable would prematurely end some very short conversations.

These lists are linked chains of functional pairs with a chosen empty
case. They are not the fold-encoded representation usually meant by
“[Church lists][wiki-lists].”[^lists] Numerals being Church-encoded doesn't require
every other data structure to share a surname.

Nor does `Cons` require list-shaped fields. `CONS ONE TWO` remains a
dotted pair. A cell on each side makes a branching tree. `IsNil` says
whether the candidate follows the empty or nonempty case at that point;
it doesn't traverse a whole structure to validate its credentials.

## Don't project nothing

`Car` and `Cdr` have a **nonempty-cell precondition**. A program handling
possibly empty input must select the empty case before demanding a field.

Mechanically, `Car Nil` and `Cdr Nil` both expose true under these raw
definitions, because nil ignores the supplied consumer. That is not a
field, not an exception, and not a supported empty-list accessor result.
It is the incidental behavior of applying these terms outside their
intended contract.[^partial]

We could define total accessors with explicit fallbacks later. We haven't.
The type system being involved does not automatically make every bad
request meaningful. This is especially worth saying in a project whose
central premise is a bad request.

## The fields remain asleep until needed

Construction does not force either field. Projection needn't force the
other one. The emptiness probe needs neither:

```text
IS_NIL (CONS Ω Ω) → FALSE
NIL Ω            → TRUE
```

The first answer identifies a nonempty cell without evaluating its
disastrous contents. The second confirms that nil really ignores its
consumer. We also test computed fields, nested cells, both list elements,
and the final nil tail through the existing behavioral probes.

The [source archive](source.tar.gz), snapshot `0bd6e9a59865`, contains all
five definitions and their checks. Run `cargo test --offline` and
`cargo run --offline` after extracting and entering `rust-lambda-types`.
The executable still prints observation types, not complete lists.

Next week a numeral will repeatedly take a cdr. We will call this
navigation rather than “making the number do the walking,” although
that is very much what we are doing.

[wiki-lists]: https://en.wikipedia.org/wiki/Church_encoding#Church_lists_%E2%80%93_right_fold_representation

[^dialects]: Common Lisp identifies its empty list with false; Scheme
    distinguishes the empty list from its false boolean. Our tiny language
    makes its own explicit choice and doesn't claim either dialect's full
    semantics. `Nil = λignored. True` must be understood together with
    this particular `IsNil` probe.

[^lists]: A fold-encoded list receives operations describing how to combine
    elements. Our cell instead exposes its two immediate fields to a
    consumer. The cdr then holds another list value. Both are functional
    representations; their observation protocols are different.

[^partial]: “Partial” here describes the contract of these library terms.
    The evaluator may still reduce an out-of-contract expression to some
    closure. That does not turn the closure into a supported field value,
    any more than obtaining an answer from a broken measuring instrument
    makes the answer a measurement.
