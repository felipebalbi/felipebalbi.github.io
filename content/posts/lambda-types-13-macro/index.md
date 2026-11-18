+++
title = "Lambda in types, part 13: Replacing angle brackets with parentheses"
date = 2027-02-17T09:00:00
draft = true
description = "A tiny procedural macro gives our type-level language S-expressions and named binders. It translates syntax only; the Rust type system is not being let off the hook."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp", "macros"]
+++

After twelve posts of nested generic types, I have identified a usability
problem: the source looks like a flock of seagulls fighting over a comma.

We could fix this by abandoning the project. Instead, we will add a
procedural macro that replaces the angle brackets with parentheses.
This is the software equivalent of moving the mess to another room,
except that this time the room really is easier to read.

<!-- more -->

**Part 13 of 16.** [Previous: Cond](/posts/lambda-types-12-cond/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: readable results](/posts/lambda-types-14-readable-results/).

## The same program in less threatening clothes

We currently write a sum as:

```rust
type Sum = App<App<Add, Two>, Three>;
```

With `lisp!()`, we can write:

```rust
type Sum = lisp!((Add Two Three));
```

That expands to exactly the same application tree, **not** to the numeral
five. The macro doesn't know what `Add` means. It doesn't know that `Two`
is a numeral. It isn't evaluating the expression in ordinary Rust and
leaving the type system a fraudulent receipt.

The distinction is the point of this post. A frontend translates a
notation; the associated-type evaluator still has to do the computation.
We have made the request more legible without making it more sensible.

## A grammar small enough to supervise

The macro supports three forms:

```text
term = name
     | (term term+)
     | (lambda (name+) term)
```

A name either refers to a lambda-bound parameter or to a Rust type in the
caller's scope. An application has a function and at least one argument.
A lambda has one or more parameter names and exactly one body.

Multiple arguments in an application associate to the left:

```text
(Add Two Three) = ((Add Two) Three)
```

Multiple lambda parameters become nested one-argument lambdas:

```text
(lambda (x y) x) = λx. λy. x
```

We haven't invented a multi-argument evaluator primitive. We have given
[currying][wiki-currying] a slightly less bureaucratic spelling.

For example:

```rust
type Increment = lisp!((lambda (n) (Succ n)));
```

Its expanded type is `Lambda<App<Succ, Var<Z>>>`. An anonymous function can
also appear directly in function position:

```rust
type Applied = lisp!(((lambda (x) x) True));
```

The outer macro parentheses delimit Rust's macro input. The inner
parentheses describe an application. Yes, we replaced the bracket problem
with a parenthesis problem. There are traditions to uphold.

## Names go in; distances come out

We originally used [de Bruijn indices][wiki-indices] to represent binding without textual
names. The macro doesn't undo that decision. It allows names at the
surface and lowers them to the same indices.

Keep the enclosing parameter names in source order. When a name is used,
search from the newest binding outward. In `(lambda (x y) ...)`, `y` is
nearest and becomes `Var<Z>`; `x` becomes `Var<S<Z>>`.

An inner name shadows an outer one:

```rust
type Shadowed = lisp!((lambda (x) (lambda (x) x)));
```

The reference belongs to the inner `x`. Parameters can even shadow a
library name:

```rust
type ConfusingButLegal = lisp!((lambda (True) True));
```

That is identity, not a constant true function. The macro respects
[lexical scope][wiki-scope], not our taste in parameter names.

After lowering a lambda body, restore the previous binding depth. Otherwise
a parameter in one expression could leak into a sibling argument. The
implementation also treats raw identifier spellings such as `r#x` and `x`
as the same binding name.

The name stack uses Rust strings and integer distances. This is frontend
metadata, not a back door through which native integers replace Church
arithmetic. Compilers are allowed to count their binders. Our encoded
addition is still denied that convenience.

## A second crate, but not a second language engine

Rust procedural macros live in a separate crate. The workspace now has
the evaluator binary and a local `lisp-macro` crate, with no third-party
dependencies.[^proc]

Rust already supplies token trees with balanced groups. The macro parses
those into a tiny host-side syntax tree, whose actual declaration is:

```rust
enum Term {
    Name(Ident),
    Apply(Vec<Term>, Span),
    Lambda(Vec<Ident>, Box<Term>, Span),
}
```

This excerpt lives in the macro crate, with `Ident` and `Span` imported
from `proc_macro`. The host `Vec` and `Box` store parsed syntax, not the
lambda program's lists or evaluated values.

Lowering does three things: emit references for names, nest `App` for
applications, and nest `Lambda` for parameters. The resulting program
contains the original constructors. The macro never unfolds `Y`, asks
whether a predicate is true, or performs multiplication.

Generated constructor names are rooted at `crate::Lambda`, `crate::App`,
`crate::Var`, `crate::Z`, and `crate::S`. Free names retain the original
identifier tokens and resolution spans. This prevents a local alias named
`App` from hijacking the generated constructor while allowing local
aliases for actual program terms.[^hygiene]

This macro is intentionally coupled to our root-level constructors.
There is no configurable backend. If we start designing one, that is
the sound of the joke escaping containment.

## Read what we've already written

The library definitions now look like this:

```rust
type Succ = lisp!((lambda (n f x) (f (n f x))));
type Add = lisp!((lambda (m n) (m Succ n)));
type Mul = lisp!((lambda (m n) (m (Add n) Zero)));
type Cons = lisp!((lambda (a d select) (select a d)));
```

And the [fixed-point combinator][wiki-y] is still the literal term:

```rust
type Y = lisp!((lambda (f) ((lambda (x) (f (x x))) (lambda (x) (f (x x))))));
```

Everything currently used as a program definition, example, or behavioral
test input is converted to this notation. We don't keep two parallel
libraries and hope they remain equivalent. Earlier posts and their
archives keep the original spelling, because history should not pretend
that the macro arrived twelve weeks before it did.

## Test against something that isn't the macro again

If both the actual syntax and its expected syntax were generated by
`lisp!()`, a broken macro could enthusiastically agree with itself.
The syntax tests therefore compare expansions to raw Rust trees.

Application order, currying, outer references, shadowing, sibling scope,
caller names, and the complete `Y` term all get independent expectations.
The pre-existing behavioral tests retain their raw expected result types.
The conversion also checked all 37 canonical definitions against the
handwritten part-12 syntax using exact type equality.

This is stronger than “the programs still seem to do the same thing”
for the conversion itself: the expanded syntax is the same tree. It is
not a claim of general lambda equivalence. We remain ambitious about
making the compiler work, not about solving undecidable problems by
accident.

## Several things this Lisp cannot read

There are no numeric or string literals, quoted lists, arbitrary operator
punctuation, or qualified Rust paths. Use a Rust alias to give a value a
simple name before referring to it. `lambda` is reserved for its form.

`()` is not our empty-list syntax; use `Nil`. A parenthesized single symbol
isn't a zero-argument call. There are no zero-parameter lambdas and no
multiple-body sequencing form. The parser is small partly because we
keep telling it no.

Malformed forms produce diagnostics at the relevant span. Unknown free
names go through ordinary Rust type resolution. Tests compile a valid
control and malformed examples in an isolated offline fixture project,
checking the diagnostic reason rather than merely celebrating failure.
`compile_error!` is qualified through `::core`, so a caller can't
accidentally replace our complaint with a more agreeable macro.

## Same nonsense, better handwriting

The [source archive](source.tar.gz), snapshot `8cafa5a3ae85`, includes the
workspace, parser, syntax tests, and compiler-diagnostic fixtures. After
extraction, run `cargo test --offline --workspace` in `rust-lambda-types`.

We haven't changed the evaluator, added domain primitives, or improved
the compiler's resource limits. We have made the input readable. Next
week we will extend the same courtesy to the output, which has spent
thirteen posts resembling a missing closing bracket investigation.

[wiki-currying]: https://en.wikipedia.org/wiki/Currying
[wiki-indices]: https://en.wikipedia.org/wiki/De_Bruijn_index
[wiki-scope]: https://en.wikipedia.org/wiki/Scope_(computer_science)#Lexical_scope
[wiki-y]: https://en.wikipedia.org/wiki/Fixed-point_combinator#Y_combinator

[^proc]: The [Rust Reference on procedural macros](https://doc.rust-lang.org/reference/procedural-macros.html)
    describes the separate `proc-macro` crate and token-stream interface.
    We use `proc_macro` directly here rather than dependencies such as
    `syn` and `quote`, because this grammar is tiny, not because avoiding
    those libraries is a general virtue.

[^hygiene]: Procedural macros are not automatically hygienic in every sense.
    Explicit paths protect the constructors we generate; preserving input
    identifier tokens preserves caller resolution for free names. Lambda
    binding is a separate job performed by our own name stack. Calling all
    three simply “hygiene” would hide the interesting mistakes.
