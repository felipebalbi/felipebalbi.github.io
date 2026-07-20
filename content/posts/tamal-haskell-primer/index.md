+++
title = "Enough Haskell to Read Tamal"
date = 2026-07-19T09:00:00
description = "A short primer on the Haskell you need to read Tamal's Clash gateware — from f :: A -> B as ordinary mathematics, through a gentle tour of categories, to the algebraic data types, pattern matching, typeclasses, and type-level widths the block-by-block deep dives will lean on."
[taxonomies]
tags = ["haskell", "clash", "fpga", "tamal", "category-theory", "functional-programming"]
[extra]
math = true
+++

[Tamal] is an FPGA eSPI exerciser, and its gateware — the UART, the
cycle engine, the ALU, the CRC unit, the loader, the trace ring — is
written in Haskell and compiled straight to hardware by [Clash]. This
is the first post in a series that opens up that gateware one block at
a time and reads each one end to end.

There's a catch, and if you're here at all you can probably feel it
coming. Approach this code from C, Rust, or Verilog — which is where
most people who care about an eSPI exerciser are standing — and the
source can read like line noise. `::`, `->`, `\case`,
`deriving stock`, `BitVector 32`, a lone `mealy` perched atop a module
like an incantation. None of it is *hard*. All of it is unfamiliar,
and unfamiliar is enough to stop you at the door.

So before the series spends a whole post on the `Uart` or the
`Engine`, this one pays down the syntax tax up front. Think of it as a
Rosetta Stone: just enough Haskell that a Tamal signature stops being
an obstacle and starts being documentation.

Two honest caveats about scope. This is **not** a Haskell course —
there are excellent ones, and I'm not writing another — and it
deliberately steers *around* the deep theory: no monad tutorial, no
functor laws, no monoids, none of the machinery a "learn Haskell" book
leads with. The goal is narrow and practical: by the end you should be
able to look at a Tamal type signature, a `data` block, a `case`, and
the `mealy` at the top of an engine and know exactly what you're seeing
— not prove theorems about it, just *read* it. That's the whole job.

<!-- more -->

[Tamal]: https://github.com/felipebalbi/tamal
[Clash]: https://clash-lang.org
[intro]: @/posts/tamal-introducing/index.md

## `f :: A -> B` is just `f : A → B`

Start with the single most important translation, because once it
clicks the rest follows. In mathematics you write a function as
$f : A \to B$, read aloud as "$f$ takes an $A$ and gives you a $B$."
That arrow is the entire idea.

Haskell writes the exact same statement as `f :: A -> B`. The `->` is
the arrow. The only oddity is the doubled colon: `::` reads "has
type." A single `:` would have been the obvious choice, but it was
already spoken for — in Haskell `:` is the list-cons operator — so the
type annotation had to take the double. Read `f :: A -> B` as "`f` has
type $A \to B$," and you are reading the mathematics.

Here is the smallest honest example:

```haskell
double :: Int -> Int
double n = n * 2
```

The first line is the type; the second the definition. And I do mean
*mapping*, not *procedure*. `double` is not a recipe of steps that
mutate something on the way to a result — it is the mathematical object
that associates each `Int` with another, no more alive than the
function $x \mapsto 2x$ on a chalkboard. Feed it `21` and you get `42`,
today, tomorrow, on every core, with nothing else happening anywhere.
That last clause has a name: **purity**. Same input, same output, no
side effects, no hidden state read or written.

If you build hardware you already own the mental model, and it is worth
stating loudly because it is load-bearing for everything Clash does:

> A block of combinational logic is a pure function of its inputs.

A truth table is literally $f : \text{inputs} \to \text{outputs}$:
same inputs, same outputs, every time, no memory of what came before —
that *is* purity, in gates instead of lambda calculus. It is the whole
reason a functional language can describe a circuit at all: your
design's combinational core and a pure Haskell function are the same
kind of object, and Clash is the compiler that notices.

## A quick tour of categories

Once functions are arrows, the natural question is: what do you
actually *do* with them? Strip it down and there are exactly two
moves.

The first is **composition**. Given $f : A \to B$ and $g : B \to C$ —
the target of the first matching the source of the second — you run
them nose to tail and get a single arrow $A \to C$. Mathematicians
write it $g \circ f$ ("$g$ after $f$"); Haskell writes it `g . f`, a
single dot:

```haskell
-- (.) :: (b -> c) -> (a -> b) -> (a -> c)
pipeline = decode . validate . receive
```

Read that right to left: `receive`, then `validate`, then `decode`.
The comment is just the arrow-matching rule spelled out — give `.` an
arrow `b -> c` and an arrow `a -> b`, and it hands back the composite
`a -> c`. If you have ever built a datapath as a chain of stages, each
feeding the next, you have built exactly this.

The second move is humbler still: the **identity**, a do-nothing arrow
$A \to A$ that hands its input straight back. It sounds too trivial to
mention, but it is the neutral element that makes composition
well-behaved — the wire from a stage to itself.

Those two ingredients, plus two laws, are the entire definition of a
**category**: some *objects*, *arrows* between them, a way to
*compose* arrows whose ends line up, and an *identity* arrow on every
object, subject to

$$ f \circ \mathrm{id} = f = \mathrm{id} \circ f $$

$$ (h \circ g) \circ f = h \circ (g \circ f) $$

The first law says the identity truly does nothing; the second says
composition doesn't care how you parenthesise a chain — only order
matters, not grouping. That is the whole axiom set. There is no third
page.

Now the part that trips people up. Sets with functions between them do
form a category — objects are sets, arrows are functions, composition
the ordinary $g \circ f$ — and it has a name, **Set**. Haskell types
with functions form another. So it can *sound* like categories are
just "sets, restated." They are not.

Here is a category with no sets and no functions in sight. Take any
**preorder** — a set with a $\le$ relation that is reflexive and
transitive, like divisibility on the integers. Make the elements the
*objects*, and draw one arrow $a \to b$ precisely when $a \le b$.
Composition? If $a \le b$ and $b \le c$ then $a \le c$: that
transitivity *is* the composite. The identity? $a \le a$: reflexivity.
The laws hold for free, and you have a category in which an "arrow" is
not a mapping of anything — it is a *fact*, that one thing sits below
another.

That is the honest version of the "superset" story. Category theory
does not *contain* set theory — **Set is one example among many.** It
steps back from *what the objects are made of* — sets, integers,
types, states — and studies only the pattern they share: things,
arrows, and a law-abiding notion of composition. Forget the elements;
keep the plumbing.

You need none of this to type a `.` into a Haskell file. But it buys
the right mental model for the rest of this post:

> Composition is the primary verb.

A type signature full of `->` is not a scary pile of punctuation. It
is a chain of arrows, and your job when reading Tamal is usually just
to see where each arrow starts, where it ends, and how they snap
together.

## Arrows in a row: currying

If a function is one arrow from one input to one output, here is a
puzzle. Why does addition have *two* arrows?

```haskell
add :: Int -> Int -> Int
add x y = x + y

addFive :: Int -> Int
addFive = add 5
```

The resolution is a single rule: `->` associates to the **right**. So
`Int -> Int -> Int` secretly means `Int -> (Int -> Int)` — "give me an
`Int`, and I hand you back *a function* of type `Int -> Int`." `add`
does not take two arguments at all: it takes one `Int` and returns a
function still waiting for the second. Every Haskell function takes
exactly one argument; the multi-arrow signatures everywhere are sugar
for this chain. The trick has a name — **currying**, after Haskell
Curry, who also lent the language its first name.

The payoff is that you can stop halfway. `add 5` is a complete value —
the function that adds five to whatever comes next. Supplying some
arguments now and the rest later is **partial application**, and
`addFive` is exactly that.

The hardware reading is direct, and worth holding onto:

> Partial application is a circuit family parameterised by
> configuration.

Fix the configuration bits now; feed the data later. `add 5` is the
adder with one operand strapped to a constant — a specialized block
carved out of a general one. You will see this shape all over Tamal:
the ALU's signature
`alu :: AluOp -> BitVector 32 -> BitVector 32 -> BitVector 32` reads
left to right as "pick an operation, then hand me two 32-bit operands,
and I'll give you a 32-bit result." Thanks to currying, `alu Add` is
already meaningful on its own — the two-operand adder, carved out of
the general ALU by pinning the opcode. Configuration first, data
second, all the way down.

## Types as data: algebraic data types

Now the syntax you will meet more than any other: `data`, the keyword
that builds new types. Haskell's are called **algebraic data types**,
and the name is a promise about how they are built — from two
operations you already know, even if never by these names.

Start with the simpler one, the **sum type**. Each `|` is an
alternative — read it as "or":

```haskell
data Color = Red | Green | Blue
```

A `Color` is `Red` **or** `Green` **or** `Blue`, and nothing else.
This is a C or Rust `enum`, and if that were all you ever used it for
it would already earn its keep. Tamal's engine lifecycle is exactly
this shape — a `Phase` that is `Idle` or `Preamble` or `Fetch` or one
of a handful of other named states, the FSM's state register written
as a type.

The other operation is the **product type**, better known as a struct
or record — a bundle that holds one field **and** another **and**
another:

```haskell
data Point = Point { x :: Int, y :: Int }
```

A `Point` carries an `x` and a `y`, both at once. The record syntax
throws in a gift: each field name is also an accessor function. Write
that `data` and you get `x :: Point -> Int` for free, no boilerplate.
Tamal's engine state is one big record of this kind, a
`State { phase, pc, regs, ... }` threaded through every step — program
counter, register file, current phase, all in one value.

Constructors are allowed to carry data, which is where sum and product
combine into something sharper than a C `enum`:

```haskell
data Shape
  = Circle Double
  | Rectangle Double Double
```

A `Shape` is *either* a `Circle` carrying one `Double` (its radius)
*or* a `Rectangle` carrying two (width and height). The tag and its
payload travel together — and, as the next section shows, you cannot
reach the payload without first checking the tag. A Rust programmer
will recognize this immediately; it is a data-bearing `enum`.

So why "algebraic"? Because you are literally doing arithmetic on
types. A sum **adds** possibilities; a product **multiplies** them.
Count the inhabitants and the algebra is exact: $|A + B| = |A| + |B|$
for a sum, and $|A \times B| = |A| \cdot |B|$ for a product. `Bool`
has 2 values; the pair `(Bool, Bool)` has $2 \cdot 2 = 4$;
`Either Bool Bool` has $2 + 2 = 4$. That counting is no party trick —
it is exactly why the compiler can tell whether you have handled every
case, which matters a great deal for state machines.

One member of the family deserves special mention, because it is how
Haskell abolishes the null pointer:

```haskell
data Maybe a = Nothing | Just a
```

`Maybe a` has two constructors: `Nothing`, carrying no data, or
`Just`, carrying an `a`. It is the type of "a value that might be
absent" — but the absence lives *in the type*, out in the open. There
is no null `a` hiding inside an ordinary `a` waiting to segfault you;
if a value might be missing, its type says `Maybe`, and you can't use
it as a bare `a` — you have to pattern-match, and if you skip the
`Nothing` case the build warns you, by name, before you ever run it.
In Tamal a step returning a `Maybe Ring` states a fact about hardware:
*at most one trace-RAM write happens this cycle* — zero or one, never a
smuggled "sometimes." A write-enable line, promoted to a type.

## Taking types apart: pattern matching

If `data` is how you build a value, **pattern matching** is how you
take one apart — and it is the mirror image, constructor for
constructor. The tool is the `case` expression:

```haskell
area :: Shape -> Double
area s = case s of
  Circle r      -> 3.14159 * r * r
  Rectangle w h -> w * h
```

Read `case s of` as "look at which `Shape` `s` actually is." If it was
built with `Circle`, the first branch fires and binds `r` to the
radius packed inside; if `Rectangle`, the second binds `w` and `h`.
This is the "you can't get the payload without checking the tag" rule
made concrete: the only route to `r` is to match `Circle r`, and inside
that branch the compiler *knows* a radius is there.

When a function is nothing but one `case` on its final argument — which
is most of the time — there is a shorthand that drops the ceremony,
`\case`:

```haskell
area' :: Shape -> Double
area' = \case
  Circle r      -> 3.14159 * r * r
  Rectangle w h -> w * h
```

Same behaviour, one fewer name to invent; `\case` is "a function that
immediately pattern-matches on the argument I didn't bother to name."
You will see it constantly in Tamal — most of the gateware's logic *is*
a match on some state or opcode.

Here is the part that should make a hardware engineer sit up straight.
The compiler does **exhaustiveness checking**: if your `case` over a
sum type leaves out a constructor, you get a warning pointing at the
exact one you missed. Now recall that a `Phase` is a sum type and the
engine's next-state logic is a `case` over it. That means the question
every FSM review is supposed to ask —

> Did you handle every state?

— is answered by the compiler, on every build, for free. Add a new
`Phase` and forget to wire it into the next-state logic, and the build
tells you before you run a test, let alone open a waveform. It is the
most boring flavour of formal-ish guarantee, and one of the most
valuable: a whole category of "unhandled state" bugs surfaces at build
time instead of in a waveform.

## Types with behaviour: typeclasses and `deriving`

The last piece of everyday syntax is how Haskell attaches *behaviour*
to types. A **typeclass** is an interface — a named set of operations
a type can choose to support:

```haskell
class Eq a where
  (==) :: a -> a -> Bool
```

This declares `Eq`, the class of types whose values can be compared:
an `Eq` must supply a `==` taking two of its values and returning a
`Bool`. A concrete type opts in with an **instance** — the actual `==`
for `Int`, for `Color`, for whatever. If you write Rust this is a
**trait**; in modern C++, a **concept**. What it is *not*, despite the
keyword, is an object-oriented class: no fields, no constructors, no
inheritance tree, no `this`. A typeclass is a contract a type
satisfies, not a box a value lives inside.

Typeclasses exist to enable **bounded polymorphism** — one function
that works for *any* type, provided it supports the operations you
need. The signature carries the requirement:

```haskell
elem :: Eq a => a -> [a] -> Bool
```

Read the `=>` as a **constraint arrow**: "for **any** type `a` that is
an `Eq`, `elem` takes an `a` and a list of `a` and says whether the
value is in the list." The part before `=>` is a precondition on the
caller — *given that `a` can be compared* — and the part after is the
ordinary type. `elem` does not care whether `a` is `Int` or `Color`; it
only asks that `==` exist.

Which brings us to the incantation stapled to nearly every `data`
declaration in Tamal:

```haskell
data Phase = Idle | Running | Halted
  deriving stock (Generic, Show, Eq, Enum, Bounded)
  deriving anyclass (NFDataX)
```

`deriving` is the compiler offering to write the boring instances for
you, so you never hand-roll a `==` that just compares constructors. The
list looks like noise the first time; here is what each name buys, so
it stops looking like noise:

- **`Show`** — the type is printable, so a failing property test can
  show you the exact value it choked on.
- **`Eq`** — values can be compared with `==`.
- **`Enum`** and **`Bounded`** — "list me every constructor, in
  order," which is exactly what an exhaustive test over all `Phase`s
  needs.
- **`Generic`** — a structural, machine-readable description of the
  type that other libraries build on top of; plumbing, rarely read by
  humans.
- **`NFDataX`** — the one that is really about hardware. It is Clash's
  way of saying **this type is allowed to sit in a register** — that
  its values, including "undefined at power-up," are well-defined
  enough to be stored in a flip-flop.

The `stock` versus `anyclass` split is only *which machinery* writes
the instance — a built-in strategy for the classic classes, a
generics-based one for `NFDataX`. It is a detail you will copy more
often than you think about. The takeaway: a wall of `deriving` atop a
Tamal type is not doing anything deep — it is the compiler filling in
equality, printing, enumeration, and "fits in a register," so the
author didn't have to.

## Types that count: widths in the type

Here is the feature that startles newcomers most, and delights them
shortly after: in Clash, **numbers can live in types.**

```haskell
x     :: BitVector 32   -- exactly 32 bits
lanes :: Vec 4 Bit      -- exactly 4 elements
i     :: Index 5        -- a value 0..4, and never 5
```

The `32`, the `4`, and the `5` are not runtime values or comments.
They are *part of the type*, checked at compile time like everything
else. `BitVector 32` is the type of exactly-32-bit vectors — not "up to
32," not "32 by convention," exactly 32. `Vec 4 Bit` is a vector of
exactly four bits. `Index 5` is a number in the range `0..4` and, by
construction, *never* 5 — the natural type for an index into something
with five slots.

Because the width is in the type, the compiler enforces it. You cannot
index past the end of a `Vec 4`, because the index type will not let
the out-of-range value exist in the first place. You cannot wire a
`BitVector 4` into a port expecting a `BitVector 8`; they are different
types and the mismatch is a compile error. The clean way to say what
that buys you:

> The compiler is a wire-width checker.

Anyone who has written RTL knows the dread here. In Verilog a width
mismatch is not an error — it is a silent zero-extension or, worse, a
silent truncation you discover three days later as a dropped high bit.
In Clash the same mistake is a type error, reported by name, before you
run a single test. Tamal leans on this everywhere: an `Unsigned AW`,
where `AW` is the address width, carries the program-counter size in
the type; an `Index 5` pins a five-state micro-FSM step to exactly its
five legal values. A whole class of off-by-one-bit bugs never makes it
past the compiler — a strictly better place to catch them than a logic
analyzer.

## From pure functions to hardware: `Signal` and `mealy`

Everything up to now has been combinational: pure functions, inputs to
outputs, no memory. But hardware has clocks, and sequential logic needs
a notion of **time**. Clash spells time with a type called `Signal`:

```haskell
counter :: Signal dom (Unsigned 8)
```

Read `Signal dom a` as "a value of type `a` that may change on every
clock tick, in clock domain `dom`." It is an endless stream — one
sample per cycle, across all of time — which is precisely what a wire
*is* once you stop looking at a single instant and watch it over the
whole run. The `dom` parameter names the clock domain, so the type
system can stop two clocks from crossing by accident; for reading Tamal
you can mostly gloss it as "the clock."

The bridge from the pure world to the clocked one is a single function,
`mealy`:

```haskell
-- mealy :: (s -> i -> (s, o)) -> s -> (Signal dom i -> Signal dom o)
step :: State -> Input -> (State, Output)
```

Look past the parameters and the shape is simple. You write an
ordinary, pure step function — current state and input in, next state
and output out — plus an initial state. `mealy` takes those two things
and hands back a function on `Signal`s: a real register holding the
state, clocked, with your step function as its next-state and output
logic. That is a textbook **Mealy machine**, which is where the name
comes from, and it is the heart of the design — Tamal's engine is,
quite literally, `mealy stepM initState`.

This is the punchline behind the [introduction post][intro]'s claim
that its tests run in under a second. `stepM` is just a function —
`State` and `Input` in, new `State` and `Output` out, pure, no clock,
no simulator — so you can hammer it with thousands of property tests in
milliseconds against a reference model, and only *after* you trust it
does `mealy` make it hardware. The pure core is the part you test;
`mealy` is the thin bridge to silicon. We'll pull that thread in the
block posts; for now it is enough to spot the `mealy` at the top of a
module and read it as "clocked state machine, its brain the pure
function beside it."

## Where to go next

That is the whole toolkit. Look back at what you can now read without
flinching: a Tamal type signature is $f : A \to B$ with the widths
written into the types; a `data` block is sums and products, with
`Maybe` for the things that might be absent; a `case` or `\case` takes
those values apart, exhaustively, with the compiler checking your work;
the stack of `deriving` clauses is the compiler filling in equality,
printing, enumeration, and "fits in a register"; a `BitVector n` or an
`Index n` is a wire whose width the type system guards; and the `mealy`
at the top of a module is a clocked state machine wrapped around an
ordinary, testable function.

None of that is the *interesting* part of Tamal. It is the syntax you
had to stop tripping over so the interesting part could show through.
Next in the series we do exactly that: open a single block and read it
end to end, top to bottom, no hand-waving. I'll start with a gentle
one — the CRC unit, or the UART, something small enough to hold whole
in your head — now that the Haskell is no longer standing between you
and the hardware. Bring the mental model of arrows snapping together;
that is most of what you will need.
