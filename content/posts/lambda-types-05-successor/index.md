+++
title = "Lambda in types, part 5: An entire post about adding one"
date = 2026-12-23T09:00:00
draft = true
description = "Deriving successor from Church iteration, counting the binders, and discovering why a computed three need not look like a literal three. A festive misuse of a compiler."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

It's nearly Christmas, so this week's present is one. Not a numeral one;
we already have that. The ability to take a number and produce the next
one. Please keep the receipt.

Our Church numerals describe iteration. Last week we wrote zero through
three by hand. Today we will stop copying application nodes and arrange
for a lambda term to supply the extra application. In a normal program,
this would be the point where somebody types `+ 1`. We have taken certain
measures to prevent that sort of progress.

<!-- more -->

**Part 5 of 16.** [Previous: numerals](/posts/lambda-types-04-numerals/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: addition](/posts/lambda-types-06-addition/).

## Describe one more step

A numeral `n`, given a function `f` and starting value `x`, performs:

```text
n f x
```

To perform one more application, use that result as the argument of `f`:

```text
f (n f x)
```

Now bind everything the expression needs. We want to supply `n` first and
receive another numeral, which will later accept its own `f` and `x`:

```text
SUCC = λn. λf. λx. f (n f x)
```

That is successor. It does not inspect the numeral's source tree, count
its nodes, or ask Rust to increment a native value. It uses the numeral's
behavior and adds another application around it. The implementation is
almost disappointingly short in mathematics. Fortunately, Rust can help.

For zero, the old numeral contributes nothing:

```text
SUCC ZERO f x
  → f (ZERO f x)
  → f x
```

For two, it contributes two applications:

```text
SUCC TWO f x
  → f (TWO f x)
  → f (f (f x))
```

These are explanations of behavior under reduction, not a promise that our
weak-head evaluator rewrites every suspended argument before doing anything
else. That distinction will remain with us like glitter after wrapping
presents.[^weak]

## The binder audit

Inside `λn. λf. λx. body`, the indices count outward from `x`:

| Parameter | Index | Rust reference |
| --- | --- | --- |
| x | 0 | `Var<Z>` |
| f | 1 | `Var<S<Z>>` |
| n | 2 | `Var<S<S<Z>>>` |

The body is `f ((n f) x)`. There is an outer application to `f`, whose
argument contains two left-associated applications. Translate that tree,
not the spacing in the printed expression:

```rust
type Succ = Lambda<                    // λn.
    Lambda<                            // λf.
        Lambda<                        // λx.
            App<                       // f ((n f) x)
                Var<S<Z>>,
                App<
                    App<Var<S<S<Z>>>, Var<S<Z>>>,
                    Var<Z>,
                >,
            >,
        >,
    >,
>;
```

Three lambdas. Three applications. Three parameter references, with `f`
appearing twice. No arithmetic trait. The evaluator from part 2 remains
unchanged and, if it had opinions, would probably prefer this to stop.

## A number can be a pending computation

We can now name four without writing four's literal body:

```rust
type Four = App<Succ, Three>;
```

This is an application expression. A Rust alias doesn't eagerly evaluate
it. When another expression asks four to iterate, the evaluator exposes
enough of that application to do the job:

```rust
type FourFlips = App<App<Four, Not>, False>;
```

Four negations starting from false produce false. The runnable program
observes that result with the familiar two function markers and prints the
selected closure type. There is still no integer display.

This is a small but important promotion for our numerals. They no longer
have to be literal lambdas with a hand-counted sequence of applications.
They can be expressions that produce numeral behavior when used. It would
be very inconvenient if every answer needed to be prewritten, even for
this project, whose position on convenience is already well documented.

## Why not compare the result to Three?

Because `Succ Two` does not need to produce the same Rust closure type as
the literal `Three`.

The successor application produces a closure waiting for `f`, with the
supplied numeral saved in its environment. A literal numeral has its own
body and surrounding environment. The two can perform exactly the same
iteration while carrying different type-level luggage.

We test behavior using the wrapper probe from part 4. A numeral applies
`True` repeatedly to `Identity`; each repetition adds a constant-function
wrapper. A fixed number of further applications must peel back to
identity's closure:

```rust
type Counted = Wrapped<App<Succ, Two>>;
type Result = <Peel<Peel<Peel<Counted>>> as Evaluate<Empty>>::Output;
```

Here `Wrapped` and `Peel` are the test aliases explained in the previous
post. The expected type is independently written as
`Closure<Var<Z>, Empty>`. We do not construct the expectation by asking
successor for its opinion.

The snapshot checks zero through three as inputs, plus a computed input:

```text
SUCC (SUCC ZERO)
```

That must perform two iterations. A check of `Four` must perform four,
not merely an even number. We have not made arithmetic simpler by
encoding it in functions, so the tests should at least decline to make it
less certain.[^finite]

## Successor can decline to inspect its numeral

The body is `f (n f x)`. In call-by-name evaluation, the outer `f` controls
whether its argument is needed. If `f` ignores that argument and gives back
identity, then the entire `n f x` computation can stay suspended.

The tests exploit this with divergent `Ω` in place of `n`. The expression
still exposes identity because the supplied step ignores the dangerous
argument. This is not a claim that `Ω + 1` is a number. `Ω` isn't a valid
numeral here; it is a device for checking what evaluation demands.[^omega]

Even successor, our bold new arithmetic operation, has not earned the
right to override the evaluator's laziness.

## One more, and not a feature more

The [source archive](source.tar.gz) is snapshot `7e5535d2a570`. Extract it and
run `cargo test --offline` and `cargo run --offline` in `rust-lambda-types`.
The source keeps the expanded definitions annotated so the indices and
application tree remain visible.

This step deliberately contains no addition or multiplication. One more
application is enough for one week. Next week we will give successor to a
numeral and ask it to repeat. This will produce addition, just in time for
the end of the year, when everyone is making regrettable commitments.

[^weak]: If the supplied `f` ignores its argument, the evaluator can expose
    `f`'s result without completing the inner `n f x`. The mathematical
    expansion describes the encoded iteration; it is not an eager execution
    schedule or a trace emitted by the program.

[^finite]: The probe is a finite behavioral check on these fixtures, not a
    proof for all untyped terms. It intentionally distinguishes counts that
    a parity example collapses. Different function representations remain
    allowed as long as they satisfy the observed contract.

[^omega]: An unused divergent expression is a particularly strict test
    fixture: forcing it doesn't yield a conveniently wrong integer, it
    prevents successful finite evaluation. We only include it in contexts
    where the tested behavior is supposed to avoid it.
