+++
title = "Lambda in types, part 3: Two booleans in a trench coat"
date = 2026-12-09T09:00:00
draft = true
description = "True and false are functions choosing arguments. Conditional selection and short-circuit logic emerge without adding a single boolean rule to the Rust evaluator."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

Rust already has a perfectly good `bool`. It is small, well documented, and
supported by rather more engineering effort than this blog deserves.
Naturally, we will not be using it.

We have an evaluator for variables, lambdas, and applications. Today we
will acquire truth, falsehood, conditional selection, and logical operators
without changing that evaluator. This is not a recommendation for your next
project. It is an explanation of what happens when nobody intervenes.

<!-- more -->

**Part 3 of 17.** [Previous: evaluation](/posts/lambda-types-02-evaluation/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: numerals](/posts/lambda-types-04-numerals/).

## Don't store a truth value; make a choice

What do we need from a boolean? For this experiment, it needs to choose
between two alternatives. Instead of storing a bit and adding a conditional
instruction, make the boolean itself a function that performs the choice:

```text
TRUE  = λt. λf. t
FALSE = λt. λf. f
```

Both functions accept two arguments, one at a time. True selects the first;
false selects the second. The arguments don't have to be booleans. They can
be any terms we want to choose between.

```text
TRUE  A B → A
FALSE A B → B
```

This is a [Church encoding][wiki-church]: represent data through the behavior of functions
when they are used.[^church] It saves us from introducing a primitive
boolean, at the modest cost of making every boolean a small function. The
compiler is not being consulted on the trade-off.

True should look familiar. It is `λx. λy. x`, the constant function from our
evaluator tests, with different parameter names. We already had truth and
failed to recognize it. A common problem, though not usually a compiler one.

## Count the binders

Inside either innermost body, the most recently introduced parameter is
`f`, at index zero. The first parameter, `t`, is one binder outward:

```text
λt. λf. body
 1   0
```

The definitions are therefore:

```rust
type True = Lambda<Lambda<Var<S<Z>>>>;
type False = Lambda<Lambda<Var<Z>>>;
```

These are aliases for ordinary syntax. There is no `struct True` with a
private arrangement with the evaluator, and no trait implementation for
“choosing the true branch.”

Applying two arguments means nesting two applications:

```rust
type OtherFunction = Lambda<App<Var<Z>, Var<Z>>>;
type TrueChoice = App<App<True, Identity>, OtherFunction>;
type FalseChoice = App<App<False, Identity>, OtherFunction>;
```

The inner application supplies the first argument and produces a closure
waiting for the second. For `True`, that closure remembers the first
argument in its environment. This is why we spent last week on closures
instead of simply declaring victory over identity and going home.

`OtherFunction` is `λx. x x`. It isn't divergent by itself; applying it to
itself makes the divergent term `Ω`. Waiting politely for an argument is
not a crime, even when your plans for that argument are questionable.

## If is not special

A conditional can simply hand its alternatives to the boolean:

```text
IF = λb. λt. λf. b t f
```

The binding indices in the body are `b = 2`, `t = 1`, and `f = 0`. Keep
application left-associated when translating:

```rust
type If = Lambda<Lambda<Lambda<
    App<App<Var<S<S<Z>>>, Var<S<Z>>>, Var<Z>>
>>>;
```

Read the body as `(b t) f`, not `b (t f)`. Our punctuation has become less
friendly than the mathematics, but it is still the same tree.

```text
IF TRUE  A B → TRUE  A B → A
IF FALSE A B → FALSE A B → B
```

This works as an ordinary function because our evaluator is [call-by-name][wiki-name].
It doesn't evaluate both alternatives before handing them over. Under an
eager strategy, merely writing this selector would not give conditional
control over argument evaluation.[^eager]

If the unchosen argument is `Ω`, it remains suspended:

```text
TRUE Identity Ω  → Identity
FALSE Ω Identity → Identity
```

This isn't a special exemption for booleans. The application rule has always
suspended its argument. We have finally found a theatrical use for it.

## Build the rest out of choices

Negation offers the alternatives in reverse order:

```text
NOT = λb. b FALSE TRUE
```

If `b` selects the first alternative, it gets false. If it selects the
second, it gets true. Translated into the syntax we have:

```rust
type Not = Lambda<App<App<Var<Z>, False>, True>>;
```

Conjunction and disjunction can decide whether the second input matters:

```text
AND = λp. λq. p q FALSE
OR  = λp. λq. p TRUE q
```

For `And`, a true first input delegates to the second; a false first input
settles the matter. For `Or`, a true first input settles the matter; a false
first input delegates to the second. In Rust:

```rust
type And = Lambda<Lambda<App<App<Var<S<Z>>, Var<Z>>, False>>>;
type Or = Lambda<Lambda<App<App<Var<S<Z>>, True>, Var<Z>>>>;
```

The truth tables are reassuringly conventional. We have found an unusually
expensive route to the usual four rows:

| p | q | AND p q | OR p q |
| --- | --- | --- | --- |
| FALSE | FALSE | FALSE | FALSE |
| FALSE | TRUE | FALSE | TRUE |
| TRUE | FALSE | FALSE | TRUE |
| TRUE | TRUE | TRUE | TRUE |

Their short-circuit behavior follows from the same terms:

```text
AND FALSE Ω → FALSE
OR TRUE Ω   → TRUE
```

The opposite combinations need `Ω` and cannot successfully expose a result
within a finite compiler run. Laziness avoids work that isn't needed. It
does not make the work that *is* needed any less infinite.

## Ask what it does, not what its closure looks like

There is a trap in testing computed functions. The result of `NOT FALSE`
may carry environments different from those of the literal `True`.
Demanding that their closure types be identical would confuse representation
with behavior.

Instead, offer every result the same two distinguishable functions:

```text
candidate Identity (λx. x x)
```

A true result must select identity; a false result must select the other
function. The test can then require the selected closure's exact Rust type,
using last week's equality witness.

This is a finite observation, not a universal equivalence test for lambda
terms.[^equivalence] It is enough to catch swapped choices, wrong operators,
and the full truth-table fixtures without baking arbitrary captured
environments into the expectations.

We also compose the functions:

```text
IF (AND TRUE (NOT FALSE)) Identity OtherFunction
  → Identity

IF (OR FALSE (NOT TRUE)) Identity OtherFunction
  → OtherFunction
```

The first condition can be written as:

```rust
type Condition = App<App<And, True>, App<Not, False>>;
type Expression = App<App<App<If, Condition>, Identity>, OtherFunction>;
```

Runtime Rust still prints a selected closure's type name, not `true` or
`false`. Readable output is deliberately much later in the series. At this
stage even our booleans require an interpreter in the reader's head. It
would be rude for the compiler to have all the fun.

## Two choices, zero new rules

The [source archive](source.tar.gz), snapshot `6a18bd7c8334`, includes all the
truth-table, composition, and lazy-choice checks. After extraction, run
`cargo test --offline` and `cargo run --offline` in `rust-lambda-types`.

We have added six functions and no evaluator rules. Next week we will use
the same three expression forms to represent numbers. If you think a
boolean is an excessive amount of function, wait until you meet two.

[wiki-church]: https://en.wikipedia.org/wiki/Church_encoding
[wiki-name]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_name

[^church]: “Church encoding” names a family of functional representations,
    not a new Rust technique. The important property here is the selector
    protocol: clients supply alternatives, and the encoded boolean chooses.
    We are implementing untyped terms; Rust does not enforce a separate
    boolean category inside the language.

[^eager]: In an eager language, an ordinary call evaluates arguments before
    the function body can select one. A special conditional form or explicit
    suspension is needed to avoid an unwanted branch. Our evaluator already
    supplies suspension uniformly, so `If` can remain a library function.

[^equivalence]: Two closures can have different bodies or captured bindings
    and still agree on the observations we make. Conversely, agreement on
    one finite probe does not establish agreement in every possible context.
    We test the contract we need; we haven't solved lambda equivalence while
    writing a boolean. That would be an even longer post.
