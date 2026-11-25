+++
title = "Lambda in types, part 1: A perfectly good compiler, ruined"
date = 2026-11-25T09:00:00
draft = false
description = "Representing untyped lambda calculus with three Rust type constructors: why syntax comes first, how de Bruijn indices preserve binding, and what we have not computed yet."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

Rust's type system spends most of its life preventing mistakes. I thought it
deserved a change of pace, so I gave it a mistake to *be*.

We are going to implement [lambda calculus][wiki-lambda] in Rust types. Not a runtime
interpreter written in Rust; that would be a sensible activity, and we have
not gathered here for that. The expressions will be types. The results will
be types. The compiler, which presumably had other plans, will do the work.

The original stretch goal was Conway's Game of Life. The eventual finale is
a little functional language that filters a list, squares its odd numbers,
and adds them up. The answer is ten. We will spend sixteen weeks arranging
for a compiler to discover ten. A seventeenth installment is a love-letter
epilogue to Lisp, in case the parentheses hadn't given us away. Please
adjust your expectations accordingly.

There is no practical application. This is play. This week's deliverable
is that we can **describe identity**. We cannot evaluate it yet. Even by the
standards of a project with no purpose, we are taking our time.

<!-- more -->

**Part 1 of 17.** [Next: evaluation](/posts/lambda-types-02-evaluation/).
The [series index](#the-series) is at the end of this post.

## The rule of the experiment

There is a very easy way to cheat at this project. Make a Rust type called
`Add`, give it an implementation that adds two Rust-level numbers, and call
the result a language. That would get us to the wrong sort of bad idea.

Our self-imposed inconvenience is:

> Rust supplies general evaluation rules. Lambda terms supply the programs.

Identity must be an expression in the language, not a special case in the
evaluator. Later, the same restriction will apply to booleans, arithmetic,
cells, and recursion. We may build human-facing output adapters, but those
adapters must not secretly do the arithmetic we claim the language performed.

This is disposable code, not the beginning of a compiler framework. If I
start discussing its enterprise plugin story, please take the keyboard away.
We do need to be precise about which layer is doing what: being useless
is a choice; being wrong would spoil the joke.

## Three forms are enough to begin

Untyped lambda calculus gives us three expression forms:

| Form | Written as | What it describes |
| --- | --- | --- |
| Variable | `x` | A reference to a bound argument |
| Abstraction | `λx. body` | A function with argument `x` and body `body` |
| Application | `function argument` | Applying one expression to another |

The dot separates a parameter from its body. Application associates to the
left: `f a b` means `(f a) b`. A lambda's body extends to the right unless
parentheses say otherwise.[^notation]

The smallest example worth inflicting on a compiler is identity:

```text
λx. x
```

It accepts an argument and gives that argument back. Functions can themselves
be arguments, so this is a perfectly reasonable expression:

```text
(λx. x) (λx. x)
```

Mathematically, applying the left-hand identity to the right-hand identity
reduces to identity. That is an example of
**[beta reduction](https://en.wikipedia.org/wiki/Beta_reduction)**, the rule that
connects application to a function's body. Today, the arrow is in our
explanation, not in the Rust program:

```text
(λx. x) (λx. x) → λx. x
```

Notice what the grammar doesn't contain: no integer literals, no booleans,
no lists, no named recursion. Those are not omissions we will patch by
adding variants to a syntax enum. We will discover representations for them
using the three forms we already have.

## Names are pleasant until substitution gets involved

The names in `λx. x` are for us. Rename both occurrences to `y` and nothing
changes: `λy. y` is the same function. The important fact is which binder
owns which reference.

Here are two functions whose distinction does matter:

```text
λx. λy. y    choose the inner argument
λx. λy. x    choose the outer argument
```

Keeping textual names would make us responsible for renaming variables
carefully during substitution. In an open term, replacing a free `x` with
`y` inside `λy. x` must not accidentally bind the new `y`. That is variable
capture; the innocent-looking textual replacement changes the meaning.

We will use **[de Bruijn indices][wiki-indices]** instead. A variable says how many binders
outward to look, starting at zero:

```text
λx. x       → λ. 0
λx. λy. y   → λ. λ. 0
λx. λy. x   → λ. λ. 1
```

The numbers are distances to binders, not the language's future arithmetic
values. `0` means “the nearest enclosing lambda's argument.” `1` means
“skip that binder and use the next.”[^debruijn]

This doesn't make binding disappear. It makes the relationship explicit.
Next week, our evaluator will still have to preserve that relationship when
a function leaves the environment in which it was defined.

## Put the syntax in types

Rust needs one constructor for each form:

```rust
use std::marker::PhantomData;

struct Z;
struct Var<N>(PhantomData<N>);
struct Lambda<Body>(PhantomData<Body>);
struct App<Function, Argument>(PhantomData<(Function, Argument)>);
```

`Z` is the index zero. That is the only index this snapshot needs; a way to
represent larger indices arrives with nested evaluation in part 2.

`Var<N>` is a variable reference. `Lambda<Body>` contains the type describing
a function body. `App<Function, Argument>` contains two type-level children.
It records an application without performing it.

`PhantomData` is the standard marker for a relationship to a type without
storing a value of that type. Rust requires us to use these generic
parameters somehow; the marker makes the relationship explicit.[^phantom]
Our program constructs none of these structs at runtime. We only name the
types.

Identity is now:

```rust
type Identity = Lambda<Var<Z>>;
type Expression = App<Identity, Identity>;
```

Read the first alias from the outside inward: a lambda whose body is a
reference to its nearest argument. The second alias describes applying
that expression to itself.

A type alias is not a new nominal type, a variable assignment, or a function
call. `Identity` is another name for `Lambda<Var<Z>>`. Naming `Expression`
does not cause a reduction. We have built the tree, not the machine that
interprets it.

This separation is our first act of tidiness at the scene of the crime. If `App<F, X>`
immediately meant “the result of calling `F` on `X`,” it would be much harder
to talk about suspended work. Keeping application as syntax leaves that
decision to the evaluator.

## What can we run?

We can inspect the tree:

```rust
fn main() {
    println!("Identity: {}", std::any::type_name::<Identity>());
    println!("Application: {}", std::any::type_name::<Expression>());
}
```

Rust expands the aliases and usually includes module paths. Look for the
shape `Lambda<Var<Z>>`, and for an `App` containing two copies of that shape.
Do not treat the exact spelling as an output protocol. `type_name` is a
diagnostic aid, not a stable serialization format, and certainly not proof
that evaluation occurred.[^typename]

Nor have we built a well-scopedness checker. Rust lets us write
`Lambda<u8>`, even though `u8` is not one of our expression forms. We can
also describe references with no matching binder once larger indices exist.
Representation alone doesn't establish validity. Later trait requirements
will reject unsupported operations when evaluation demands them; that is
not the same thing as checking every syntax tree up front.

## Read and run this step

The [source archive](source.tar.gz) is the exact article-1 snapshot,
`afb9fbeb022c`, not the final version of the experiment:

```sh
tar -xzf source.tar.gz
cd rust-lambda-types
cargo run --offline
```

Use a Rust toolchain supporting edition 2024. The archive has no third-party
dependencies. Each later article includes its own snapshot, so following an
early post does not require mentally deleting features introduced months
after it.

Next week we will give the tree meaning. A lambda will become a closure, an
argument will become suspended work, and application will connect the two.
The first genuine result will still be identity. This time, Rust will have
worked it out.

## The series

All posts are Wednesday installments. Later links become public as the
series is published; they are available together in the draft preview.

| Part | Date | Topic |
| --- | --- | --- |
| 1 | November 25, 2026 | A perfectly good compiler, ruined |
| [2](/posts/lambda-types-02-evaluation/) | December 2 | Evaluation and lexical environments |
| [3](/posts/lambda-types-03-booleans/) | December 9 | Booleans are choices |
| [4](/posts/lambda-types-04-numerals/) | December 16 | Numbers are iterators |
| [5](/posts/lambda-types-05-successor/) | December 23 | One more application |
| [6](/posts/lambda-types-06-addition/) | December 30 | Addition repeats successor |
| [7](/posts/lambda-types-07-multiplication/) | January 6, 2027 | Multiplication repeats addition |
| [8](/posts/lambda-types-08-cells/) | January 13 | A pair is a function |
| [9](/posts/lambda-types-09-lists/) | January 20 | Constructors, projections, and nil |
| [10](/posts/lambda-types-10-navigation/) | January 27 | Let a number walk a list |
| [11](/posts/lambda-types-11-recursion/) | February 3 | Recursion without recursive aliases |
| [12](/posts/lambda-types-12-cond/) | February 10 | First true clause wins |
| [13](/posts/lambda-types-13-macro/) | February 17 | A little Lisp notation |
| [14](/posts/lambda-types-14-readable-results/) | February 24 | Readable numbers and booleans |
| [15](/posts/lambda-types-15-readable-structures/) | March 3 | Readable pairs and lists |
| [16](/posts/lambda-types-16-sicp/) | March 10 | A little SICP |
| [17](/posts/lambda-types-17-love-letter/) | March 17 | Dear Lisp, sorry about Rust — an epilogue |

[wiki-lambda]: https://en.wikipedia.org/wiki/Lambda_calculus
[wiki-indices]: https://en.wikipedia.org/wiki/De_Bruijn_index

[^notation]: These are parsing conventions, not evaluation rules. `λx. λy. x y`
    means `λx. (λy. (x y))`. Parentheses can override the conventions; changing
    parentheses can change a program even when every variable name stays put.

[^debruijn]: N. G. de Bruijn introduced the nameless notation in
    [“Lambda calculus notation with nameless dummies” (1972)](https://doi.org/10.1016/1385-7258(72)90034-0).
    We use zero-based indices. Eliminating names avoids name-based capture,
    but an evaluator still needs a correct account of binding; the notation
    is not a substitute for lexical-scope tests.

[^phantom]: See Rust's [`PhantomData` documentation](https://doc.rust-lang.org/std/marker/struct.PhantomData.html).
    The marker also participates in variance, auto traits, and drop checking.
    None of those subtleties is our evaluation mechanism: associated types
    and trait selection will do that work.

[^typename]: The standard library explicitly describes
    [`type_name`](https://doc.rust-lang.org/std/any/fn.type_name.html) as
    intended for diagnostics. Our later tests will compare types directly,
    rather than parsing this string.
