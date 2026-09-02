+++
title = "Making Smaller Things"
date = 2026-09-02T09:00:00
draft = false
description = "Sandi Metz spent a RailsConf keynote telling a room full of Ruby developers to make smaller things. Rust gives you a second axis she did not have: you can make the types smaller too, and a smaller type deletes tests instead of adding them. A twenty-line temperature sensor driver, cut along Gary Bernhardt's boundary into a functional core that never touches a bus and an imperative shell with no logic left in it — and an honest count of how many states each version could reach."
[taxonomies]
tags = ["rust", "embedded", "design", "types", "testing", "drivers"]
[extra]
math = true
+++

Take a temperature sensor about as simple as a part can get. It hangs
off an I²C bus and has exactly two registers. Register `0x00` holds
the temperature result --- signed sixteen bits, one LSB equal to
1/128 of a degree Celsius. Register `0x01` holds the
configuration, three fields packed into sixteen bits: **averaging**,
how many samples the part means together before it reports one;
**conversion cycle**, how long it waits between conversions; and
**mode**, which is continuous, one-shot, or shut down.

That is the entire device. No alert pin, no threshold registers, no
calibration. For completeness, here's a tabular view of the device's
register map.

<svg class="regmap" viewBox="0 0 730 96" role="img" aria-labelledby="rm-t rm-d" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg">
  <title id="rm-t">Register map of the temperature sensor</title>
  <desc id="rm-d">Two 16-bit registers. Register 0x00 is the temperature result and uses all sixteen bits as one signed value, one LSB equal to 1/128 degree Celsius. Register 0x01 is the configuration: bits 15 to 12 reserved, bits 11 to 10 MODE, bits 9 to 7 CONV, bits 6 to 5 AVG, bits 4 to 0 reserved.</desc>
  <style>
    .regmap{width:100%;height:auto;max-width:44rem;display:block;margin:1.75rem 0;font-family:var(--sans)}
    .regmap .f{fill:var(--bg-dim);stroke:var(--border)}
    .regmap .r{fill:none;stroke:var(--border)}
    .regmap text{shape-rendering:auto}
    .regmap .b{fill:var(--fg-dim);font:11px var(--mono-code,ui-monospace,monospace)}
    .regmap .a{fill:var(--fg-alt);font:700 13px var(--mono-code,ui-monospace,monospace)}
    .regmap .n{fill:var(--fg-main);font-size:13px;font-weight:600}
    .regmap .s{fill:var(--fg-dim);font-size:10.5px;font-family:var(--mono-code,ui-monospace,monospace)}
    .regmap .d{fill:var(--fg-dim);font-size:12px;font-style:italic}
  </style>
  <text class="b" x="79" y="13" text-anchor="middle">15</text>
  <text class="b" x="121" y="13" text-anchor="middle">14</text>
  <text class="b" x="163" y="13" text-anchor="middle">13</text>
  <text class="b" x="205" y="13" text-anchor="middle">12</text>
  <text class="b" x="247" y="13" text-anchor="middle">11</text>
  <text class="b" x="289" y="13" text-anchor="middle">10</text>
  <text class="b" x="331" y="13" text-anchor="middle">9</text>
  <text class="b" x="373" y="13" text-anchor="middle">8</text>
  <text class="b" x="415" y="13" text-anchor="middle">7</text>
  <text class="b" x="457" y="13" text-anchor="middle">6</text>
  <text class="b" x="499" y="13" text-anchor="middle">5</text>
  <text class="b" x="541" y="13" text-anchor="middle">4</text>
  <text class="b" x="583" y="13" text-anchor="middle">3</text>
  <text class="b" x="625" y="13" text-anchor="middle">2</text>
  <text class="b" x="667" y="13" text-anchor="middle">1</text>
  <text class="b" x="709" y="13" text-anchor="middle">0</text>
  <text class="a" x="50" y="44" text-anchor="end">0x00</text>
  <rect class="f" x="58" y="20" width="672" height="38"/>
  <text class="n" x="394" y="37" text-anchor="middle">TEMPERATURE RESULT</text>
  <text class="s" x="394" y="51" text-anchor="middle">signed &#183; 1 LSB = 1/128 &#176;C</text>
  <text class="a" x="50" y="82" text-anchor="end">0x01</text>
  <rect class="r" x="58" y="58" width="168" height="38"/>
  <text class="d" x="142" y="82" text-anchor="middle">reserved</text>
  <rect class="f" x="226" y="58" width="84" height="38"/>
  <text class="n" x="268" y="75" text-anchor="middle">MODE</text>
  <text class="s" x="268" y="89" text-anchor="middle">11:10</text>
  <rect class="f" x="310" y="58" width="126" height="38"/>
  <text class="n" x="373" y="75" text-anchor="middle">CONV</text>
  <text class="s" x="373" y="89" text-anchor="middle">9:7</text>
  <rect class="f" x="436" y="58" width="84" height="38"/>
  <text class="n" x="478" y="75" text-anchor="middle">AVG</text>
  <text class="s" x="478" y="89" text-anchor="middle">6:5</text>
  <rect class="r" x="520" y="58" width="210" height="38"/>
  <text class="d" x="625" y="82" text-anchor="middle">reserved</text>
</svg>

The naive approach to configuring `MODE`, `CONV`, and `AVG` --- the
one I reach for first, and the one you should reach for first too ---
is an `async set_config()` taking those three fields as three `u8`
arguments. At the call site it reads like this:

```rust
sensor.set_config(2, 1, 0).await?;
```

I cannot tell you whether that is correct. Neither can you, and you
just finished reading the field list. Neither can the compiler, and
neither can the reviewer. To find out you have to go back to the
register map, count the bit fields, and check that the three
arguments are in the order this particular function happens to
expect. Swap any two of them and it still compiles. Swap any two of
them and it still runs. The part just quietly averages over the
wrong window for the rest of the product's life.

There are $16{,}777{,}216$ distinct values --- $256 \times 256 \times
256$ --- you could pass to that function. Ninety-six of them mean
anything at all.

<!-- more -->

## What Sandi actually said

In 2014 Sandi Metz gave a RailsConf keynote called [All the Little
Things][sandi]. The advice in the title is the whole argument:
**make smaller things**. Small classes, small methods, each one
doing a single job, each one nameable in a short phrase without the
word "and" in it. She spends the talk taking a lump of branching
procedural code and pulling small named objects out of it, one at a
time, until the branches are gone and what is left is a handful of
things you can hold in your head individually.

The talk is also where the line about duplication comes from ---
*duplication is far cheaper than the wrong abstraction* --- and I want
to come back to that at the end, because it is the part that gets
quoted least and matters most.

Here is what I noticed rereading it as a Rust programmer. In Ruby,
"smaller" has exactly one meaning: fewer lines, fewer
responsibilities, fewer reasons to change. The discipline is
entirely social. Ruby will happily let you pass a `String` where the
method wanted a `Time`, and the only thing standing between you and
that mistake is a test you remembered to write.

Rust has a second axis. A thing in Rust can be smaller in lines,
*or* it can be smaller in **inhabitants** --- the count of distinct
values its type can hold. And those two kinds of smaller behave
very differently when you go to test them. Making a function shorter
splits one test into two. Making a *type* smaller deletes tests
outright, because the states they were guarding against stop being
expressible.[^nb]

That is the whole post. The rest is one driver.

## The other half: where to put the small things

[Gary Bernhardt's "Functional Core, Imperative Shell"][gary] answers
the question Sandi's talk leaves open, which is *where the small
things go*.

His split: a **functional core** of pure functions --- values in,
values out, no I/O, many code paths, no dependencies --- wrapped in a
thin **imperative shell** that does all the talking to the outside
world and has almost no branches in it. The core is where the
thinking happens and is trivially testable. The shell is where the
hardware happens and needs almost no tests, because there is nothing
in it to be wrong.[^errata]

For embedded work this is not a stylistic preference. It is the
difference between tests that run in microseconds on your laptop and
tests that need a part on a bus.

There is a tension buried in that split, and it is better named here
than stumbled over later. Half of this post is going to tell you to
make the types smaller and sharper --- newtype the temperature,
enumerate the register field, push everything the datasheet knows
into something the compiler can check. The other half insists the
boundary stay dumb: bytes, plain integers, nothing clever. Those
sound like opposite instructions, and plenty of otherwise good
drivers have been wrecked by trying to satisfy both everywhere at
once.

They are not opposites. They are answers to different questions.
*What crosses the boundary* is bytes, because a bus has never heard
of `Celsius` and never will. *What you think in*, once you are past
the boundary, is the narrowest type that can still express the idea.
The edge is a translation, not a representation choice, and the
whole job of the shell is to perform that translation exactly once
in each direction. Everything the core knows, it knows because the
shell parsed it at the door.

That is the shape of the rest of this post. Bytes in, meaning in the
middle, bytes back out.

## The shameless green driver

The version everybody writes first, and should write first. Writing
it is not wasted effort and it is not a strawman: it proves the part
answers, and it proves you read the register map the way the vendor
meant it. Both are worth knowing before you design anything on top
of them.

What you should not do is *publish* it. This is the one place I part
company with Sandi, and the reason is context rather than principle.
Her advice lands in code you own end to end, where a refactor is an
edit and the only people you inconvenience are on your team. A
driver crate is not that. The moment `0.1.0` is on crates.io,
`set_config(u8, u8, u8)` stops being a sketch and becomes a
contract, and everything else in this post turns into a breaking
change --- paid for by however many people took you at your word.

So write the shameless version to learn the part, then throw the API
away before anyone can depend on it. How much of it survives is a
judgement call that scales with the part: two registers do not need
much design, while page-mode writes and a busy flag need rather
more. But `0.1.0` should already be the shape you are willing to
maintain.

```rust
const REG_TEMP: u8 = 0x00;
const REG_CONFIG: u8 = 0x01;

pub struct TempSensor<I2C> {
    i2c: I2C,
    addr: u8,
}

impl<I2C: I2c> TempSensor<I2C> {
    pub async fn read_temperature(&mut self) -> Result<f32, Error> {
        let mut buf = [0u8; 2];
        self.i2c.write_read(self.addr, &[REG_TEMP], &mut buf).await?;
        let raw = i16::from_be_bytes(buf);
        Ok(raw as f32 * 0.0078125)
    }

    pub async fn set_config(
        &mut self,
        avg: u8,
        conv: u8,
        mode: u8,
    ) -> Result<(), Error> {
        let word = ((avg as u16) << 5)
            | ((conv as u16) << 7)
            | ((mode as u16) << 10);
        self.i2c
            .write(self.addr, &[REG_CONFIG, (word >> 8) as u8, word as u8])
            .await
    }
}
```

Four things are wrong here, and none of them are bugs. The code
works. That is what makes them worth naming:

- `read_temperature` welds a bus transaction to a fixed-point
  conversion, so you cannot exercise the arithmetic without a mock.
- `set_config` takes three interchangeable `u8`s --- the call site
  carries no information at all.
- `f32` out. Which unit? Can it be `NaN`? Can it be -400 °C? The
  type says *any float*. The part says something far narrower, and
  we can write down exactly what.
- Every test of any of this needs a fake I²C bus.

## The first cut is Bernhardt's

Split the arithmetic from the transaction and stop. Even if you do
nothing else in this post, do this.

Start by writing the conversion down. The register is sixteen bits
of two's complement, one LSB is 1/128 of a degree, so the reported
temperature is

$$T = \frac{\mathrm{raw}}{128} = \mathrm{raw} \times 2^{-7},
\qquad \mathrm{raw} \in [-32768,\ 32767]$$

which gives a closed, and notably *lopsided*, range:

$$-256.0 \le T \le 255.9921875
\qquad \text{in steps of } 0.0078125$$

The top of the range is not 256. Two's complement has one more
negative code than positive, so the largest temperature the part can
report is $32767/128$, one LSB short. That asymmetry is a real fact
about the device. It is exactly the kind of thing a hand-written
unit test forgets, and `-> f32` does not carry one bit of it.

It is worth being precise about why `f32` is the wrong return type,
because the obvious reason is not the real one. The obvious reason
would be rounding, and it does not apply: the scale factor is a
power of two, so every value this part can report survives the trip
through `f32` exactly. Nothing is lost.

The real reason is that `f32` is *enormous*. It admits about four
billion distinct values, including `NaN`, both infinities, and
$-400\ \text{°C}$. The sensor admits 65,536, all of them real
temperatures, in a known range, at a known step. Returning `f32`
means choosing a type sixty-five thousand times larger than the
thing being described, and then relying on prose to explain which
values are impossible. That is the same mistake as
`set_config(u8, u8, u8)`, one register further down.

So the core keeps the raw units, and the scale stays in the type
rather than in a comment:

```rust
// core - pure, no bus, no async, no HAL, no float
fn decode_temperature(raw: [u8; 2]) -> Celsius {
    Celsius(i16::from_be_bytes(raw))
}
```

That is a small change with a disproportionate result. The
conversion is now a total function from `[u8; 2]` to `Celsius`: no
bus, no `async`, no HAL, no error case, and 65,536 possible inputs
that a `for` loop can walk in its entirety in under a millisecond.
There is no mock to write, because there is nothing to mock. And
`read_temperature` collapses into two lines that fetch bytes and
hand them over.

Dividing by 128 has not gone away, it has moved. It is a *rendering*
concern, and it belongs wherever a human finally reads the number
--- a log line, a display, a JSON field at the far edge of the shell.
That is also the honest place for `f32`, once the value is on its
way out of the system and nobody downstream will do arithmetic on
it:

```rust
impl Celsius {
    /// Exact: the scale is a power of two.
    pub fn to_degrees(self) -> f32 {
        f32::from(self.0) / 128.0
    }
}
```

The point was never that floats are bad. It is that they are the
wrong type to *think* in, and a perfectly good type to *print*.

## Making the types smaller

`Celsius` above was the first newtype. The three `u8`s get the same
treatment, and this is where the Rust half of the argument does work
that Ruby cannot: each enum is not just a clearer name for a number,
it is a smaller set of numbers.

```rust
pub struct Celsius(i16); // 1/128 °C, exactly as the part reports it

pub enum Averaging {
    X1,
    X8,
    X16,
    X32
}

pub enum ConversionCycle {
    Ms15,
    Ms125,
    Ms250,
    Ms500,
    Ms1000,
    Ms4000,
    Ms8000,
    Ms16000
}

pub enum Mode {
    Shutdown,
    OneShot,
    Continuous
}

pub struct Config {
    pub averaging: Averaging,
    pub cycle: ConversionCycle,
    pub mode: Mode,
}

impl Config {
    fn encode(self) -> [u8; 2] {
        let word = (self.averaging as u16) << 5
            | (self.cycle as u16) << 7
            | (self.mode as u16) << 10;
        word.to_be_bytes()
    }
}
```

The shifts survived, because the part still wants its bits where it
wants them. What did not survive is the masking, the casting, and
the range checking, and they did not survive because there is
nothing left to check --- `self.averaging as u16` cannot exceed 3,
and the compiler knows it even though no `if` says so.

Then read the call site out loud:

```rust
sensor.set_config(Config {
    averaging: Averaging::X8,
    cycle: ConversionCycle::Ms1000,
    mode: Mode::Continuous,
}).await?;
```

Compare that with `set_config(2, 1, 0)` from the top of the post.
The arguments cannot be swapped, because they are not positional any
more. They cannot be out of range, because there is no range. And a
reviewer can approve it without opening the datasheet, which is the
first time in this post that has been true of anything.

## Counting what you deleted

Everything above is arguable as taste. This part is arithmetic.

`(u8, u8, u8)` is $2^{24} = 16{,}777{,}216$ reachable states, of which
only 96 are legal. `(Averaging, ConversionCycle, Mode)` is $4 \times 8
\times 3 = 96$ reachable states, all of them legal. Same
functionality; the illegal region is gone, not guarded.

It is worth sitting with the size of that number for a second,
because the instinct is to say the guards handled it. They did not.
Three `if` statements do not test sixteen million states, they
*reject* them, and only if all three are written correctly and none
is ever deleted by someone tidying up a merge conflict. Those states
were never covered by tests. They were covered by *hope*. Making the
type smaller did not add safety --- it deleted the obligation.

And it deletes more than you aim at. Count what actually left the
codebase when the three `u8`s became three enums:

- Three range checks, and the branches through the function they
  created.
- Three `Error` variants, and every `match` arm downstream that had
  to handle them.
- The tests for those three variants, which someone had to write and
  everyone had to keep passing.
- The documentation sentence explaining that `avg` must be `0..=3`,
  and the risk that it drifts out of date.
- The question, at every call site, of whether the numbers are in
  the right order.

None of those were removed by hand. They stopped existing because
the thing they were protecting against stopped being expressible.
That is the compounding effect and it is the whole argument: in
Ruby, making things smaller adds objects you must then keep track
of; in Rust, making a *type* smaller subtracts work from places you
were not looking.

The reverse is also true, which is the part worth being nervous
about. Every `u8` in a public signature is an invitation to write
guards, error variants, tests, and prose that would not be needed if
the type were honest. The cost of a loose type is never paid at the
definition. It is paid everywhere the value goes afterwards.

## Primitives at the edges, meaning in the middle

I named this tension near the top; now there is code to point at, so
here is the resolution in full.

"Keep boundaries close to primitives" and "newtype everything" are
the same instruction viewed from different sides. Bytes arrive from
the bus as bytes. They are parsed **once**, at the edge, into a type
that cannot be wrong. The core then operates only on the parsed
form, and the shell renders back to bytes on the way out. All the
fallibility lives in that one parse --- which is Alexis King's [parse,
don't validate][parse], arriving from a different direction.

The word doing the work is *once*. A validating design checks a
value and hands back the same loose type it was given, so the next
function down has no way to know the check happened and defensively
checks again. That is how you end up with the same range test in
four places and a bug in the fifth, where somebody forgot. A parsing
design checks a value and hands back a *different type* --- one whose
existence is the evidence. `Config` is not a `(u8, u8, u8)` that has
been inspected; it is a thing that could not have been built from
bad numbers. Nothing downstream re-checks it because there is no
check left to perform.

Notice this makes the boundary the only interesting place in the
driver. Everything upstream of the parse is bytes, and bytes are the
part's problem. Everything downstream is `Config` and `Celsius`, and
those are the compiler's problem. The parse itself is fifteen lines,
pure, and gets the property tests, because it is the one place where
being wrong is possible.

For embedded work the payoff is concrete. The core has no HAL
dependency, no `async`, and no bus: it is a handful of pure
functions over arrays and enums, so it builds for the host as
readily as for the target. Tests run natively at full speed with a
real harness, a real allocator, and a real panic message, and the
`no_std` constraint never touches the part of the code where the
thinking is. The part on the bench is then needed only to answer the
question it is actually qualified to answer --- does the hardware do
what the datasheet claims --- rather than to re-litigate arithmetic
you could have checked on a laptop.

There is one honest caveat about where you draw the edge, which is
that it depends on where you are standing.[^scale] "The narrowest
type that can express the idea" gives a different answer when the
idea changes size. For this driver, the idea is *what this part
reported*, and `Celsius(i16)` in the part's own units is exactly
that. For a system reading a dozen sensors of five different kinds,
the idea is *a temperature*, and no single part's raw format
expresses it. The rule has not changed; the scope has. Each layer
parses into its own narrowest type, and each boundary between them
translates exactly once.

## What the tests look like now

Everything the core does is now a pure function over small domains,
which changes what a test is allowed to assert. None of what follows
needs a bus.

Start with the shape of the two directions, because they are not
symmetric and the asymmetry is the interesting part:

```rust
impl Config {
    fn encode(self) -> [u8; 2] { /* total */ }
    fn decode(raw: [u8; 2]) -> Result<Config, ConfigError> { /* partial */ }
}
```

`encode` is total. Every one of the 96 configurations you can
construct is a legal register value, so there is no failure to
report and no `Result` to unwrap. `decode` is not, and the reason is
worth chasing down: `Averaging` has four variants in a two-bit
field, and `ConversionCycle` has eight in a three-bit field, so both
are exhaustive --- every encoding a part could hand back names a
variant. `Mode` has three variants in a two-bit field. Encoding `3`
is reserved. It has no meaning, so `Mode` cannot represent it, so
`decode` has to be allowed to fail.

That is not a wart, it is the type system doing exactly the job we
hired it for. The reserved encoding is a fact about the silicon; the
`Result` is where that fact surfaces in the API, once, in the only
function that could ever encounter it. Everything downstream of the
parse gets a `Config` that cannot be reserved.

Which means the roundtrip property holds in one direction
unconditionally:

```rust
#[test]
fn every_config_roundtrips() {
    for c in Config::all() {
        assert_eq!(Config::decode(c.encode()), Ok(c));
    }
}
```

With only 96 inhabitants you do not have to settle for sampling at
all. `proptest` on its default budget draws 256 cases, which sounds
like plenty and is not --- covering all 96 by random draw takes
around 494 of them on average, so a green run proves less than it
appears to. Walking the domain is both cheaper and stronger. That is
what small types buy: not better sampling, but the option to stop
sampling. Ninety-six cases is proof by exhaustion that runs in
microseconds, and nobody has to say the word "proof".

The other direction is where `proptest` earns its place, because the
input is arbitrary bytes off a bus and the space is genuinely too
large to walk:

```rust
proptest! {
    #[test]
    fn decode_never_panics(raw: [u8; 2]) {
        // every one of 65,536 inputs is Ok or Err, never a panic
        let _ = Config::decode(raw);
    }

    #[test]
    fn decode_rejects_reserved_mode(raw: [u8; 2]) {
        let mode = (u16::from_be_bytes(raw) >> 10) & 0b11;
        prop_assert_eq!(Config::decode(raw).is_err(), mode == 3);
    }
}
```

The second one is the property I would not have thought to write as
a unit test. It says something stronger than "reserved mode fails":
it says reserved mode is the *only* thing that fails. Any future
edit that makes `decode` reject something else --- a stray range
check, a mask applied to the wrong field --- breaks it immediately.
Write that assertion once and it guards the boundary forever.

## What is left in the shell

Put the two versions of `set_config` next to each other. Not the
sketch from earlier --- the *honest* naive version, the one that
actually checks its arguments, because a published driver that
silently truncates a bad `u8` is not a driver anyone should use.

```rust
// before: three u8s, and every guard they oblige you to write
pub async fn set_config(
    &mut self,
    avg: u8,
    conv: u8,
    mode: u8,
) -> Result<(), Error> {
    if avg > 3 {
        return Err(Error::InvalidAveraging(avg));
    }
    if conv > 7 {
        return Err(Error::InvalidConversionCycle(conv));
    }
    if mode > 2 {
        return Err(Error::InvalidMode(mode));
    }

    let word = ((avg as u16) & 0x03) << 5
        | ((conv as u16) & 0x07) << 7
        | ((mode as u16) & 0x03) << 10;

    self.i2c
        .write(self.addr, &[REG_CONFIG, (word >> 8) as u8, word as u8])
        .await
}
```

```rust
// after
pub async fn set_config(&mut self, config: Config) -> Result<(), Error> {
    let [hi, lo] = config.encode();
    self.i2c.write(self.addr, &[REG_CONFIG, hi, lo]).await
}
```

Squint at those two and you do not need me to narrate it. But a few
things are worth saying out loud, because they are not only about
length.

The guards are the tests you were going to have to write, moved
inside the function. Three `if`s, three error variants, three
branches through a method whose actual job is one bus write. And
they are load-bearing: delete them and `mode = 3` writes a reserved
encoding to the part. The naive version is not shorter than the
refactored one because it is simpler. It was only ever shorter
because it was wrong.

Notice the masks, too. `& 0x03` after `avg > 3` has already been
checked is redundant --- and yet removing it makes the correctness of
the shift depend on a guard fifteen lines up. Keep it and it is
noise; drop it and it is a trap. That dilemma exists only because
the value arrived as a `u8` that could have been anything.

Both functions return `Result<(), Error>`, and the signatures are
lying about different things. On the *before* version, `Err` means
*either* you passed nonsense *or* the bus failed, and the caller has
to distinguish. On the *refactored* version there is no nonsense to
pass, so `Err` means one kind of thing: the transaction did not
complete. The type got smaller without the signature changing at
all.

And the three variants the shell no longer needs ---
`InvalidAveraging`, `InvalidConversionCycle`, `InvalidMode` --- come
out of the crate's `Error` enum entirely. Making the argument types
smaller made the error type smaller. That is the thing about
encoding knowledge in types that does not show up until you go
looking: the deletions compound.

The shell has no logic; the core has no dependencies.

## The failure mode

Everything so far has argued in one direction, so here is the
correction, because this technique has a failure mode and it is not
a rare one.

Rust makes over-encoding genuinely tempting: typestate on everything,
const generics for register widths, a newtype per field, a sealed
trait per axis. That code is not safer, it is just harder, and it is
exactly the wrong abstraction Sandi warned about --- only now the
compiler enforces it, so it is more expensive to back out of than a
bad Ruby class.

The heuristic I have landed on: encode the invariant a caller could
plausibly get wrong *at a call site*. Newtype the thing that crosses
a boundary. Leave the loop counter alone.

Which is where the two halves of this post finally meet, because
they have been pulling in opposite directions the whole way. Design
before you publish, said the shameless green section. Do not
over-design, says this one. Both are true, and the thing that
reconciles them is not a balance point --- it is publication. A wrong
abstraction you have not released is a cheap experiment; you delete
it and nobody finds out. The same abstraction behind a version
number is a promise, and now removing it costs somebody else a
migration. So tolerate duplication for as long as the shape is
genuinely unclear, and spend the design budget on exactly those
invariants you are willing to still be defending at `1.0`.

Make smaller things. In Rust, make sure some of them are types.

[^nb]: No Boilerplate's [Rust Data Modelling Without
Classes](https://www.youtube.com/watch?v=z-0-bbc80JM) is the same
observation aimed at a different target. Where Sandi decomposes
behaviour into objects, that video decomposes *data* into sum and
product types, and lands on the phrase this section is circling ---
make illegal states unrepresentable. Worth watching immediately
after the Metz talk, because the two together are the argument this
post is making: the Ruby half tells you to cut things up, the Rust
half tells you the type system will hold the pieces apart for you.

[^errata]: Nothing in the *software* to be wrong, is what I mean.
The hardware is another matter. Parts have errata: a register reads
back a value it never accepted, a bus wedges on a zero-length write,
a conversion is quietly wrong for one combination of settings that
the datasheet says is legal. When that happens the workaround lands
in the shell, because the shell is the only layer that knows a
particular piece of silicon exists --- and at that moment the shell
does have logic in it, does need tests, and they are the expensive
kind that need the part on the bench. Keeping those workarounds
quarantined, attributed to a specific erratum, and deletable once
the next die revision ships is a whole post of its own, and not this
one.

[^scale]: Taking this into the real world complicates the tidy
answer, and it is worth saying so. A thermal monitoring system that
has to be generic over its sensors cannot speak in any one part's
raw units: a part reporting in 1/128 °C, another in 1/16 °C, a
thermistor read through an ADC, and an RTD needing a resistance
curve have nothing in common at the register level. At *that*
boundary `f32` degrees is a defensible choice, and possibly the best
one --- it is the only representation all of them can reach without
the aggregator knowing which part produced the reading, and it is
the type a human thinks in when the question is "how hot is rack
three." A fixed-point millidegree `i32` is the other serious
candidate if you want to stay off floats and can accept a fixed
resolution for every part. What does not change is the shape:
`Celsius(i16)` is right for this driver because it is what this part
means, `f32` or millidegrees may be right one layer up because that
is what the *fleet* means, and each of those is a boundary that
parses once rather than a format smeared through the whole system.
The mistake would be letting the aggregator's convenient type reach
back down and become the driver's return type, so that every part
converts to degrees before anyone has decided what precision the
system needs.

[sandi]: https://www.youtube.com/watch?v=8bZh5LMaSmE
[gary]: https://www.youtube.com/watch?v=yTkzNHF6rMs
[parse]: https://lexi-lambda.github.io/blog/2019/11/05/parse-don-t-validate/
