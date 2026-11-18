+++
title = "Lambda in types, part 4: Two is a function now"
date = 2026-12-16T09:00:00
draft = true
description = "Church numerals turn counting into repeated application. We make zero through three, discover that zero is false, and refuse to mistake parity for arithmetic testing."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

Having replaced Rust's booleans with functions, we should probably stop.
Instead, let's do the same thing to its integers.

We won't put an integer in a struct, ask a const expression to count, or
introduce a Rust trait for arithmetic. We will give each number two
arguments and make it repeat the first one. This is a strange thing to do
to a number, but the number has no lawyer.

<!-- more -->

**Part 4 of 16.** [Previous: booleans](/posts/lambda-types-03-booleans/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: successor](/posts/lambda-types-05-successor/).

## The number tells you how many times

A [Church numeral][wiki-numerals] accepts a step function `f` and a starting value `x`.
Its count is the number of times it applies the step:

```text
ZERO  = λf. λx. x
ONE   = λf. λx. f x
TWO   = λf. λx. f (f x)
THREE = λf. λx. f (f (f x))
```

There is no integer payload in any of these definitions. Two is not a
box with `2` written on it. It is the behavior “do that twice.”

The choice of step and starting value belongs to the caller:

```text
ZERO  f x → x
ONE   f x → f x
TWO   f x → f (f x)
THREE f x → f (f (f x))
```

This will eventually let one numeral drive boolean negation, arithmetic,
and movement through a list. Today it will mostly drive the point home
that `u8` was sitting right there and we walked past it.

## Two different sorts of number

Our syntax already has `Z` and `S<N>` for binding indices. Do not recruit
them as the language's numeric values. They belong to the evaluator's
addressing system.

For all four numeral bodies, the binding layout is identical:

```text
λf. λx. body
 1   0
```

`f` is `Var<S<Z>>`; `x` is `Var<Z>`. The count comes from application nodes,
not from changing either index:

```rust
type Zero = Lambda<Lambda<Var<Z>>>;
type One = Lambda<Lambda<App<Var<S<Z>>, Var<Z>>>>;
type Two = Lambda<Lambda<
    App<Var<S<Z>>, App<Var<S<Z>>, Var<Z>>>
>>;
type Three = Lambda<Lambda<
    App<Var<S<Z>>, App<Var<S<Z>>, App<Var<S<Z>>, Var<Z>>>>
>>;
```

Read `Two` from the inside outward. First `f x`. Then `f` applied to that
whole expression. Putting the applications the other way round would give
`(f f) x`, a different program with the same ingredients and a much less
promising attitude toward counting.

An index happens to use a successor-shaped Rust representation. A Church
numeral happens to describe iteration. They are different layers. Combining
them would be like doing your accounts using the page numbers.

## Zero is false, which is unfortunate for the branding

Compare zero to last week's false:

```text
ZERO  = λf. λx. x
FALSE = λt. λf. f
```

Erase the parameter names. Both are `λ. λ. 0`: choose the inner argument.
Their Rust aliases name the exact same type.

This isn't a bug in the numeral definition. The boolean interpretation says
“choose the second alternative.” The numeral interpretation says “do not
apply the step; give back the starting value.” Same behavior, different
use.[^untyped]

Remember this when we eventually want a printer. An untagged function does
not arrive wearing a badge saying NUMBER. A printer will need us to choose
an interpretation. A language with only functions is admirably consistent
and quite unhelpful at introductions.

## Make a number do something observable

We already have a step function worth repeating: `Not`. Start from false:

```text
ZERO  NOT FALSE → FALSE
ONE   NOT FALSE → TRUE
TWO   NOT FALSE → FALSE
THREE NOT FALSE → TRUE
```

For example:

```rust
type TwoFlips = App<App<Two, Not>, False>;
```

The executable offers each resulting boolean the same distinct functions
from part 3, then prints the selected closure's type. The runtime doesn't
loop twice. The Church numeral drives applications through the evaluator.

This demonstrates iteration, but only observes **parity**. A broken `Two`
that performs zero applications would pass this particular example. A
broken `Three` that performs one would also pass. We have made counting
unreasonably difficult; we should at least check the count.[^parity]

## A wrapper we can peel

We need a step whose repetitions remain distinguishable. Use the constant
function already called `True`:

```text
WRAP = λvalue. λignored. value
```

Applied to a value, it makes a function waiting for an irrelevant argument
before giving the value back. Each iteration adds one such wrapper:

```text
ZERO  WRAP Identity → Identity
ONE   WRAP Identity → WRAP Identity
TWO   WRAP Identity → WRAP (WRAP Identity)
```

Supply one argument to remove one wrapper. Supply two to remove two.
For the tests, those operations are still ordinary lambda applications:

```rust
type SelfApply = Lambda<App<Var<Z>, Var<Z>>>;
type Wrapped<Numeral> = App<App<Numeral, True>, Identity>;
type Peel<Term> = App<Term, SelfApply>;
```

The wrapper ignores `SelfApply`. We choose it because it is distinguishable
from identity if we accidentally apply one argument too many:

```rust
type Result = <Peel<Peel<Wrapped<Two>>> as Evaluate<Empty>>::Output;
```

That result must be `Closure<Var<Z>, Empty>`, identity evaluated at the top
level. Too few peels leave a wrapper. An extra peel applies identity to
`SelfApply` and exposes the other function; sufficiently many extra peels
can diverge.

The expected number of peels is written independently in each check. We
are not asking the numeral how many tests it thinks it ought to pass.

This is not yet a general number-to-integer decoder. We have a candidate
number and an expected finite count, then apply a discriminating probe.
Neither a numeric evaluator primitive nor a runtime counter is required.

## Zero is very good at not doing things

Zero never refers to its step function. In our [call-by-name][wiki-name] evaluator,
that step need not be evaluated at all:

```text
ZERO Ω Identity → Identity
```

The tests use this to check that the zero-iteration case really is lazy.
`Ω` is not a valid numeral or a special error value. It is a divergent
term placed where the computation promises not to look.

The wrapper descriptions above are mathematical explanations of behavior,
not claims that every suspended body is eagerly normalized. A numeral
describes repeated application. What the evaluator must expose still
depends on what the supplied step does with its argument.[^demand]

## Four can wait

The [source archive](source.tar.gz), snapshot `36987cbb396c`, contains the
four numerals, parity demonstrations, and exact-count tests. After
extraction, run `cargo test --offline` and `cargo run --offline` inside
`rust-lambda-types`.

We could write four by adding another application node. We could then
write five, six, and a resignation letter. Instead, next week's post will
construct the next numeral from the one we already have. We are about to
spend an entire installment on adding one. Please don't tell the people
who designed the increment instruction.

[wiki-numerals]: https://en.wikipedia.org/wiki/Church_encoding#Church_numerals
[wiki-name]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_name

[^untyped]: “Untyped” describes the lambda language, not its Rust host.
    Rust checks that the associated-type computations have implementations.
    It does not impose disjoint boolean and numeral categories on the terms
    represented by those types. Alias names don't introduce such categories
    either.

[^parity]: More generally, repeated negation collapses every even count
    into one observation and every odd count into another. Testing a sum
    only through this observation could miss an error of two. This is why
    the later arithmetic tests keep the exact-count probe.

[^demand]: Our evaluator stops at weak-head closures. `TRUE value` can expose
    a wrapper without evaluating `value`. Peeling the wrapper demands that
    saved argument later. The probe works with the strategy instead of
    assuming the syntax was first expanded into a fully reduced numeral.
