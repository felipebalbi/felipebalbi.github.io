+++
title = "Lambda in types, part 11: To understand recursion, annoy the compiler"
date = 2027-02-03T09:00:00
draft = true
description = "The literal Y combinator supplies recursion to a list-length function. No recursive Rust alias, no special fixed-point evaluator primitive, and no guarantee that this was wise."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

We can walk three steps through a list if somebody supplies three. This
is satisfactory until the question is how many steps the list contains.
Asking the caller to provide the answer would certainly simplify the
implementation, but we are trying to make the *compiler* do pointless
work, not outsource it.

Today we will implement list length using recursion. Not a recursive
Rust function. Not a recursive Rust type alias. An ordinary lambda term
will arrange for a computation to receive itself. If that sounds like a
meeting that should have been an email, wait until you see the email.

<!-- more -->

**Part 11 of 16.** [Previous: navigation](/posts/lambda-types-10-navigation/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: ordered clauses](/posts/lambda-types-12-cond/).

## First write a function with a vacancy

Length has two cases. Nil has length zero. A nonempty list has one more
element than its tail has.

Normally we would give the function a name and use that name in its body.
Instead, make the recursive call an argument:

```text
LENGTH_STEP = λrecur. λxs.
    IF (IS_NIL xs)
       ZERO
       (SUCC (recur (CDR xs)))
```

Assume somebody supplies the right `recur`. On nil, we don't need it.
On a cell, we apply it to the tail and take the successor of its result.

`LengthStep` isn't recursive. It is an ordinary function with two
parameters, one of which has an impressive job title. The body never
refers to a Rust alias called `Length`.

What we want is a `LENGTH` satisfying this relationship:

```text
LENGTH = LENGTH_STEP LENGTH
```

In other words, a fixed point of the step: give the step its recursive
behavior and get the same overall behavior back. There is a standard
lambda-calculus way to build this, and it is not standard because anyone
was worried about readable Rust diagnostics.

## Y, because we can

The call-by-name fixed-point combinator is:

```text
Y = λf. (λx. f (x x)) (λx. f (x x))
```

There are two identical-looking arms. Each accepts itself as an argument
and hands the resulting self-application to `f`.

To see the mechanism without drowning in parentheses, supply a step `F`
and abbreviate `λx. F (x x)` as `M` *on paper*:

```text
Y F → M M
M M → F (M M)
```

The computation handed to `F` can unfold into another call to the same
step. The usual equation is:

```text
Y F =β F (Y F)
```

The subscript matters: this is beta-equivalence. Both sides can reduce to
`F (M M)`. It is not a claim that the evaluator literally rewrites an
expression into the printed alias `Y F` in one step, or that both sides
have the same Rust closure type.[^fixed]

Our earlier self-application term `Ω` immediately asks for more of itself.
Here the step `F` gets a chance to do something before demanding the next
unfolding. Whether it takes that chance is where termination enters the
discussion, usually carrying a clipboard.

## Laziness lets the step get a word in

An eager evaluator would try to evaluate the recursive argument before
giving it to the step. The self-application could keep unfolding without
ever reaching the useful case distinction.

Our application rule suspends arguments. That lets these examples finish:

```text
Y (λrecur. TRUE)  → TRUE
Y (λrecur. FALSE) → FALSE
```

Both steps ignore the recursive argument. The tests observe both choices
successfully. This is the first feasibility check: does the evaluator we
already have support the literal combinator, rather than a Rust-specific
`Fix` primitive wearing its name tag? It does.

No cache is involved, and no cyclic Rust type alias is constructed.
Recursive behavior arises from repeatedly exposing applications of a
finite lambda expression.[^eager]

## The actual type expression

Inside either arm of `Y`, the arm's `x` is index zero and the surrounding
`f` is index one:

```rust
type Y = Lambda<
    App<
        Lambda<App<Var<S<Z>>, App<Var<Z>, Var<Z>>>>,
        Lambda<App<Var<S<Z>>, App<Var<Z>, Var<Z>>>>,
    >,
>;
```

The two `x` binders are separate binders. Each arm's references belong to
its own lambda; both arms can reach the same outer `f` binding. There is
no extra syntax form hiding in this definition: just `Lambda`, `App`,
and `Var`.

The length step has two binders: `xs = 0`, `recur = 1`. Its Rust tree is
longer than its mathematical explanation, which by now should qualify
as a content warning rather than a surprise:

```rust
type LengthStep = Lambda<Lambda<
    App<
        App<App<If, App<IsNil, Var<Z>>>, Zero>,
        App<Succ, App<Var<S<Z>>, App<Cdr, Var<Z>>>>,
    >
>>;

type Length = App<Y, LengthStep>;
```

That last alias is the complete recursive function. It names an
application, not a Rust type expression referring back to itself.

## Follow a singleton

For a one-element list, the recursive behavior is:

```text
LENGTH (CONS TRUE NIL)
  =β SUCC (LENGTH NIL)
  =β SUCC ZERO
```

For three outer cells, the result behaves like:

```text
SUCC (SUCC (SUCC ZERO))
```

Nobody supplies the count. Nil determines when the recursive alternative
is no longer needed. On nil, `If` must keep that alternative suspended,
or we would attempt to inspect a cdr outside the cell contract.

Length never needs the cars. The tests count a list with divergent heads
and an ordinary finite nil-terminated spine. They also count a list whose
elements are themselves lists. Two outer cells still mean length two;
we are measuring the queue, not conducting a census of everyone inside
the building.

## Reaching a closure isn't the same as finishing every call

Our evaluator is weak-head. A numeral-shaped closure may still carry
suspended recursive work. To require the full numeric answer, the tests
give it the wrapper probe:

```rust
type Counted = Wrapped<App<Length, NumeralList>>;
type Result = <Peel<Peel<Peel<Counted>>> as Evaluate<Empty>>::Output;
```

That must expose identity's closure after three peels. The cases include
nil, a singleton, the two-element boolean list, the three-element numeral
list, a computed tail, divergent heads, and nested-list elements.

The executable continues our long-running failure to print numbers by
using the length as a negation count and printing a selected marker type.
The tests check exact counts independently. There is no host-side walk
quietly computing length for the display.

## Recursion is not a termination certificate

`Y` can build a computation that keeps asking for itself forever. It
does not prove that the argument gets smaller, that a list ends, or that
Rust has enough memory to finish the experiment.

These tiny fixed-point cases and list lengths compile at the current
toolchain's default limits. We haven't increased `recursion_limit` in
this snapshot. That is evidence about these examples, not about arbitrary
recursive programs. In particular, infinity does not become affordable
because we found a compact spelling for it.

The [source archive](source.tar.gz), snapshot `86f28392e048`, includes the
literal combinator and all the checks. Run `cargo test --offline` and
`cargo run --offline` in the extracted `rust-lambda-types` directory.

Next week we will use recursion to search an ordered list of clauses.
This gives us a Lisp-shaped `Cond`. The compiler's only new contribution
will be continued patience.

[^fixed]: A fixed-point equation concerns the recursive term's lambda
    behavior. Rust type aliases, syntactic identity, equality of closure
    environments, and beta-equivalence are distinct notions. Compressing
    them into one equals sign is a good way to make an already odd article
    quietly incorrect.

[^eager]: There are fixed-point constructions adapted to eager evaluation,
    typically by introducing another layer of delay. That is not what
    we implement here. The literal `Y` term works with the call-by-name
    strategy chosen in part 2; importing it unchanged into an eager
    evaluator would not preserve these operational claims.
