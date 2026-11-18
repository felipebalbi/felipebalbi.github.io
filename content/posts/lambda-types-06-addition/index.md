+++
title = "Lambda in types, part 6: Five, after considerable administration"
date = 2026-12-30T09:00:00
draft = true
description = "A Church numeral can repeat successor as easily as negation. Addition follows, the compiler remains employed, and two plus three receives the scrutiny it never asked for."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

There are easier ways to add two and three. Almost all ways, in fact.
A pocket calculator would work. Your fingers would work. A compiler can
constant-fold `2 + 3` without a seventeen-part intervention.

Unfortunately, we promised to implement our arithmetic as lambda terms.
Last week we built successor: a function that makes a numeral perform one
more application. This week we will repeatedly apply it, which is exactly
what our numerals already know how to do. Our terrible idea has developed
some internal consistency. That's how they get you.

<!-- more -->

**Part 6 of 17.** [Previous: successor](/posts/lambda-types-05-successor/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: multiplication](/posts/lambda-types-07-multiplication/).

## The step doesn't have to be Not

The numeral `Two` applies its supplied function twice. It knows nothing
about what that function does. Our first example used negation:

```text
TWO NOT FALSE → NOT (NOT FALSE)
```

Instead, supply successor and start from three:

```text
TWO SUCC THREE → SUCC (SUCC THREE)
```

Three becomes four, then five. We have changed the step and starting value,
not the iterator. This is the entire trick, and I realize “the entire
trick” arrived awfully early for an article scheduled a week after the
previous one.

There is no requirement in `Two` that the step consume a boolean. We are
representing untyped lambda calculus in a typed host. The host checks the
evaluation machinery's trait requirements; it does not assign an exclusive
boolean or numeric category to the encoded values.

The contrast is worth keeping in sight:

| Expression | Step | Starting value |
| --- | --- | --- |
| `TWO NOT FALSE` | Toggle a boolean | False |
| `TWO SUCC THREE` | Construct the next numeral | Three |

Same two applications. Different inconvenience.

## Give the pattern a name

To add `m` and `n`, apply successor `m` times starting from `n`:

```text
ADD = λm. λn. m SUCC n
```

For two and three:

```text
ADD TWO THREE
  → TWO SUCC THREE
  → SUCC (SUCC THREE)
```

Use the result as a numeral by giving it a new step `f` and base `x`:

```text
(ADD TWO THREE) f x
  → f ((SUCC THREE) f x)
  → f (f (THREE f x))
  → f (f (f (f (f x))))
```

Five applications. None of those reductions uses a native addition
operation. The result is a numeral because it behaves like an iterator,
not because Rust has certified it as belonging to a `Number` trait.

These are mathematical expansions explaining the behavior. Our evaluator
still exposes weak-head closures, carrying suspended work until a later
application demands it. We haven't quietly installed a normalizer between
the second and third lines.[^expansion]

## Translate the surprisingly small body

In `λm. λn. body`, `m` is one binder outward and `n` is the nearest binder.
The body associates as `(m SUCC) n`:

```rust
type Add = Lambda<          // λm.
    Lambda<                 // λn.
        App<
            App<Var<S<Z>>, Succ>,
            Var<Z>,
        >,
    >,
>;
```

`Succ` names a closed term: its own variables are bound inside its lambdas.
Embedding it doesn't retarget them at `m` and `n`.[^closed]

That alias is the addition implementation. We don't touch `Evaluate`,
`Apply`, `Force`, or `Lookup`. Adding addition by not adding to the
evaluator is the recurring joke, but it is also a concrete property of the
source diff. It is important to have receipts for a joke this expensive.

## The operands arrive one at a time

Our functions are [curried][wiki-currying], so we can pause between arguments:

```rust
type AddTwo = App<Add, Two>;
type Five = App<AddTwo, Three>;
```

The snapshot writes five directly:

```rust
type Five = App<App<Add, Two>, Three>;
```

Both forms describe the same syntax after aliases are expanded. The first
is worth reading slowly: `AddTwo` is an expression for a function waiting
for another numeral. When evaluated, its closure remembers the supplied
two. That half-applied function will become the main ingredient next week.

For now the executable compares the *behavior* of the named addition and
the direct `TWO SUCC THREE` expression by using both results to iterate
`Not`. Both select the true observation marker because five is odd.

This output does not print `5`. It does not even prove five rather than
three or seven. The compiler has computed a numeral; our current display
is merely squinting at one bit of it.

## Two ways for zero to be boring

Zero on the left supplies no successor applications:

```text
ADD ZERO n → ZERO SUCC n → n
```

Zero on the right supplies the starting value, but leaves the left
operand in charge of repetition:

```text
ADD TWO ZERO → SUCC (SUCC ZERO)
```

Both obey the expected arithmetic law on numeral inputs, through different
paths. A test of one doesn't cover the other. Even zero can be boring in
more than one way, and we must respect its range.

The test fixtures therefore include both orders, unequal operands, and a
case with computed inputs:

```text
ADD (SUCC ONE) (ADD ONE ONE)
```

Both operands behave like two; the expected result performs four steps.
We are not restricting addition to aliases for literal numeral bodies.

## Count five, don't ask whether it's odd

The wrapper probe from part 4 supplies the exact expectations. Repeating
`True` wraps identity in constant functions; a fixed number of applications
peels those wrappers away.

```rust
type Counted = Wrapped<Five>;
type Result = <Peel<Peel<Peel<Peel<Peel<Counted>>>>> as Evaluate<Empty>>::Output;
```

The test requires `Closure<Var<Z>, Empty>`. Five peels are written in the
expectation independently of the implementation of `Add`. A function that
accidentally computes three instead of five must not be allowed to submit
“well, they're both odd” as its defense.

There are fixtures for zero with zero, zero on either side, one with one,
unequal operands in both orders, the direct repeated-successor expression,
and the computed-input example. These are finite regression checks, not
an exhaustive proof of arithmetic.[^proof]

Computed numerals can capture environments different from literal
numerals. As with booleans, we test what the function does under a probe,
not whether its private closure luggage matches a preferred shape.

## A sum before the year is out

The [source archive](source.tar.gz), snapshot `8506d2febfbe`, contains this
step. Extract it and run `cargo test --offline` and `cargo run --offline`
inside `rust-lambda-types`. There is no runtime arithmetic hidden in the
executable; it displays the selected type names.

We can now obtain five through lambda evaluation. This is not a useful
result, but it is the correct useless result. Next week we will obtain six
by repeating addition. New year, same questionable judgment.

[wiki-currying]: https://en.wikipedia.org/wiki/Currying

[^expansion]: A reduction written in an explanation may descend farther
    into a function than our weak-head evaluator currently needs to go.
    The eventual numeral probe supplies arguments that expose the relevant
    behavior. We are not claiming those arrows are a compiler trace.

[^closed]: A term is closed when all its variable references are bound
    within that term. This is why embedding `Succ` doesn't require shifting
    its internal indices. Arbitrary open syntax spliced under new binders
    would need more care; these aliases don't implement a general capture-
    avoiding substitution operation.

[^proof]: Checking `ADD TWO THREE` and `ADD THREE TWO` exercises different
    binding and iteration paths. It doesn't establish commutativity for
    every numeral, much less for every untyped term somebody might hand
    this evaluator. The compiler's finite resources are another boundary,
    not an unfortunate footnote we can wish away.
