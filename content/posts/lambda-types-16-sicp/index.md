+++
title = "Lambda in types, part 16: Sixteen weeks to compute ten"
date = 2027-03-10T09:00:00
draft = true
description = "The gloriously useless computational finale: SICP-inspired map, filter, and accumulate, implemented as lambda terms and evaluated by Rust's type system. The answer is ten. We checked."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp", "sicp"]
+++

We have reached the computational finale of a seventeen-part series about
making a perfectly serviceable compiler do something it was not hired
to do. We represented lambda expressions as Rust types, built a general
evaluator, invented numbers out of repeated application, and eventually taught the output to
stop looking like a stack trace from a punctuation factory.

Today we will compose a filter, a map, and a fold. The result will be ten.
You may wish to check it on your fingers now, before we begin, so that
the suspense doesn't become unbearable.

<!-- more -->

**Part 16 of 17.** [Previous: readable structures](/posts/lambda-types-15-readable-structures/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series) ·
[Next: a love letter to Lisp](/posts/lambda-types-17-love-letter/).

## Borrow a good idea for a bad purpose

The inspiration is *[Structure and Interpretation of Computer Programs][wiki-sicp]*,
second edition, by Harold Abelson and Gerald Jay Sussman with Julie Sussman.
Its [section 2.2.3, “Sequences as Conventional Interfaces”][sequences]
organizes computations into stages connected by lists: select elements,
transform them, and combine the results. [Exercise 2.21][square-list]
also asks for a list-squaring procedure expressed through mapping.

These are good ideas. We will apply them to a context in which adding ten
seconds of compilation to obtain ten would still be a philosophical
improvement over obtaining it incorrectly. To be clear, that is not a
benchmark. I am declining to give this project performance marketing.

Our program is a small adaptation, not a transcription of SICP's full
example. The book's `sum-odd-squares` traverses a tree; we start with the
proper list we already have. No tree enumerator, Fibonacci generator,
or metacircular interpreter will sneak into the final paragraph.[^sicp]

The intended pipeline is:

```text
(1 2 3) → keep odd numbers → (1 3) → square → (1 9) → sum → 10
```

All domain computation remains lambda terms. We will not rescue the finale
with a native Rust iterator chain. That would be a perfectly reasonable
way to write a program, which is exactly why it would ruin this one.

## Two little functions

The input is our existing `NumeralList`: three [Church numerals][wiki-numerals] in
functional cells, terminated by our chosen nil. Squaring uses the
multiplication term we already derived:

```rust
type Square = lisp!((lambda (n) (Mul n n)));
```

Oddness is even smaller:

```rust
type Odd = lisp!((lambda (n) (n Not False)));
```

Start from false and toggle `n` times. Odd counts produce true. After
weeks of warning that parity is not an adequate arithmetic test, we have
finally found the one job for which parity is the entire specification.

Both are ordinary functions in the lambda language. They aren't Rust
closures, macro special forms, or names the evaluator recognizes.

## Map the transformation, not the plumbing

Mapping has two cases. Empty input evaluates to nil. A cell becomes a
cell containing the transformed head and the mapped tail. Use the same
step-and-fixed-point arrangement as length:

```rust
type MapStep = lisp!(
    (lambda (recur f xs)
        (If (IsNil xs)
            Nil
            (Cons (f (Car xs)) (recur f (Cdr xs)))))
);
type Map = lisp!((Y MapStep));
```

`recur` is a lambda-bound argument, not a recursive Rust alias. `Y` is
still the literal [fixed-point combinator][wiki-y]. The evaluator's three general
expression rules haven't acquired a mapping department.

Use the numeric list view from last week to see the result:

```rust
println!(
    "MAP Square NumeralList = {}",
    observe::render::<lisp!((Map Square NumeralList)), observe::List<observe::Number>>()
);
```

That displays `(1 4 9)`. The observer doesn't know what squaring is. It
only knows how to demand and render the chosen list-and-numeral behavior.

The tests also map `Not` over the boolean list to get `(false true)` and
map `(Add Two)` over the numeral list to get `(3 4 5)`. The latter keeps
its captured operand while the mapper supplies each next argument.
Our closure environments have made it into an ensemble cast rather
than remaining the subjects of isolated identity tests.

## Filter the selection

Filtering adds a predicate choice to the nonempty case:

```rust
type FilterStep = lisp!(
    (lambda (recur predicate xs)
        (If (IsNil xs)
            Nil
            (If (predicate (Car xs))
                (Cons (Car xs) (recur predicate (Cdr xs)))
                (recur predicate (Cdr xs)))))
);
type Filter = lisp!((Y FilterStep));
```

A true predicate keeps the head. A false predicate skips it. Either way,
the recursive call continues through the tail, preserving input order
among the retained elements.

SICP expresses its filter with `cond`. We use nested `If` applications
because our `Cond` expects an explicitly constructed list of clause
pairs. We could build those pairs here, but the article already has
enough parentheses to qualify for its own enclosure permit.

```rust
println!(
    "FILTER Odd NumeralList = {}",
    observe::render::<lisp!((Filter Odd NumeralList)), observe::List<observe::Number>>()
);
```

The result is `(1 3)`. Filtering `BooleanList` with `Identity` also works:
each stored boolean supplies its own keep-or-skip decision, leaving
`(true)`. There is no numeral-specific filter implementation to bypass.

## Accumulate toward the answer

SICP's `accumulate` is a **[right fold][wiki-fold]**. For elements `a`, `b`, and `c`,
it constructs this association:

```text
op a (op b (op c initial))
```

The initial value belongs at the empty tail. Each application supplies
the element first, then the accumulated rest. This is not a mutable
running total, although addition can make the distinction hard to see.

```rust
type AccumulateStep = lisp!(
    (lambda (recur op initial xs)
        (If (IsNil xs)
            initial
            (op (Car xs) (recur op initial (Cdr xs)))))
);
type Accumulate = lisp!((Y AccumulateStep));
```

Choose addition and zero:

```rust
println!(
    "ACCUMULATE Add Zero NumeralList = {}",
    observe::number::<lisp!((Accumulate Add Zero NumeralList))>()
);
```

That prints six. Choose cell construction and nil instead:

```rust
println!(
    "ACCUMULATE Cons Nil NumeralList = {}",
    observe::render::<lisp!((Accumulate Cons Nil NumeralList)), observe::List<observe::Number>>()
);
```

Now the result is `(1 2 3)`: a reconstructed list, not a number. The same
traversal can combine values into different encodings by receiving a
different operation and base. Higher-order functions have turned up
despite our best efforts to make their host environment embarrassing.

For association and argument order, addition alone is a weak test. We
also fold `Cons` over `(1 2)` with numeric zero as the initial tail.
The nested pair view must show `(1 . (2 . 0))`. A conventional left fold
or reversed operator arguments would expose a different shape.[^fold]

## The whole pointlessly elaborate expression

We can finally connect the stages:

```rust
type SumOddSquares = lisp!(
    (Accumulate Add Zero
        (Map Square
            (Filter Odd NumeralList)))
);

println!("Sum of odd squares = {}", observe::number::<lisp!(SumOddSquares)>());
```

Read from the inside outward: filter the input, square the kept numerals,
then combine the squares with addition starting from zero.

For the intermediate result, the executable also contains:

```rust
println!(
    "MAP Square (FILTER Odd NumeralList) = {}",
    observe::render::<lisp!((Map Square (Filter Odd NumeralList))), observe::List<observe::Number>>()
);
```

The actual finale output lines are:

```text
MAP Square NumeralList = (1 4 9)
FILTER Odd NumeralList = (1 3)
MAP Square (FILTER Odd NumeralList) = (1 9)
ACCUMULATE Add Zero NumeralList = 6
ACCUMULATE Cons Nil NumeralList = (1 2 3)
Sum of odd squares = 10
```

The executable test checks every line. The final ten is not a prediction,
not an integer literal planted in the formatter, and not a native sum
performed by the macro. It is the decoded observation of the lambda
program's numeral behavior.

Please take a moment to appreciate that we could have written `10`
in November.

## Lisp-shaped, not Scheme-shaped evaluation

We borrowed sequence-processing ideas from Scheme examples, not Scheme's
entire operational behavior. Ordinary Scheme procedure applications
evaluate their arguments eagerly; our evaluator is [call-by-name][wiki-name] and
weak-head. Our `If` can be a plain function because unused arguments
remain suspended.

That changes the demands made by the pipeline. A mapped list can expose
a head without traversing the tail. A filter must scan rejected heads
until it finds a kept one, but needn't inspect the kept head's later
tail. A fold's operator can ignore its second argument and avoid the
recursive fold entirely. Right association does not imply eager
tail-first execution.

The tests put divergent terms behind those boundaries. A constant mapper
can ignore an element. Empty map and filter can ignore their function
and predicate. Selecting a produced head can ignore a divergent tail.
Folding with `True` can select the first element without demanding either
the initial value or the recursive result.

This does not make the implementation an optimized streaming system.
There is no memoizing [call-by-need][wiki-need] machinery, no fusion pass, and no
performance claim. Suspended work can be demanded more than once. We
should not confuse avoiding one unnecessary computation with having
developed a sensible way to compute anything.

## Ten was slightly too deep for the default budget

There is a final joke, supplied by the compiler. The composed observation
exceeded Rust's default trait-recursion depth of 128, producing E0275.
The isolated `Square Three` and `Map Square NumeralList` observations
compiled at that depth; the full composition did not.

The crate therefore sets:

```rust
#![recursion_limit = "256"]
```

With that setting the complete pipeline and regression suite compile.
This increases the compiler's recursion allowance. It does not add a
fixed-point primitive, change the lambda evaluator, or provide a
termination argument. Rust had enough of our nonsense at 128; we asked
whether it could tolerate twice as much.[^budget]

The [source archive](source.tar.gz), snapshot `a42567ffccff`, is the exact
finale. After extracting it:

```sh
cd rust-lambda-types
cargo test --offline --workspace
cargo run --offline --quiet
```

This is a tested small program, not evidence that arbitrary SICP examples,
large inputs, or infinite structures fit the available compiler budget.
The host display number also remains bounded by `usize`.

## Where all the work went

The arrangement from the first post survives to the last:

| Layer | Job |
| --- | --- |
| `lisp!()` | Translate notation into syntax types |
| Rust traits | General variable, abstraction, application, and binding rules |
| Lambda terms | All booleans, arithmetic, cells, recursion, and sequence algorithms |
| Observers | Expose and decode a chosen interpretation |
| Runtime Rust | Format constants and rendered strings |

We did not add numbers to the evaluator to get arithmetic, add list
iteration to Rust to get mapping, or move clause selection into the macro
when the syntax got difficult. The general machinery is doing exactly
the unnecessary job we assigned to it.

## No product announcement follows

Game of Life remains unimplemented. So do membership, a chosen equality
policy, and association-list environments. The language is expressive;
the practical compiler budget and unbuilt algorithms are separate
questions. We do not need to turn every possible extension into homework.

Nor is there a crate release, a production recommendation, or a concluding
claim that this will improve your business logic. It won't. It is a
playful use of the type system, and it has now fulfilled its purpose by
having no useful purpose at all.

We began with three syntax constructors and ended with a little language
that filters, transforms, and combines encoded data. The answer is ten.
The compiler deserves a break. So do we.

Next week is an [epilogue, not another feature](/posts/lambda-types-17-love-letter/):
a love letter to Lisp, to its parentheses, and to McCarthy. The compiler
gets to sit that one out.

[sequences]: https://sarabander.github.io/sicp/html/2_002e2.xhtml#g_t2_002e2_002e3
[square-list]: https://sarabander.github.io/sicp/html/2_002e2.xhtml#Exercise-2_002e21

[wiki-sicp]: https://en.wikipedia.org/wiki/Structure_and_Interpretation_of_Computer_Programs
[wiki-numerals]: https://en.wikipedia.org/wiki/Church_encoding#Church_numerals
[wiki-y]: https://en.wikipedia.org/wiki/Fixed-point_combinator#Y_combinator
[wiki-fold]: https://en.wikipedia.org/wiki/Fold_(higher-order_function)
[wiki-name]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_name
[wiki-need]: https://en.wikipedia.org/wiki/Evaluation_strategy#Call_by_need

[^sicp]: The references are to the original Scheme-language second edition,
    not the JavaScript adaptation. We adapt the sequence interfaces and
    square-list idea to our existing pair/list encoding and smaller input.
    This is not an implementation of Scheme's reader, numeric tower,
    truth rules, or evaluation strategy.

[^fold]: Noncommutative structure is useful for observing association.
    Even if addition gives the same answer under several arrangements,
    `Cons` preserves the direction in the result's shape. This checks
    the right-fold contract rather than relying on arithmetic identities
    to make the fixture forgiving.

[^budget]: The original source was verified with rustc 1.99.1; the article
    snapshots and listings were rechecked with rustc 1.100.0. The
    [Rust Reference's `recursion_limit` documentation](https://doc.rust-lang.org/reference/attributes/limits.html#the-recursion_limit-attribute)
    describes a compiler limit, not a language-level recursion counter.
    Increasing it may just move a larger example's failure to compilation
    time or memory. Two hundred and fifty-six is not a scalability plan.
