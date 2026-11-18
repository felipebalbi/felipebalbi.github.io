+++
title = "Lambda in types, part 10: These numbers were made for walking"
date = 2027-01-27T09:00:00
draft = true
description = "Repeat cdr with a Church numeral to skip cells, then take car to select an element. Off-by-one errors remain available despite the absence of machine integers."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

We have numbers that repeat functions, and a function that selects the
tail of a list. Combining them gives us list navigation. You might think
this means today's implementation will be short. It does. I have decided
to compensate by discussing the bounds.

There will be no runtime indexing, recursive Rust trait for lists, or
host integer used as a loop count. A [Church numeral][wiki-numerals] will provide the
repetition. It has already been a boolean flipper and an arithmetic
supervisor; today it gets sent out for a walk.

<!-- more -->

**Part 10 of 16.** [Previous: lists](/posts/lambda-types-09-lists/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: recursion](/posts/lambda-types-11-recursion/).

## The same iterator, again

Two still means:

```text
TWO f x → f (f x)
```

Give it `Cdr` as the step and a list as the starting value:

```text
TWO CDR xs → CDR (CDR xs)
```

Two steps through the cdr chain. `Two` doesn't need to know about lists;
`Cdr` doesn't need to know how many steps we plan to take. No committee
meeting is necessary between the abstractions.

This is the fourth job for the same numeral protocol:

| Step | Base | What two does |
| --- | --- | --- |
| `Not` | `False` | Toggle twice |
| `Succ` | `Three` | Advance twice |
| `Add Three` | `Zero` | Add three twice |
| `Cdr` | A list | Follow two tails |

None of these jobs makes the overall project useful. They do at least
demonstrate that we haven't installed a separate Rust primitive every
time we want something to happen twice.

## Name the walk

To skip `n` cells at the front of a list:

```text
DROP = λn. λxs. n CDR xs
```

Inside the body, `xs` is index zero and `n` is index one. Translate
`(n CDR) xs`:

```rust
type Drop = Lambda<Lambda<
    App<App<Var<S<Z>>, Cdr>, Var<Z>>
>>;
```

This name has nothing to do with Rust's destructor trait. It is just a
lambda alias with an unfortunate neighbor in the phone book.[^drop]

For the examples, the source introduces:

```rust
type NumeralList = App<App<Cons, One>,
    App<App<Cons, Two>, App<App<Cons, Three>, Nil>>
>;
```

Its successive tails are:

```text
DROP ZERO  NumeralList → CONS ONE (CONS TWO (CONS THREE NIL))
DROP ONE   NumeralList → CONS TWO (CONS THREE NIL)
DROP TWO   NumeralList → CONS THREE NIL
DROP THREE NumeralList → NIL
```

`Drop` produces the selected **tail**, not an element. It does not
construct replacement cells. We are describing the lambda expression,
not promising runtime pointer sharing or object identity. There are no
runtime cons objects to point at in this experiment.

## Take the head of the tail

Once the walk reaches the desired cell, select its car:

```text
NTH = λn. λxs. CAR (DROP n xs)
```

The indices remain the same; only the application tree grows:

```rust
type Nth = Lambda<Lambda<
    App<Car, App<App<Drop, Var<S<Z>>>, Var<Z>>>
>>;
```

This is zero-based indexing. The zeroth element needs no cdr steps;
the first needs one:

```text
NTH ZERO NumeralList → ONE
NTH ONE  NumeralList → TWO
NTH TWO  NumeralList → THREE
```

At least one convention in the project remains familiar. Please enjoy it
before looking at the surrounding angle brackets.

## A count is not an index

For a proper list containing `k` elements:

| Operation | Valid argument | Boundary |
| --- | --- | --- |
| `Drop n xs` | `0 ≤ n ≤ k` | Exactly `k` steps reaches nil |
| `Nth n xs` | `0 ≤ n < k` | The last valid index is `k - 1` |

That subtraction is explanatory mathematics, not an implemented feature.
We haven't acquired subtraction by writing it in a table. If that worked,
I would have written a much more ambitious table.

`Drop Zero Nil` is valid. `Drop One` on a singleton is valid. `Nth Zero Nil`
is not valid: there is no cell from which to select a head.

These library terms don't check bounds, clamp a count, count the list,
or provide a fallback. Walk too far and you apply `Cdr` outside its
nonempty-cell contract. Select at the end and you do the same to `Car`.
Last week's incidental reductions on nil remain unsupported behavior,
not a surprisingly cheap implementation of error handling.[^bounds]

## Ignore the scenery

Taking a cdr needn't evaluate a car. We can skip a divergent head:

```text
NTH ONE (CONS Ω (CONS TRUE NIL)) → TRUE
```

Likewise, choosing the first element needn't inspect the tail:

```text
NTH ZERO (CONS FALSE Ω) → FALSE
```

The second input isn't a complete proper list, but the valid first cell
is enough for this particular demand. More precisely, the operations
need a usable cell at each step they actually take. They don't validate
or force the whole structure before starting.[^prefix]

The laziness tests use both cases. If navigation accidentally evaluates
every head, the first case fails. If selection insists on traversing a
tail it doesn't need, the second fails. Divergence makes a fairly
uncompromising tripwire.

## Check the walk, not just its parity

The runnable examples obtain the second and third numeral elements and
check that dropping three reaches nil. The numeral results are displayed
indirectly, by iterating `Not` and printing the selected function's type.
That remains a parity demonstration, not an exact indexing oracle.

The tests also apply the wrapper-count probe to the selected numerals.
They cover first, middle, and last valid indices; zero and endpoint drop
counts; a singleton; boolean elements; and computed counts and lists.
The expression `ADD ONE ONE` must select the third element, so the count
needn't be a literal alias.

The [source archive](source.tar.gz), snapshot `e34a51bdafb7`, contains these
checks. Extract it and run `cargo test --offline` and `cargo run --offline`
in `rust-lambda-types`. No runtime Rust function walks the encoded list.

We can now walk as far as we're told. Next week we will ask a different
question: how far is it to the end? We will answer without receiving the
count in advance and without defining a recursive Rust alias. The price
is the [Y combinator][wiki-y]. A sensible project would charge less.

[wiki-numerals]: https://en.wikipedia.org/wiki/Church_encoding#Church_numerals
[wiki-y]: https://en.wikipedia.org/wiki/Fixed-point_combinator#Y_combinator

[^drop]: Rust's [`Drop` trait](https://doc.rust-lang.org/std/ops/trait.Drop.html)
    controls destruction of runtime values. Our `Drop` is a local type alias
    naming an encoded program. Nothing is freed by applying it. If only
    it could reclaim the time we've spent getting here.

[^bounds]: A checked operation with a default or an encoded success/failure
    result could be written as another lambda program. We deliberately
    haven't chosen that API. These posts describe the implemented terms,
    not a larger list library they might someday become.

[^prefix]: The simple length-based bounds describe the common finite proper-
    list case. The lazy examples show a finer operational fact: an unused
    remainder need not itself be inspected. This does not turn a divergent
    tail into a proper list; it only keeps this request from needing it.
