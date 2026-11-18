+++
title = "Lambda in types, part 15: Putting a nice frame around the nonsense"
date = 2027-03-03T09:00:00
draft = true
description = "Composable output views for mixed pairs, trees, and proper lists. The formatter learns about empty cases without making Car Nil a job requirement."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp"]
+++

Last week the program finally printed numbers as numbers. This week it
will print pairs and lists as pairs and lists. We are approaching the
dangerous stage where the output looks reasonable enough to conceal
what happened to produce it.

Don't be fooled. The arithmetic is still lambda evaluation through Rust
types. We are merely adding parentheses to the evidence.

<!-- more -->

**Part 15 of 16.** [Previous: readable scalars](/posts/lambda-types-14-readable-results/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: the SICP finale](/posts/lambda-types-16-sicp/).

## Describe how to look at the value

An untyped term doesn't carry an intrinsic number, boolean, pair, or
list tag. Last week's explicit scalar interpretation therefore becomes
a composable **view schema**:

```rust
use observe::{Boolean, List, Number, Pair, render};

println!("{}", render::<lisp!(BooleanCell), Pair<Boolean, Boolean>>());
println!("{}", render::<lisp!(NumeralList), List<Number>>());
```

The first prints `(true . false)`; the second prints `(1 2 3)`.

These Rust names describe output, not new values in the lambda language.
`Number` doesn't replace a Church numeral. `Pair<A, B>` doesn't replace
our selecting-function cell. `List<E>` doesn't replace our nil-terminated
chain. No lambda program constructs these schemas, and no evaluator
rule knows what they are.

The common interface in the observer module is:

```rust
trait View<Term> {
    fn render() -> String;
}
```

The `Number` and `Boolean` implementations reuse the existing readers
and format their associated constants. The interesting part is letting
structured schemas ask other schemas to render smaller observations.
We have invented compositional pretty-printing for a language whose
previous output strategy was “show everyone the type and run away.”

## A pair is two projections and some punctuation

For `Pair<First, Second>`, use the first view on `Car Term` and the
second on `Cdr Term`:

```rust
impl<Term, First, Second> View<Term> for Pair<First, Second>
where
    First: View<lisp!((Car Term))>,
    Second: View<lisp!((Cdr Term))>,
{
    fn render() -> String {
        format!("({} . {})", First::render(), Second::render())
    }
}
```

This excerpt lives in `src/observe.rs`, alongside the schemas and imported
lambda definitions. The formatter doesn't pattern-match a native pair of
language values. It names two lambda projection expressions. Those must
expose behavior that the chosen field readers can interpret.

Both fields can have different schemas:

```rust
type Mixed = lisp!((Cons One False));
println!("{}", render::<Mixed, Pair<Number, Boolean>>());
```

The result is `(1 . false)`. A numeric first field and boolean second
field require no special mixed-pair implementation. The view parameters
already say what each field means.

A pair view **always** uses dotted notation. If its second field is
viewed as a proper list, it can display `(1 . (true false))`. We don't
flatten it into a list behind the caller's back. Even our parentheses
are now contractually obliged to be honest.

## Let the schemas nest

Our old `NestedCell` holds one and the boolean pair:

```rust
type Shape = Pair<Number, Pair<Boolean, Boolean>>;
println!("{}", render::<lisp!(NestedCell), Shape>());
```

This prints `(1 . (true . false))`. Put a pair on each side and use
`Pair<Pair<Number, Number>, Pair<Number, Number>>` to display a small
branching tree as `((1 . 2) . (3 . 4))`.

Fields needn't be literal terms. A pair containing `(Succ Zero)` and
`(Length NumeralList)` displays `(1 . 3)`. There is no formatter branch
for either operation. The readers ask for numeral behavior and get it
through the evaluator, which is still being held responsible for our
arithmetic decisions.

## A list's empty case must exist before its head

Pairs are straightforward because both fields are required by the pair
contract. Proper lists need an empty case, and the empty case must not
require a head or tail projection.

This cannot be fixed merely by writing a runtime `if` around a rendering
call whose generic bounds already demand `Car Nil`. Trait requirements
are established at compilation; runtime restraint arrives too late to
impress them.

Instead, use the same boolean markers as last week to observe `IsNil`:

```rust
type ListProbe<Term> = lisp!((IsNil Term Identity SecondChoice));
```

An empty list selects the identity marker. A nonempty cell selects
`SecondChoice`, the `λx. x x` marker. The observed **type** chooses the
reader implementation before imposing the branch-specific requirements.

The empty reader is especially satisfying in a series with this much
machinery:

```rust
trait ListItems<Term, Element> {
    fn append(items: &mut Vec<String>);
}

impl<Env, Term, Element> ListItems<Term, Element> for Closure<Var<Z>, Env> {
    fn append(_items: &mut Vec<String>) {}
}
```

It does nothing. At last, an implementation that reflects the project's
economic contribution.

More importantly, its bounds require **no element renderer and no
projections**. The nonempty implementation has different obligations:
render `Car Term` with `Element`, evaluate the next `ListProbe` on
`Cdr Term`, and let that observed marker continue the protocol.

The regression test gives the nil case an element schema with no
renderer at all. It still produces `()`. This is a more direct check
of empty-branch independence than using a normal numeric schema and
hoping invalid work happened not to be noticed.

## About that suspicious Vec

Yes, the reader uses `Vec<String>`. No, we haven't finally admitted
defeat and moved the language's lists into ordinary Rust.

Each statically selected nonempty reader appends an already rendered
element string. The final view joins the strings with spaces and adds
outer parentheses. The vector stores **output text**, not lambda values
used to compute the answer.

The list's shape, emptiness choices, and projections are determined
through type-level evaluation. Runtime Rust formats the observations;
it doesn't take a native list of inputs and run the domain's list
algorithm over them. Nothing in that output buffer goes back into the
lambda program.[^buffer]

We are allowed a printer. Requiring a lambda-encoded allocator for the
printer would be a different joke, and I would prefer not to tell it.

## Several sorts of parentheses

The schemas compose for list elements too:

| View | Example display |
| --- | --- |
| `List<Number>` | `(1 2 3)` |
| `List<Boolean>` | `(true false)` |
| `List<Pair<Number, Boolean>>` | `((1 . true) (2 . false))` |
| `List<List<Number>>` | `((1 2 3) () (4))` |

Nil and a one-element list containing nil remain distinct:

```text
NIL          → ()
CONS NIL NIL → (())
```

The same second term viewed as
`Pair<List<Number>, List<Boolean>>` displays `(() . ())`. The schema
changes the presentation, not the underlying encoding. Likewise a list
containing zero displays `(0)` with a numeric element view and `(false)`
with a boolean view, because those scalar terms coincide.

The input spine can also be computed:

```rust
println!("{}", render::<lisp!((Drop One NumeralList)), List<Number>>());
```

That prints `(2 3)`. The tests cover this and lists of computed arithmetic
fields, so neither navigation nor arithmetic can be replaced by a
formatter that only recognizes the examples' literal source shapes.

## Human-readable is not a serialization format

The output is for reading. `lisp!()` does not parse numeric literals,
dotted-pair notation, or printed `()` as an empty list. We haven't
promised round-tripping or a stable interchange format.[^roundtrip]

Pair views require nonempty cells. List views require finite proper
lists for full rendering. Their fields need the selected interpretations.
A divergent field or infinite spine can prevent compilation when the
view demands it. A pleasant set of parentheses isn't a termination proof.

The [source archive](source.tar.gz), snapshot `29e0f977a177`, includes the
schemas, nested and empty cases, computed fields and spines, and real
executable-output checks. Extract it and run `cargo test --offline --workspace`
and `cargo run --offline --quiet` in `rust-lambda-types`.

Next week is the finale. We will take recognizable ideas from SICP and
use all this machinery to calculate ten. Sixteen weeks. Ten. I think
you'll agree the schedule left ample room for efficiency.

[^buffer]: A native container used to assemble output is not the same
    thing as a native implementation of the encoded data structure's
    computation. The boundary is what the values are used for. Formatting
    strings stays on the observation side; filter/map/fold over language
    inputs must remain lambda programs.

[^roundtrip]: A printer and a reader can intentionally support different
    surface forms. Here the macro is a constructor of syntax types,
    while the renderer is an encoding-specific observation of evaluated
    behavior. Treating them as inverse functions would assign both a
    contract neither implements.
