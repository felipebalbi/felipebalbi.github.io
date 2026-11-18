+++
title = "Lambda in types, part 2: Making the compiler do the paperwork"
date = 2026-12-02T09:00:00
draft = true
description = "An entirely unnecessary evaluator made from associated types, lexical environments, closures, and suspended arguments. Identity finally gets something done."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system"]
+++

Last week we represented a function that does nothing to its argument, then
declined to run it. This week we will build enough machinery to run it.
There will be environments, closures, suspended arguments, and a small
committee of traits. The eventual computation is still “give me that back.”

If you were hoping this project would become practical, this is an
excellent point to get off the bus. We are about to implement laziness by
making the Rust compiler work considerably harder.

<!-- more -->

**Part 2 of 17.** [Previous: syntax](/posts/lambda-types-01-syntax/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: booleans](/posts/lambda-types-03-booleans/).

## An answer with no method

Our syntax has three forms: `Var<N>`, `Lambda<Body>`, and `App<F, X>`.
Evaluation needs an expression and the bindings its variables refer to.
Call that collection of bindings an **environment**:

```rust
trait Evaluate<Env> {
    type Output;
}

struct Empty;
```

There is no `fn evaluate`. Nothing here can be called at runtime. An
implementation relates a type and an environment to another type through
the associated type `Output`.[^associated]

The notation for asking the question is a little ceremonial:

```rust
type Result = <Identity as Evaluate<Empty>>::Output;
```

Read it as “the output of identity evaluated with no bindings.” The angle
brackets are not a reduction engine by themselves. They ask Rust to resolve
an implementation whose associated type supplies the answer.

## A function needs its old address

Consider `λx. λy. x`. Supplying the first argument gives back a function
waiting for the second. That inner function must remember `x` when it is
eventually used elsewhere. Forgetting it would make [lexical scope][wiki-scope] a sort
of optimistic suggestion.

A **[closure][wiki-closure]** remembers the body and the environment where the lambda
appeared:

```rust
struct Closure<Body, Env>(PhantomData<(Body, Env)>);

impl<Body, Env> Evaluate<Env> for Lambda<Body> {
    type Output = Closure<Body, Env>;
}
```

That is our first rule. It handles every lambda, including identity. We
haven't evaluated the body. The argument hasn't even arrived yet.

The distinction between syntax and result matters: `Lambda<Body>` describes
a function; `Closure<Body, Env>` is what the evaluator produces when it
encounters that function. The saved environment contains surrounding
bindings, not the still-missing argument.

Stopping here makes this a **[weak-head][wiki-weak-head]** evaluator. It does not go tidying
up inside every function body before allowing the function to exist.[^weak]
The compiler has quite enough tidying up to do outside it.

## A small filing cabinet

We extend an environment by putting the newest binding at the front:

```rust
struct Bind<Binding, Older>(PhantomData<(Binding, Older)>);
struct S<N>(PhantomData<N>);
```

`Z` still means index zero. `S<Z>` means one, `S<S<Z>>` means two, and so on.
These are distances to binders, not our future arithmetic representation.
In `Bind<A, Bind<B, Empty>>`, zero selects `A`; one selects `B`.

```rust
trait Lookup<N> {
    type Binding;
}

impl<Binding, Older> Lookup<Z> for Bind<Binding, Older> {
    type Binding = Binding;
}

impl<N, Binding, Older> Lookup<S<N>> for Bind<Binding, Older>
where
    Older: Lookup<N>,
{
    type Binding = <Older as Lookup<N>>::Binding;
}
```

The first implementation opens the front drawer. The second skips a drawer
and repeats the question with a smaller index. Their shapes don't overlap:
`Z` and `S<N>` are different types.

There is no lookup implementation for `Empty`. An out-of-scope reference
doesn't receive a default value, a null, or a strongly worded warning. If
evaluation demands it, the required trait implementation does not exist.

## Postpone the argument, with supporting documentation

We chose **[call-by-name][wiki-name]** evaluation: don't evaluate an argument unless the
function needs it. Suspending just its syntax isn't enough. An argument can
itself refer to variables in the caller, so we must save that environment
alongside it:

```rust
struct Thunk<Term, Env>(PhantomData<(Term, Env)>);

trait Force {
    type Output;
}

impl<Term, Env> Force for Thunk<Term, Env>
where
    Term: Evaluate<Env>,
{
    type Output = <Term as Evaluate<Env>>::Output;
}
```

A [thunk][wiki-thunk] is an unevaluated expression with a forwarding address. Forcing it
evaluates the saved expression in the saved environment, not whichever
environment happens to be nearby at the time.

We haven't built a cache. “Call-by-name” here does not mean “evaluate once
and share the result”; that latter promise belongs to [call-by-need][wiki-need].[^need]
Our implementation is lazy in when it demands work, not an assurance of
economy. Naturally, the useless compiler experiment is also permitted to
repeat itself.

A variable lookup can now do something with the binding it finds:

```rust
impl<N, Env> Evaluate<Env> for Var<N>
where
    Env: Lookup<N>,
    <Env as Lookup<N>>::Binding: Force,
{
    type Output = <<Env as Lookup<N>>::Binding as Force>::Output;
}
```

Find the binding. Force its thunk. The nested projection is ugly, but the
rule is small. A suspiciously large fraction of this language consists of
writing small rules in typography that suggests a dental emergency.

## Connect the function to the argument

Application has two jobs. First, get a function result. Second, apply it
to the suspended argument. The helper for that second job is:

```rust
trait Apply<Argument> {
    type Output;
}

impl<Body, Env, Argument> Apply<Argument> for Closure<Body, Env>
where
    Body: Evaluate<Bind<Argument, Env>>,
{
    type Output = <Body as Evaluate<Bind<Argument, Env>>>::Output;
}
```

`Argument` is a thunk. Prepend it to the **closure's** environment, then
evaluate the body. Using the caller's environment here would lose lexical
scope. The closure brought its own filing cabinet for a reason.

The application rule supplies the thunk:

```rust
impl<Function, Argument, Env> Evaluate<Env> for App<Function, Argument>
where
    Function: Evaluate<Env>,
    <Function as Evaluate<Env>>::Output: Apply<Thunk<Argument, Env>>,
{
    type Output = <<Function as Evaluate<Env>>::Output as Apply<Thunk<Argument, Env>>>::Output;
}
```

Crucially, there is no separate eager `Argument: Evaluate<Env>` requirement.
We need the function's value to apply it. We do not yet need its argument's
value.

Keep the two environments distinct:

| Saved thing | Environment it remembers |
| --- | --- |
| Closure | Where the function was defined |
| Argument thunk | Where the argument expression was supplied |

That is lexical binding without rewriting the syntax tree through explicit
substitution. We have traded renaming work for environment work. This is
not a reduction in silliness; merely a change of department.

## Identity, at last

Ask for the result of an application:

```rust
type Expression = App<Identity, Identity>;
type ApplicationResult = <Expression as Evaluate<Empty>>::Output;
```

Here is the path, written out by a human rather than printed by Rust:

```text
function Identity in Empty   → Closure<Var<Z>, Empty>
argument Identity in Empty   → saved as Thunk<Identity, Empty>
apply closure               → evaluate Var<Z> with that thunk at the front
look up Z                   → find the argument thunk
force it                    → evaluate Identity in Empty
answer                      → Closure<Var<Z>, Empty>
```

The result is identity's closure. A truly heroic amount of work has allowed
something to remain itself.

## A test the compiler cannot politely ignore

Printed type names aren't a sound oracle. Instead, the tests use an equality
witness:

```rust
trait Same<T> {}
impl<T> Same<T> for T {}

fn assert_same<Actual: Same<Expected>, Expected>() {}
```

With only that implementation, the bound is satisfied when the two types
are identical. The function body can be empty because the interesting check
happens before the test runs. A wrong result can stop `cargo test` during
compilation.

The tests do not stop at identity. They exercise nested applications,
constant functions, multiple binding depths, and arguments supplied in one
scope but forced in another. They also check that the body of an unapplied
lambda stays unevaluated.

For laziness, the unchosen argument is the usual troublemaker:

```text
Ω = (λx. x x) (λx. x x)
```

It reduces to itself. We do not ask Rust to evaluate it alone. We supply it
where a constant function ignores it, and require the selected argument's
result instead. Passing this check means we avoided the work, not that we
found an innovative way to finish an infinite computation.

## This week's unnecessary accomplishment

The [source archive](source.tar.gz), snapshot `c224bfbb611c`, contains the
complete evaluator and the scope tests. Extract it, enter
`rust-lambda-types`, and run `cargo test --offline` and `cargo run --offline`.
The executable prints diagnostic type names; it is not a reduction trace.

For closed terms whose demanded evaluation terminates within compiler
limits, the result is a closure. Its body and environment can still contain
suspended expressions. Two different closures can behave identically; Rust
type equality is not general lambda equivalence.

We now have all three evaluation rules. Next week we will resist the urge
to add more and write some programs instead. First up: obtaining booleans
without allowing the compiler to use a boolean.

[wiki-scope]: https://en.wikipedia.org/wiki/Scope_(computer_science)#Lexical_scope
[wiki-closure]: https://en.wikipedia.org/wiki/Closure_(computer_programming)
[wiki-weak-head]: https://en.wikipedia.org/wiki/Weak_head_normal_form
[wiki-name]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_name
[wiki-need]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_need
[wiki-thunk]: https://en.wikipedia.org/wiki/Thunk

[^associated]: Rust's [associated-type reference](https://doc.rust-lang.org/reference/items/associated-items.html#associated-types)
    explains the syntax independently of this abuse. The `where` clauses
    express requirements for an implementation, not instructions in a
    runtime loop.

[^weak]: “Weak” means we do not reduce under lambdas; “head” concerns exposing
    the outer result. A function waiting for an argument is already a
    result for this evaluator, even if its body contains applications.
    Full normalization would be a different job, with more opportunities
    for the compiler to regret meeting us.

[^need]: Call-by-need adds sharing of demanded results. The source here has
    syntax-plus-environment thunks and a forcing rule, but no memoization
    mechanism. Compiler implementation details are not a language-level
    promise that each thunk is evaluated only once.
