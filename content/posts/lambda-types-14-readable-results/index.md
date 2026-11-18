+++
title = "Lambda in types, part 14: The answer should not require a microscope"
date = 2027-02-24T09:00:00
draft = true
description = "Explicit observers decode Church-numeral and boolean behavior into display constants. The output finally says five instead of presenting its entire family tree."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

For thirteen posts, the compiler has been doing things and the executable
has been printing evidence that something happened somewhere. A number
becomes a boolean choice, the choice selects a function, and the function
appears as a Rust type name. It is an output format best described as
“technically a confession.”

Today the program will print `5`. It will not become useful. It will
merely become easier to confirm exactly how useless it is.

<!-- more -->

**Part 14 of 16.** [Previous: the macro](/posts/lambda-types-13-macro/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: readable structures](/posts/lambda-types-15-readable-structures/).

## First tell the printer what you mean

We cannot hand the printer an arbitrary lambda function and expect it
to discover an intrinsic category. Recall:

```text
ZERO  = λf. λx. x
FALSE = λt. λf. f
```

Those are the same term with different names for its parameters. A
universal “print the right thing” adapter would have to decide whether
that identical input means zero or false. No amount of formatting
enthusiasm can recover information we didn't supply.

So we choose the view explicitly:

```rust
println!("{}", observe::number::<lisp!(Zero)>());          // 0
println!("{}", observe::number::<lisp!((Succ Zero))>());   // 1
println!("{}", observe::boolean::<lisp!(False)>());        // false
```

A numeric view of `False` also gives zero. A boolean view of `Zero` gives
false. That isn't a coercion rule we added to the language. It is the
same behavior interpreted under two protocols.

The new Rust functions are for people. Their outputs never re-enter the
lambda computation. Otherwise this would be the point where our pure
type-level arithmetic quietly escaped through the printer's back door.

## Read behavior, not source shape

`Succ Zero`, `Add Two Three`, and `Length NumeralList` have quite different
syntax trees. Their [weak-head][wiki-weak-head] results may carry suspended work inside
closures and environments. Counting `App` nodes in the input would
measure our spelling choices, not the resulting numeral.

Parsing `type_name` would be worse: a diagnostic string is not a reduction
protocol. We need to ask the function to behave like a numeral, then
decode the behavior it exposes. This can demand more compile-time work
than merely obtaining a closure's name. Making a result readable doesn't
make obtaining it free.[^readback]

## Turn the old test probe into a reader

Our exact-count tests have used:

```text
n TRUE Identity
```

Here true acts as the constant-function wrapper `λvalue. λignored. value`.
Each iteration adds a wrapper around the base identity. The old tests
peeled an independently specified number of layers. The reader instead
keeps peeling until it exposes the base.

The probe expression still belongs to the lambda language:

```rust
type NumberProbe<Term> = lisp!((Term True Identity));
```

After evaluation, this protocol exposes two body shapes. Identity's body
is `Var<Z>`. A wrapper's body is `Var<S<Z>>`: wait for an ignored argument,
then select the value saved one binder outward.

The following excerpt from `src/observe.rs` performs the readback. The
module imports the evaluator types from its parent:

```rust
trait ReadNumber {
    const VALUE: usize;
}

impl<Env> ReadNumber for Closure<Var<Z>, Env> {
    const VALUE: usize = 0;
}

impl<Env> ReadNumber for Closure<Var<S<Z>>, Env>
where
    Self: Apply<Thunk<SecondChoice, Empty>>,
    <Self as Apply<Thunk<SecondChoice, Empty>>>::Output: ReadNumber,
{
    const VALUE: usize =
        1 + <<Self as Apply<Thunk<SecondChoice, Empty>>>::Output as ReadNumber>::VALUE;
}
```

At a wrapper, use the existing `Apply` rule to expose the next layer.
At the base, stop with zero. `SecondChoice`, defined below, is a closed
lambda marker. Valid wrappers ignore the argument supplied to peel them.

Yes, there is an ordinary Rust `1 +` in that excerpt. It counts observed
wrapper layers into an output constant. It does **not** implement Church
successor, add the original operands, or recognize `Add`, `Mul`, or
`Length`. Those programs must expose the correct iteration behavior
through the evaluator before this reader has anything to count.

This is the observation boundary, not a numerals department secretly
subcontracting to `usize`.[^boundary]

## Decode a boolean's selection

The boolean reader uses the same two function markers we've been offering
our booleans in tests:

```rust
type SecondChoice = lisp!((lambda (x) (x x)));
type BooleanProbe<Term> = lisp!((Term Identity SecondChoice));
```

The chosen marker's body determines the output constant:

```rust
trait ReadBoolean {
    const VALUE: bool;
}

impl<Env> ReadBoolean for Closure<Var<Z>, Env> {
    const VALUE: bool = true;
}

impl<Env> ReadBoolean for Closure<App<Var<Z>, Var<Z>>, Env> {
    const VALUE: bool = false;
}
```

The lambda expression has already selected the marker. Rust maps that
observation to a display value; it doesn't evaluate the original boolean
operator natively.

The environment remains generic because captured environments can differ
without changing this marker protocol. We aren't attempting to classify
every closure with one of these body shapes as a boolean in every context.
Part 8's overly agreeable numeral probe should have discouraged that
ambition permanently.

## The runtime finally gets an ordinary number

The public-to-the-parent helper retrieves the associated constant:

```rust
fn number<Term>() -> usize
where
    NumberProbe<Term>: Evaluate<Empty>,
    <NumberProbe<Term> as Evaluate<Empty>>::Output: ReadNumber,
{
    <<NumberProbe<Term> as Evaluate<Empty>>::Output as ReadNumber>::VALUE
}
```

The actual module gives the helper and supporting traits `pub(super)`
visibility so `main` can use them. The generic parameter is a lambda
program type, not a runtime argument. Trait resolution determines the
observed type; const evaluation supplies the decoded count. Runtime code
formats the resulting `usize` or `bool`.

The executable now prints lines including:

```text
ZERO = 0
SUCC ZERO = 1
ADD TWO THREE = 5
MUL TWO THREE = 6
LENGTH NumeralList = 3
TRUE = true
FALSE = false
COND CondExample = 2
```

After all this effort, the output resembles the sort of thing a competent
programmer might have produced on the first afternoon. We must not let
appearances diminish our commitment to the longer route.

## Test the adapter, then the actual executable

The original structural and behavioral tests remain unchanged. The new
observer tests add independent integer and boolean expectations for
literal numerals, computed successors, arithmetic, length, indexing,
projections, and `Cond`.

Lazy-field cases still leave unselected divergent terms unforced. We also
check the deliberate zero/false ambiguity rather than pretending the
reader introduced type tags.

An integration test runs the real executable, checks readable output
lines, and rejects leaked diagnostic type names. Testing a helper that
prints five is not enough if `main` continues printing its ancestry.

The [source archive](source.tar.gz), snapshot `7ba519e92375`, contains both
the observer module and output test. Extract it and run
`cargo test --offline --workspace` and `cargo run --offline --quiet` in
`rust-lambda-types`.

## The printer has limits, and so do we

The numeric view produces a machine-sized unsigned integer. Overflow in
the decoded associated constant is a compile-time error, not a promise
of arbitrary precision. Trait depth, time, and memory can run out first.
A divergent term remains unprintable no matter how nicely we ask.[^limits]

Neither reader is a universal validator of untyped terms. A wrong view
can fail to compile or accidentally agree with the finite observation.
There is no general printer for arbitrary functions here.

Next week we will compose views for pairs and lists. The answer can then
have parentheses around it, completing our transition from unreadable
angle brackets to readable parentheses around an equally unnecessary
computation.

[wiki-weak-head]: https://en.wikipedia.org/wiki/Weak_head_normal_form
[wiki-numerals]: https://en.wikipedia.org/wiki/Church_encoding#Church_numerals

[^readback]: “Readback” here means decoding the behavior exposed by a
    chosen probe into a host display value. It is not full normalization
    of arbitrary terms or reconstruction of a canonical lambda-expression
    syntax tree.

[^boundary]: A display adapter inevitably uses host operations to create
    display data. The restriction is directional: lambda programs compute
    their results; observers expose and decode them; native values do not
    flow back in as a hidden implementation of the encoded arithmetic.
    Otherwise we could replace the whole series with a suspiciously
    well-informed `println!`.

[^limits]: The finite host output type is a limit of the view as well as
    compiler resources being a limit of evaluation. [Church numerals][wiki-numerals] do not
    intrinsically have a `usize` maximum; our chosen presentation does.
