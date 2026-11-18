+++
title = "Lambda in types, part 7: Six, with extra steps"
date = 2027-01-06T09:00:00
draft = true
description = "Multiplication by repeated addition, with partial application supplying the step function. No multiplication instruction, no runtime loop, and absolutely no reason to do this."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

A new year is an opportunity to abandon bad habits. In unrelated news,
we are continuing to make Rust's type system perform arithmetic through
lambda calculus.

We have successor and addition. Multiplication is next. If you guessed
that it involves repeating addition, congratulations: you have anticipated
the plot. The interesting part is how a two-argument addition function
becomes the one-argument step our numeral knows how to repeat.

<!-- more -->

**Part 7 of 16.** [Previous: addition](/posts/lambda-types-06-addition/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: cells](/posts/lambda-types-08-cells/).

## Only half an addition, please

Our addition term is curried:

```text
ADD = λm. λn. m SUCC n
```

Supply the first argument, three:

```text
ADD THREE
```

We have not supplied an incomplete argument list to a multi-argument
primitive. We have completely applied the outer lambda and received the
inner lambda, which is waiting for its own argument. That function adds
three to the numeral it receives.

```text
(ADD THREE) ZERO  → a numeral behaving like three
(ADD THREE) THREE → a numeral behaving like six
```

This is **partial application**. The first operand survives in the inner
closure's captured environment. We already implemented the necessary
machinery back when the most thrilling thing in the program was identity.
It has been waiting patiently for an outing.[^currying]

In Rust syntax, name the function expression:

```rust
type AddThree = App<Add, Three>;
```

No new evaluator rule. No function-pointer value. No closure allocation
in a runtime heap. Just another type describing an application whose
evaluation can expose a closure.

## Repeat that function twice

The numeral `Two` wants a step and a starting value. We now have a step:
add three. Start from zero:

```text
TWO (ADD THREE) ZERO
  → (ADD THREE) ((ADD THREE) ZERO)
```

Zero, then three, then six. The expression's nested structure is shown
above; our call-by-name evaluator will expose it according to demand,
not run an imperative accumulator through three runtime states.

Write the expression in types:

```rust
type RepeatedAddition = App<App<Two, AddThree>, Zero>;
```

We have supplied a number with a function that operates on numbers. The
number supplying the repetition remains indifferent to all of this.
Its contract is “twice,” not “twice, unless what you're doing is silly.”
An unfortunate absence of judgment.

## Abstract over the operands

To multiply `m` by `n`, repeat “add `n`” exactly `m` times from zero:

```text
MUL = λm. λn. m (ADD n) ZERO
```

Inside the body, `m` is index one and `n` is index zero. There are three
applications, with an important pair of parentheses:

```rust
type Mul = Lambda<              // λm.
    Lambda<                     // λn.
        App<
            App<
                Var<S<Z>>,
                App<Add, Var<Z>>,
            >,
            Zero,
        >,
    >,
>;
```

The inner `App<Add, Var<Z>>` constructs the step. We repeat **add `n`**,
not `Add` by itself, which is still waiting for its first operand, and not
`Succ`, which would give a rather disappointing multiplication table.

The base is equally important. Starting from `n` instead of zero would
give one extra copy of `n`. This is why “roughly the right arrangement of
angle brackets” isn't a testing strategy.

`Add` and `Zero` are closed lambda terms. Their internal variables refer
to their own binders, not to the surrounding multiplication parameters.
The whole implementation remains an expression built from our original
three syntax forms.

## Meet Six, a pending application

The source names the product:

```rust
type Six = App<App<Mul, Two>, Three>;
```

Six is neither a native numeric type nor a hand-written iterator body.
It is a program that, when asked for numeral behavior, must supply six
iterations. The evaluator handles the applications and environments.
Runtime Rust does not add three twice on its behalf.

The executable observes the direct repeated-addition expression and the
named multiplication expression by having each iterate `Not` from false.
Both select the false marker because six is even. By now, you should be
suspicious of any arithmetic demonstration whose strongest claim is
“divisible by two.” The exact-count tests do the less photogenic work.

## A small multiplication table, taken far too seriously

The wrapper probe requires six layers for `Six`:

```rust
type Counted = Wrapped<Six>;
type Peeled = Peel<Peel<Peel<Peel<Peel<Peel<Counted>>>>>>;
type Result = <Peeled as Evaluate<Empty>>::Output;
```

The expected result is identity's closure, `Closure<Var<Z>, Empty>`.
The count is written independently of `Mul`. The probe does not decode a
native integer or inspect the operand aliases.

The fixture table includes:

| Operands | Expected iterations |
| --- | --- |
| Zero, Zero | 0 |
| Zero, Three | 0 |
| Three, Zero | 0 |
| One, Three | 3 |
| Three, One | 3 |
| Two, Two | 4 |
| Two, Three | 6 |
| Three, Two | 6 |

It also checks the direct repeated-addition form and computed operands:

```text
MUL (SUCC ONE) (ADD ONE ONE)
```

That must perform four iterations. The tests compare the observed
behavior, not the closure type of a literal `Four`. Different ways of
producing a numeral are allowed to leave different environments behind.

These are regression examples, not an induction proof or a compiler
performance benchmark.[^scale] Six is currently our largest named number.
Please avoid making investment decisions based on the growth curve.

## The zeros have different jobs

The first operand provides the repetition count:

```text
MUL ZERO n → ZERO (ADD n) ZERO → ZERO
```

No repetitions means no need for the step function. The tests even put
divergent `Ω` in place of `n` and observe zero successfully. This checks
laziness, not arithmetic on an invalid numeral.

With zero in the second position, the path is different:

```text
MUL THREE ZERO → THREE (ADD ZERO) ZERO
```

Now the first operand still provides three iterations. Each step adds
zero, leaving the result at zero. The mathematical product is symmetric;
the evaluator's demands need not be.[^zero]

This is one reason to test operand orders even for commutative operations.
A theorem about the intended numbers does not make two pieces of evaluator
plumbing identical.

## Arithmetic has had quite enough attention

The [source archive](source.tar.gz), snapshot `cf4bc71048da`, contains the
implementation, direct repetition example, and exact-count tests. Extract
it and run `cargo test --offline` and `cargo run --offline` in
`rust-lambda-types`.

We have successor, addition, and multiplication, all as lambda terms.
The evaluator still knows only variables, functions, and application.
Next week we will store two things together without using a Rust tuple
as the language's pair. At this point, making data storage inconvenient
feels like a natural extension of the brand.

[^currying]: Currying represents a function of several arguments as nested
    one-argument functions. Partial application supplies some of those
    arguments. They are related ideas, not synonyms. Here the representation
    is already curried, so ordinary application gives us partial application
    without another mechanism.

[^scale]: Our `Mul` is deliberately derived through repeated addition,
    itself derived through repeated successor. We are not choosing an
    efficient numeral representation or optimizing multiplication.
    Larger terminating expressions can exceed compiler resources even
    though the underlying mathematics is elementary.

[^zero]: A divergent first operand is not rescued merely by a zero second
    operand: the chosen term still asks the first operand to drive
    iteration. On proper numeral inputs both zero laws hold; on arbitrary
    untyped terms, evaluation strategy remains observable.
