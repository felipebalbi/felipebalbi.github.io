+++
title = "Lambda in types, part 12: The first true statement wins"
date = 2027-02-10T09:00:00
draft = true
description = "A Lisp-shaped Cond searches clause pairs through lambda-encoded recursion. False values can win, nil can mean two things, and no Rust pattern match chooses the result."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

Our language has individual choices and lists. It also has recursion,
which lets it keep making choices until somebody asks it to stop. We can
therefore implement a conditional that walks an ordered list of clauses
and selects the first true one.

We will call it `Cond`, borrowing the Lisp name. This is not a claim that
we have implemented Lisp. We have implemented enough of a resemblance to
make the angle brackets look like an administrative error.

<!-- more -->

**Part 12 of 16.** [Previous: recursion](/posts/lambda-types-11-recursion/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: readable syntax](/posts/lambda-types-13-macro/).

## The rules, before the punctuation

Each clause has a predicate and a value expression. We want three cases:

1. An empty clause list evaluates to nil.
2. A true first predicate selects that clause's value, without searching
   further.
3. A false first predicate skips its value and continues with the tail.

Exhausting a finite list without a match therefore also evaluates to nil.
There is no imperative `return`, break statement, or exception lurking
behind this description. We are specifying which expression is selected
for evaluation.

Predicates must follow our Church-boolean convention. Values can be any
lambda terms. A chosen false value is still a chosen value. A chosen nil
value is still a match. The committee votes on the predicate, not on
whether it likes the result.

## Clauses are pairs; the collection is a list

Represent one clause as:

```text
CONS predicate value
```

Its car is the predicate; its cdr is the value expression. This is a
**dotted pair**, not a proper two-element list. Put those pairs in a
proper outer list ending in nil.

Our example clauses, in order, are:

```text
(FALSE . ONE)
(TRUE  . TWO)
(TRUE  . THREE)
```

The first is skipped, the second wins, and the third gets to remain
unconsidered. Two is the answer. Three may file an appeal with an
unevaluated thunk.

The corresponding Rust syntax is still the old notation:

```rust
type FirstCondClause = App<App<Cons, False>, One>;
type SecondCondClause = App<App<Cons, True>, Two>;
type ThirdCondClause = App<App<Cons, True>, Three>;
type CondExample = App<App<Cons, FirstCondClause>,
    App<App<Cons, SecondCondClause>,
        App<App<Cons, ThirdCondClause>, Nil>
    >
>;
```

Nothing in this construction makes Rust choose a clause. We have only
described data for the lambda program to examine.

## Separate the recursive step again

Use the same arrangement as length: a step function receives its recursive
call as an argument.

```text
COND_STEP = λrecur. λclauses.
    (IS_NIL clauses)
        NIL
        ((CAR (CAR clauses))
            (CDR (CAR clauses))
            (recur (CDR clauses)))
```

There are two selections. The first is made by the emptiness predicate.
If the list is nonempty, the second is made by the first clause's
predicate. Here is the projection map:

| Expression | What it selects |
| --- | --- |
| `CAR clauses` | First clause pair |
| `CAR (CAR clauses)` | Its predicate |
| `CDR (CAR clauses)` | Its value expression |
| `CDR clauses` | The remaining clauses |

This is the point where a Lisp programmer might reasonably ask for
`caar` and `cdar`. We will instead continue spelling everything out,
because the series has apparently confused discomfort with suspense.

The body uses Church booleans **directly**, not the `If` alias. Applying
a boolean to two expressions already selects between them. Keeping
`Cond` independent of `If` avoids a dependency knot if we later want to
express conveniences through `Cond`. We do not change `If` today.

## All the search logic is one expression

There are only two binders in the step: `clauses = 0`, `recur = 1`.
The compact expanded type is:

```rust
type CondStep = Lambda<Lambda<
    App<
        App<App<IsNil, Var<Z>>, Nil>,
        App<
            App<
                App<Car, App<Car, Var<Z>>>,
                App<Cdr, App<Car, Var<Z>>>,
            >,
            App<Var<S<Z>>, App<Cdr, Var<Z>>>,
        >,
    >
>>;

type Cond = App<Y, CondStep>;
```

`Y` supplies the recursive behavior. This is not a recursive Rust alias,
a Rust-side loop through clause types, or a parser that evaluates the
conditions while building the expression.

Up to beta-equivalence, the cases are:

```text
COND NIL                                     =β NIL
COND (CONS (CONS TRUE value) rest)            =β value
COND (CONS (CONS FALSE value) rest)           =β COND rest
```

These are equations for the program's behavior, not assertions that the
resulting closure types are syntactically identical.

## False can win; nil can win

The selected value does not decide whether the search continues:

```text
COND (CONS (CONS TRUE FALSE) rest) =β FALSE
COND (CONS (CONS TRUE NIL) rest)   =β NIL
```

In both cases `rest` stays unneeded. In particular, selecting nil doesn't
mean “the attempt failed; try another clause.”

This design intentionally uses nil both for “no clause matched” and as a
possible matched value. The final result alone need not distinguish those
histories. If we wanted that distinction, we would choose an explicitly
tagged result protocol. We haven't, because there are limits to how many
things one ought to build for a joke.[^nil]

A final true predicate supplies a default through the ordinary rules.
There is no special `Else` token in the library. Don't infer full Scheme
or Common Lisp `cond` syntax or truth conventions from the familiar name.
This is ordered Church-boolean selection over our chosen data representation.

## Prove that the losing branches get no attention

Laziness is not decorative here. A false predicate must not force its
value. A true predicate must not force later predicates or the rest of
the list. An empty input must not demand the car of a nonexistent clause.

The tests place divergent `Ω` expressions at those boundaries:

- In a skipped value, both with and without a later match.
- In a predicate after the first match.
- As the entire unused tail after a match.
- After a matching nil value, to ensure nil doesn't restart the search.

They also check selected false used as a condition, computed predicates
and values, a third-clause match, a computed clause list, and a selected
cell inspected with our ordinary projections.

The selected numeral in the main example gets an independently specified
two-step wrapper probe. The executable's parity display is still not
sufficient by itself to prove that the correct clause won.[^observations]

A needed predicate can diverge. So can the selected value when demanded.
No match requires reaching the end of a finite usable clause list.
`Cond` is a selection strategy, not an amnesty program for bad inputs.

## The notation has earned a complaint

The [source archive](source.tar.gz), snapshot `b4f8dc313171`, contains the
program and tests. Extract it and run `cargo test --offline` and
`cargo run --offline` in `rust-lambda-types`. All clause selection happens
through the type evaluator; runtime Rust prints observation types.

We now have enough language to make the Rust spelling genuinely tiresome.
Next week we will add an S-expression macro. It will translate notation,
not compute answers. The bad idea stays exactly as bad; it merely becomes
easier to type.

[^nil]: “No match” and “matched nil” are operationally different paths
    whose final values deliberately coincide. Tests with divergent later
    clauses distinguish the paths even though a nil-result observation
    alone cannot. A sentinel is not automatically an option type.

[^observations]: Requiring the example's numeral behavior checks the selected
    value. Requiring skipped divergent expressions to stay unforced checks
    the search policy. These are complementary contracts; neither an
    attractive transcript nor one successful output value covers both.
