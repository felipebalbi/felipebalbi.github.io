+++
title = "Lambda in types, part 17: Dear Lisp, sorry about Rust"
date = 2027-03-17T09:00:00
draft = true
description = "An epilogue and a love letter to Lisp: visible structure, homoiconicity, macros, and McCarthy's remarkable insight. Affection is not a firmware deployment strategy."
[taxonomies]
tags = ["rust", "lambda-calculus", "type-system", "lisp", "history"]
+++

Last week the compiler calculated ten. It has been released back into
the wild, where I hope it finds a nice project involving an ordinary
`Vec` and nobody asks it to implement arithmetic as functions again.

There is one thing left to explain. This series was, underneath the
angle brackets and the poor decisions, a love letter to [Lisp][wiki-lisp].
I love Lisp. Making Rust suffer for sixteen weeks was apparently how
I chose to express that. Some people buy flowers.

<!-- more -->

**Part 17 of 17.** [Previous: the computational finale](/posts/lambda-types-16-sicp/) ·
[Series index](/posts/lambda-types-01-syntax/#the-series).

## The parentheses are showing you the tree

Consider this [S-expression][wiki-sexpr]:

```lisp
(+ 2 (* 3 4))
```

For this simple list syntax, the structure is almost impolitely explicit:
open a parenthesis, start a list; read its elements; open another, start
a child list; close it, finish that child and resume the enclosing list.

```text
list
├── +
├── 2
└── list
    ├── *
    ├── 3
    └── 4
```

The multiplication sits inside the addition. We don't recover that fact
from an operator-precedence table; we can see it. Parentheses are not
decoration around the program's structure. They are announcing it.

I understand that the punctuation can look alarming at first. We just
spent twelve installments making people read nested Rust generic types,
so I am not in a position to lecture anyone about welcoming typography.
But once indentation becomes familiar, the closing parentheses mostly
confirm a shape the layout has already shown you.

The beauty, for me, is that the source doesn't hide its grouping and
then make you reconstruct it. Here is the tree. You're welcome to walk
around. Please don't trip over the cdr.

## Very close to its own AST, with an important distinction

Calling Lisp “its own [abstract syntax tree][wiki-ast]” captures the
attraction, but compresses two stages. The **reader** turns source text
into Lisp objects: lists, symbols, numbers, and so on. The evaluator or
compiler then interprets those objects as forms. That reader-produced
structure is not necessarily the compiler's final internal AST.[^reader]

An ordinary call evaluates arguments and calls a function. A special
operator such as `if` has its own evaluation rules. A macro receives
unevaluated forms and supplies another form. Similar-looking lists can
therefore play different roles without the reader needing to implement
every operation's meaning.

Quotation makes the separation wonderfully visible:

```lisp
(+ 2 (* 3 4))          ; evaluates to 14
'(+ 2 (* 3 4))         ; the list structure, not the arithmetic result
```

The apostrophe abbreviates `quote`. We can hold the second form as data,
take it apart, construct related forms, and pass them to ordinary list-
processing functions. The plus symbol is an element in a list; it doesn't
start adding merely because we looked at it. A useful quality in a guest.

This close relationship between program representation and the language's
ordinary data is the attraction of [homoiconicity][wiki-homoiconicity].
It doesn't mean all data is executable, that comments survive reading,
or that the source text is identical to a heap dump. It means structured
program forms aren't exotic artifacts that only the compiler gets to touch.

The examples here are **[Common Lisp][wiki-cl]**, not additions to our `lisp!()`
macro. Our toy frontend has neither numeric literals nor quotation.
It remains a very small guest wearing an ambitious name badge.

## The language hands you its building materials

That representation makes [macros][wiki-macros] particularly natural.
Instead of generating source strings and hoping the quotes balance,
transform structured forms into other structured forms.

Here is an intentionally tiny Common Lisp macro:

```lisp
(defmacro when-curious (condition &body body)
  `(if ,condition
       (progn ,@body)
       nil))
```

Backquote builds a template. Comma inserts a form; comma-at splices the
body's sequence of forms into the surrounding list. `progn` evaluates
those forms in order and supplies the last value. We have invented a
less sensibly named version of the standard `when`, because this series
has a commitment to unnecessary construction.

Use it like this:

```lisp
(when-curious (> 3 2)
  (write-line "Still not writing a device driver.")
  (+ 2 3))
```

Its expansion is:

```lisp
(if (> 3 2)
    (progn
      (write-line "Still not writing a device driver.")
      (+ 2 3))
    nil)
```

The macro transforms syntax; it doesn't evaluate the comparison during
expansion. The resulting `if` supplies conditional evaluation. An ordinary
eager function couldn't suppress evaluation of already supplied arguments
in the same way.[^macros]

This is the strength I find so elegant: the language lets a library
participate in shaping how a program is expressed. Not just another
function in a fixed vocabulary, but a new arrangement built from the
same materials. Resource-management forms and small domain-specific
notations can be expressed without assembling a separate string parser.

[Hal Abelson][wiki-abelson] put it rather better in SICP lecture 3A:

> Lisp is a lousy language for doing any particular problem. What it's
> good for is figuring out the right language that you want and embedding
> that in Lisp.[^abelson]

The important word is *embedding*. Your little language doesn't have to
rebuild functions, recursion, and abstraction before it can do anything
interesting. It can keep using Lisp's. The host supplies the workshop;
you supply the questionable extension to the premises.

It is not a guarantee of taste. You can duplicate evaluation accidentally,
capture a caller's variable, or invent a private language everyone else
must learn. Common Lisp macro authors need to manage introduced names;
[hygienic macro][wiki-hygiene] systems address related problems differently.
Access to the building materials includes permission to construct a shed
in front of the fire exit.[^our-shed]

The [downloadable examples](examples.lisp) run with `sbcl --script examples.lisp`.
They check the expansion and the condition's single evaluation as well
as the arithmetic. No seventeenth evaluator feature is hiding here;
the Rust experiment's [final snapshot](/posts/lambda-types-16-sicp/source.tar.gz)
is still exactly the one from last week.

## Dear Dr. McCarthy

[John McCarthy][wiki-mccarthy] is one of my heroes. I never had the chance
to meet him. I wish I had. This is a small, rather peculiar thank-you
for an insight that still makes me happy to look at source code.

His 1960 paper, *Recursive Functions of Symbolic Expressions and Their
Computation by Machine, Part I*, brings symbolic expressions, recursive
function definitions, and a universal evaluation function into a remarkably
compact account. The evaluator accepts a representation of an expression
and an environment of bindings. The language can describe the work of
evaluating its own represented programs.[^mccarthy]

That doesn't make implementation effortless. It makes the relationship
between a program and a program that manipulates programs unusually
visible. After sixteen posts spent recreating a tiny piece of that feeling
in an inhospitable host, I remain thoroughly charmed.

The history is better than a perfectly polished origin myth. McCarthy's
later account describes an intended M-expression notation, the growing
use of S-expressions for programs, and Steve Russell recognizing that
`eval` could become an interpreter and hand-coding it. The parentheses
were not simply handed down as a completed aesthetic decree. They moved
in and became difficult to evict.

An ode to McCarthy should leave room for that collaboration and discovery.
The result is extraordinary without pretending it arrived fully formed
from one person, or that every feature of modern Lisp was in the first
paper. Affection doesn't need better history than the history we have.

For a later expression of that affection, watch William E. Byrd's
[“The Most Beautiful Program Ever Written”][byrd-talk], presented at
Papers We Love NYC in 2017. Byrd, a coauthor of
[*The Reasoned Schemer*][reasoned-schemer], explores a tiny
[metacircular evaluator][wiki-metacircular]: a Lisp interpreter written
in Lisp.[^byrd] The family resemblance to our three expression forms is
hard to miss. The difference is that he lets Lisp help, while I involved
Rust's human-resources department.

## Love is not a deployment strategy

There is no [Lisp machine][wiki-machines] on my desk. Dedicated commercial
Lisp workstations are historical machines now, not a mainstream hardware
platform I can specify for the next board revision. That is a loss of a
particular hardware-and-software world, not evidence that Lisp itself
requires that hardware to run.

Nor does every Lisp implementation execute through a bytecode
[virtual machine][wiki-vm]. SBCL, for example, has a native-code compiler.
It also has a runtime: compiling to machine instructions doesn't make
allocation, [garbage collection][wiki-gc], and other support machinery
disappear.[^runtime]

Those distinctions matter when choosing software for a constrained target.
What is the memory footprint? Can the relevant path allocate? What are
the timing guarantees? What must exist before the first function runs?
Can the toolchain support this processor, boot environment, and interrupt
model? A familiar runtime is not automatically a disqualification, and
native code is not automatically a hard-real-time guarantee.

I am still not putting SBCL in a tiny microcontroller's interrupt handler.
I love Lisp. This does not mean I am putting a garbage collector between
the interrupt controller and my afternoon.

But “Lisp cannot do systems software” would be a bad conclusion. Lisp
machines had Lisp operating systems, and projects such as **Mezzano**
provide a modern Common Lisp operating-system counterexample.[^systems]
“Not my default tool for this particular low-level job” is an engineering
choice. “Fundamentally incapable” is a claim requiring rather more than
my preference for a different compiler.

## No defense brief required

Lisp can be useful. This project was not. Keeping those facts separate
is the last bit of bookkeeping I will inflict on the reader.

I don't need every language I admire to be the one I would choose for
a device driver. I can value direct control over layout and timing in
one setting and adore a language that lays its program structure out
on the table in another. Appreciation is not a purchase order.

We spent sixteen weeks making the compiler find ten and one more explaining
why the parentheses make me smile. That seems an appropriately extravagant
way to say it.

Dear Lisp: thank you for the functions, the lists, and the invitation to
treat a language as something we can take apart and build with.

Dear Rust: sorry about all of that.

[wiki-lisp]: https://en.wikipedia.org/wiki/Lisp_(programming_language)
[wiki-cl]: https://en.wikipedia.org/wiki/Common_Lisp
[wiki-sexpr]: https://en.wikipedia.org/wiki/S-expression
[wiki-ast]: https://en.wikipedia.org/wiki/Abstract_syntax_tree
[wiki-homoiconicity]: https://en.wikipedia.org/wiki/Homoiconicity
[wiki-macros]: https://en.wikipedia.org/wiki/Macro_(computer_science)
[wiki-hygiene]: https://en.wikipedia.org/wiki/Hygienic_macro
[wiki-mccarthy]: https://en.wikipedia.org/wiki/John_McCarthy_(computer_scientist)
[wiki-machines]: https://en.wikipedia.org/wiki/Lisp_machine
[wiki-vm]: https://en.wikipedia.org/wiki/Virtual_machine
[wiki-gc]: https://en.wikipedia.org/wiki/Garbage_collection_(computer_science)
[wiki-abelson]: https://en.wikipedia.org/wiki/Hal_Abelson
[wiki-metacircular]: https://en.wikipedia.org/wiki/Meta-circular_evaluator
[byrd-talk]: https://paperswelove.org/videos/william-byrd-on-the-most-beautiful-program-ever-written-pwl-nyc/
[reasoned-schemer]: https://mitpress.mit.edu/9780262535519/the-reasoned-schemer/

[^reader]: The [Common Lisp reader algorithm](https://www.lispworks.com/documentation/HyperSpec/Body/02_b.htm)
    covers tokens, escapes, macro characters, and more. “Open parenthesis,
    descend; close parenthesis, resume the parent” describes the simple
    list example, not the complete reader. Reader macros are also distinct
    from the form-transforming `defmacro` macros discussed here.

[^macros]: See the HyperSpec entries for
    [`defmacro`](https://www.lispworks.com/documentation/HyperSpec/Body/m_defmac.htm)
    and [`macroexpand-1`](https://www.lispworks.com/documentation/HyperSpec/Body/f_mexp_.htm).
    Macro expansion produces a form for subsequent processing. The returned
    form may itself need expansion; the example here exposes one outer
    expansion rather than pretending to show a compiler's final AST.

[^abelson]: Hal Abelson, [SICP lecture 3A, “Henderson Escher Example”](https://ocw.mit.edu/courses/6-001-structure-and-interpretation-of-computer-programs-spring-2005/resources/3a-henderson-escher-example/),
    [transcript, page 14](https://ocw.mit.edu/courses/6-001-structure-and-interpretation-of-computer-programs-spring-2005/453216e53b2225221a756bf42bfdcd3d_PEwZL3H2oKg.pdf#page=14).
    The transcript renders the language's name as “List”; the quotation
    restores “Lisp.” His example embeds a picture language whose procedures
    can still use the host language's facilities.

[^our-shed]: Our own [part-13 `lisp!()` macro](/posts/lambda-types-13-macro/)
    relies on this freedom to build a private notation inside a host
    language. Rust gives the procedural macro structured token trees;
    it translates our S-expressions into `Lambda`, `App`, and `Var` types.
    The trait evaluator still performs the computation. Rust isn't thereby
    homoiconic in Lisp's sense, and `lisp!()` isn't a Common Lisp macro.
    The shared superpower is extending the surface notation by transforming
    structured syntax. In our case, the shed contains a lambda evaluator
    and appears to be obstructing the compiler's emergency exit.

[^mccarthy]: McCarthy's [original 1960 paper](https://www-formal.stanford.edu/jmc/recursive.html)
    remains worth reading. His later [account of Lisp's implementation](https://www-formal.stanford.edu/jmc/history/lisp/node3.html)
    discusses both Russell's interpreter and the M-expression plans.
    These are primary sources, not an attribution of every later Lisp
    implementation technique to one paper.

[^byrd]: The organizer's [talk page][byrd-talk] links the recording and
    confirms Byrd's book credit. This is a small evaluator, not a complete
    implementation of Scheme or Common Lisp. Its compactness relies on
    facilities already supplied by the host, including procedures and
    application. A short interpreter is not an entire language system
    compressed through superior parenthesis management.

[^runtime]: The [SBCL manual's introduction](https://www.sbcl.org/manual/index.html#Introduction)
    identifies its native compiler; [“Parts of SBCL”](https://www.sbcl.org/manual/index.html#Parts-of-SBCL)
    describes its runtime and core image. Other implementations make other
    choices. A runtime is not synonymous with a virtual machine, and neither
    term alone settles suitability for a particular systems task.

[^systems]: [Mezzano](https://github.com/froggey/Mezzano) describes itself as
    an operating system written in Common Lisp. Its documentation also warns
    that getting it running on a given machine can require work. This is an
    existence counterexample, not a recommendation to replace the firmware
    in the nearest appliance as a gesture of literary appreciation.
