+++
title = "Thirty-Six Registers, Described Twelve Times"
date = 2026-09-16T09:00:00
draft = false
description = "A follow-up to Making Smaller Things, against a part that fights back. The first driver had two registers and one newtype. The second has four channels, four alert slots, sixteen address strappings, and a calibration equation — which means three different small integers that must never be transposed, a threshold whose meaning moves when you recalibrate, and a shell that had to grow a cache. What survived the scale-up, what bent, and why you stop being able to count the states."
[taxonomies]
tags = ["rust", "embedded", "design", "types", "testing", "drivers", "tmp108", "ina4230"]
[extra]
math = true
+++

```rust
AddressPins { a0: AddrPinState::Gnd, a1: AddrPinState::Sda }
```

That selects `0x48`. Swap the two fields and you get `0x42`. Both are addresses
an [INA4230][ina] will answer to, given the right strapping. Both fields have
the same type, so the compiler has nothing to say about it, and a reviewer
reading the line has nothing to check it against except [the datasheet][ds]. Get
it wrong and the part does not complain. It simply never acknowledges, and you
spend the afternoon looking at your pull-ups.

That is the argument from [the last post][msm], arriving again. What is new is
that this part offers three separate opportunities to make it --- channels,
alert slots, and address pins --- and I only caught the second one because the
first had already given me trouble.

<!-- more -->

## The first driver

[Making Smaller Things][msm] argued its case against a fictitious part with two
registers and one quantity. That is the easy case --- designed like that for the
sole purpose of making the argument easy to follow ---, and a reader is entitled
to ask whether any of it survives contact with a part that fights back. A part
available to order from your favorite parts distributor.

The first answer arrived two days after that post went up.  [`tmp108`][tmp] is a
driver I have been maintaining since 2024. It's currently shipping on real
products users can pay real money. On 4 September its temperature API stopped
being `f32` and started being `Celsius`.

The count is the same shape as the one in the last post. The TMP108 is a
twelve-bit part with a 1/16 °C LSB: **4,096** representable temperatures, evenly
spaced between −128.0 °C and +127.9375 °C. `f32` admits roughly **4.3 billion**,
including `NaN`, both infinities, and −400 °C.

But the newtype is not the interesting part. The interesting part is what
writing it down *found*.

Making the decode total surfaced two bugs in a driver that worked, had users,
and had been on crates.io for a year.

The first is small. Encoding a temperature truncated toward zero where it should
have rounded half away from zero, so a value landing exactly between two codes
went to the wrong one. Nobody would ever notice, and it is wrong.

The second is not small. Decoding scaled by 1/256 instead of arithmetic-shifting
right by four. Those are the same operation only if the low nibble is zero ---
and the low nibble is reserved, which means the part is entitled to put anything
there and you are obliged to discard it. Scaling folds it in instead.

Chase that and it leads somewhere uncomfortable. The datasheet gives the
power-on value of `T_HIGH` as `0x7FF8`. That word has a stray bit 3 set, inside
the reserved nibble. The old decode folded it in and produced 127.96875 °C.

The TMP108 cannot report 127.96875 °C. Its maximum is 127.9375 °C, which is
$2047/16$, one LSB below 128 --- the same lopsided ceiling the last post pointed
at in a different part. The driver had been decoding the datasheet's own stated
reset value into a temperature the silicon is incapable of producing, and nobody
noticed for a year, because `f32` will hold anything you hand it and never ask
why.

Writing down the type caught the datasheet.

So the technique works. But TMP108 is four registers and one quantity, and all
of that is still the easy case. The rest of this post is about a part that is
not.

## Three small integers that must never be transposed

The INA4230 is a 48 V quad-channel current, voltage, power and energy
monitor. Four independent shunts, each with its own resistor, its own current
scale, and its own calibration. Thirty-six addressable registers. Four alert
slots. Sixteen possible I²C addresses. A calibration equation with a decimal
constant in it.

It also hands you three separate indices, and all three of them are the integers
0, 1, 2 and 3.

`Channel` says which shunt. `AlertSlot` says which of the four
comparators. `AddrPinState` says what a strap pin is tied to, and there are two
such pins, so sixteen addresses. Three meanings, one set of numbers, and a bus
that cannot tell them apart.

Table 6-1 of [the datasheet][ds] gives the address encoding, and it is perfectly
regular. Every table, equation and section number in this post refers to that
document --- SBOSAD4, June 2024:

$$\mathrm{addr} = \mathtt{0x40} \mathrel{|} (A_1 \ll 2) \mathrel{|} A_0
\qquad \mathrm{GND}=0,\ \mathrm{V_S}=1,\ \mathrm{SDA}=2,\ \mathrm{SCL}=3$$

Regular, and not symmetric. `A1 = SDA, A0 = GND` is `0x48`. Reverse them and it
is `0x42`. So `AddressPins` takes named fields rather than two positional
arguments, the table in the crate documentation is transcribed with A1 in the
first column because that is the order the datasheet uses, and there is a test
whose entire job is to guard the swap:

```rust
#[test]
fn address_pin_order_is_not_symmetric() { /* ... */ }
```

Two companions sit next to it. `address_is_injective` says no two strappings
collide, and `address_covers_exactly_the_reachable_range` says the sixteen of
them land on sixteen consecutive addresses with no gaps and nothing outside
`0x40..=0x4F`. Sixteen cases. The test walks all of them, which is the entire
domain, which means those two are not samples of a property but statements of
fact.

I wrote the first of the three in the same change that introduced the type. It
felt slightly paranoid at the time.

Nine days later the alert registers came up for implementation, and
the paranoia paid.

`ALERT_LIMIT` sits at `0x06`, `0x0E`, `0x16` and `0x1E`. `ALERT_CONFIG` sits at
`0x07`, `0x0F`, `0x17` and `0x1F`. Stride eight --- which is also the stride of
the per-channel measurement bank. So the register description had them nested
inside the channel block, and the generated accessor was reached as:

```rust
channel_regs(channel).alert_config()
```

Every register address that call produces is correct. The arithmetic is right,
the tests pass, the part answers appropriately.

And it is *wrong*, because Table 7-20 describes each limit flag as "independent
of channel", and Table 7-8 gives `ALERT_CONFIG` its own `CHANNEL` field in bits
4:3. **Alert slot 2 can watch channel 4.**  The index was never a channel. It
was a slot that happened to be spelled with the same four numbers, at the same
stride, inside the same address space.

From the design note for that change:

> That names an alert slot a channel and invites exactly the transposition class
> of bug already fixed once in this driver for the I2C address table.

The fix moves nothing. `alert-regs[alert-slot stride 8]`, same base addresses,
same stride, byte-identical register access. It is purely a change to what the
index is *called*, and therefore to what the type system will let you put there.

This is the last post's lesson with a harder edge on it. That post said three
interchangeable `u8`s carry no information at the call site. This says something
worse: two small integers with the same range and the same stride are not
*necessarily* the same index, and **the register map cannot tell you which**,
because the address arithmetic comes out identical either way. The only thing
that distinguishes them is a sentence of prose in a different table.

The guard, in the end, looks a lot like the first one:

```rust
#[tokio::test]
async fn limit_alert_is_indexed_by_slot() { /* ... */ }
```

which the design note calls, accurately, "the
`address_pin_order_is_not_symmetric` of this feature."

## The smallest thing was not the code

Sandi Metz's advice is about programs. The most valuable place I applied it in
this driver was not the program.

The four per-channel register banks are laid out contiguously with an eight-byte
stride (Table 7-1). So thirty-two hand-written register definitions were, in
fact, one bank written out four times. Saying so in the register description
language collapses it:

```
block channel-regs[channel stride 8] {
    register shunt-voltage { address: 0x00, /* ... */ }
    register bus-voltage   { address: 0x01, /* ... */ }
    register current       { address: 0x02, /* ... */ }
    register power         { address: 0x03, /* ... */ }
    register energy        { address: 0x04, /* ... */ }
    register calibration   { address: 0x05, /* ... */ }
}
```

Six definitions, indexed four ways. The addresses written down are the channel-1
addresses; the other twenty-four are arithmetic.

|                                         | before            | after    |
|-----------------------------------------|-------------------|----------|
| `INA4230.ddsl`                          | 512 lines         | 285      |
| generated `src/device.rs`               | 5,632 lines       | 2,540    |
| per-channel `match` blocks in the shell | 5 blocks, 20 arms | one call |

The last row is the one that shows up while you are working. Reading a
measurement used to mean selecting a register by channel, which meant a five-arm
`match`, five times over, once per quantity. All twenty arms were correct. All
twenty were also transcription, and transcription is where the third copy goes
wrong.

<svg class="bankmap" viewBox="0 0 700 268" role="img" aria-labelledby="bm-t bm-d" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg">
  <title id="bm-t">The INA4230 register map as a repeated bank</title>
  <desc id="bm-d">An eight-column by four-row grid. Columns are byte offsets plus zero through plus seven within a stride of eight; rows are the base addresses 0x00, 0x08, 0x10 and 0x18. Offsets plus zero through plus five hold the per-channel registers SHUNT, BUS, CURRENT, POWER, ENERGY and CALIBRATION, described once as a block indexed by channel. Offsets plus six and plus seven hold ALERT_LIMIT and ALERT_CONFIG, described once as a separate block indexed by alert slot. The row index therefore means a channel for the first six cells and an alert slot for the last two. Below the grid sit the four global registers: CONFIG1 at 0x20, CONFIG2 at 0x21, FLAGS at 0x22 and MANUFACTURER_ID at 0x7E. Thirty-six addressable registers in total, described by twelve definitions.</desc>
  <style>
    .bankmap{width:100%;height:auto;max-width:44rem;display:block;margin:1.75rem 0;font-family:var(--sans)}
    .bankmap .c{fill:var(--bg-dim);stroke:var(--border)}
    .bankmap .a{fill:none;stroke:var(--fg-alt);stroke-width:1.5}
    .bankmap text{shape-rendering:auto}
    .bankmap .n{fill:var(--fg-main);font-size:11px;font-weight:600}
    .bankmap .s{fill:var(--fg-dim);font-size:10px;font-family:var(--mono-code,ui-monospace,monospace)}
    .bankmap .k{fill:var(--fg-alt);font:700 11.5px var(--mono-code,ui-monospace,monospace)}
    .bankmap .d{fill:var(--fg-dim);font-size:11px;font-style:italic}
    .bankmap .br{fill:none;stroke:var(--fg-alt);stroke-width:1.2}
  </style>
  <text class="k" x="260" y="11" text-anchor="middle">channel-regs[channel stride 8]</text>
  <path class="br" d="M50 16 v6 H470 v-6"/>
  <text class="k" x="540" y="11" text-anchor="middle">alert-regs[alert-slot stride 8]</text>
  <path class="br" d="M470 16 v6 H610 v-6"/>
  <text class="s" x="85" y="40" text-anchor="middle">+0</text>
  <text class="s" x="155" y="40" text-anchor="middle">+1</text>
  <text class="s" x="225" y="40" text-anchor="middle">+2</text>
  <text class="s" x="295" y="40" text-anchor="middle">+3</text>
  <text class="s" x="365" y="40" text-anchor="middle">+4</text>
  <text class="s" x="435" y="40" text-anchor="middle">+5</text>
  <text class="s" x="505" y="40" text-anchor="middle">+6</text>
  <text class="s" x="575" y="40" text-anchor="middle">+7</text>
  <text class="s" x="44" y="66" text-anchor="end">0x00</text>
  <rect class="c" x="50" y="48" width="70" height="26"/><text class="n" x="85" y="65" text-anchor="middle">SHUNT</text>
  <rect class="c" x="120" y="48" width="70" height="26"/><text class="n" x="155" y="65" text-anchor="middle">BUS</text>
  <rect class="c" x="190" y="48" width="70" height="26"/><text class="n" x="225" y="65" text-anchor="middle">CURR</text>
  <rect class="c" x="260" y="48" width="70" height="26"/><text class="n" x="295" y="65" text-anchor="middle">PWR</text>
  <rect class="c" x="330" y="48" width="70" height="26"/><text class="n" x="365" y="65" text-anchor="middle">NRG</text>
  <rect class="c" x="400" y="48" width="70" height="26"/><text class="n" x="435" y="65" text-anchor="middle">CAL</text>
  <rect class="a" x="470" y="48" width="70" height="26"/><text class="n" x="505" y="65" text-anchor="middle">LIMIT</text>
  <rect class="a" x="540" y="48" width="70" height="26"/><text class="n" x="575" y="65" text-anchor="middle">CFG</text>
  <text class="k" x="620" y="66">1</text>
  <text class="s" x="44" y="94" text-anchor="end">0x08</text>
  <rect class="c" x="50" y="76" width="70" height="26"/><text class="n" x="85" y="93" text-anchor="middle">SHUNT</text>
  <rect class="c" x="120" y="76" width="70" height="26"/><text class="n" x="155" y="93" text-anchor="middle">BUS</text>
  <rect class="c" x="190" y="76" width="70" height="26"/><text class="n" x="225" y="93" text-anchor="middle">CURR</text>
  <rect class="c" x="260" y="76" width="70" height="26"/><text class="n" x="295" y="93" text-anchor="middle">PWR</text>
  <rect class="c" x="330" y="76" width="70" height="26"/><text class="n" x="365" y="93" text-anchor="middle">NRG</text>
  <rect class="c" x="400" y="76" width="70" height="26"/><text class="n" x="435" y="93" text-anchor="middle">CAL</text>
  <rect class="a" x="470" y="76" width="70" height="26"/><text class="n" x="505" y="93" text-anchor="middle">LIMIT</text>
  <rect class="a" x="540" y="76" width="70" height="26"/><text class="n" x="575" y="93" text-anchor="middle">CFG</text>
  <text class="k" x="620" y="94">2</text>
  <text class="s" x="44" y="122" text-anchor="end">0x10</text>
  <rect class="c" x="50" y="104" width="70" height="26"/><text class="n" x="85" y="121" text-anchor="middle">SHUNT</text>
  <rect class="c" x="120" y="104" width="70" height="26"/><text class="n" x="155" y="121" text-anchor="middle">BUS</text>
  <rect class="c" x="190" y="104" width="70" height="26"/><text class="n" x="225" y="121" text-anchor="middle">CURR</text>
  <rect class="c" x="260" y="104" width="70" height="26"/><text class="n" x="295" y="121" text-anchor="middle">PWR</text>
  <rect class="c" x="330" y="104" width="70" height="26"/><text class="n" x="365" y="121" text-anchor="middle">NRG</text>
  <rect class="c" x="400" y="104" width="70" height="26"/><text class="n" x="435" y="121" text-anchor="middle">CAL</text>
  <rect class="a" x="470" y="104" width="70" height="26"/><text class="n" x="505" y="121" text-anchor="middle">LIMIT</text>
  <rect class="a" x="540" y="104" width="70" height="26"/><text class="n" x="575" y="121" text-anchor="middle">CFG</text>
  <text class="k" x="620" y="122">3</text>
  <text class="s" x="44" y="150" text-anchor="end">0x18</text>
  <rect class="c" x="50" y="132" width="70" height="26"/><text class="n" x="85" y="149" text-anchor="middle">SHUNT</text>
  <rect class="c" x="120" y="132" width="70" height="26"/><text class="n" x="155" y="149" text-anchor="middle">BUS</text>
  <rect class="c" x="190" y="132" width="70" height="26"/><text class="n" x="225" y="149" text-anchor="middle">CURR</text>
  <rect class="c" x="260" y="132" width="70" height="26"/><text class="n" x="295" y="149" text-anchor="middle">PWR</text>
  <rect class="c" x="330" y="132" width="70" height="26"/><text class="n" x="365" y="149" text-anchor="middle">NRG</text>
  <rect class="c" x="400" y="132" width="70" height="26"/><text class="n" x="435" y="149" text-anchor="middle">CAL</text>
  <rect class="a" x="470" y="132" width="70" height="26"/><text class="n" x="505" y="149" text-anchor="middle">LIMIT</text>
  <rect class="a" x="540" y="132" width="70" height="26"/><text class="n" x="575" y="149" text-anchor="middle">CFG</text>
  <text class="k" x="620" y="150">4</text>
  <text class="d" x="50" y="176">the same row index is a channel on the left and a slot on the right</text>
  <rect class="c" x="50" y="196" width="100" height="26"/><text class="n" x="100" y="213" text-anchor="middle">CONFIG1</text>
  <text class="s" x="100" y="234" text-anchor="middle">0x20</text>
  <rect class="c" x="158" y="196" width="100" height="26"/><text class="n" x="208" y="213" text-anchor="middle">CONFIG2</text>
  <text class="s" x="208" y="234" text-anchor="middle">0x21</text>
  <rect class="c" x="266" y="196" width="100" height="26"/><text class="n" x="316" y="213" text-anchor="middle">FLAGS</text>
  <text class="s" x="316" y="234" text-anchor="middle">0x22</text>
  <rect class="c" x="374" y="196" width="100" height="26"/><text class="n" x="424" y="213" text-anchor="middle">MFR_ID</text>
  <text class="s" x="424" y="234" text-anchor="middle">0x7E</text>
  <text class="k" x="610" y="258" text-anchor="end">36 registers &#183; 12 definitions</text>
</svg>

Two smaller moves rode along in the same change, and both are deletions achieved
by declaration rather than by editing.

Marking the two's-complement fields as signed made the generator emit `i16`
directly. That removed two `as i16` casts and the two
`#[allow(clippy::cast_possible_wrap)]` attributes that had been apologising for
them. And making the reserved `ALERT_MASK` encodings 6 and 7 *fallible* rather
than naming them means a reserved encoding never acquires a name --- the last
post's argument about `Mode` and its reserved `3`, arriving one layer down, at
the generator.

The best evidence that the duplication had already been charging rent:
collapsing the four banks caused `ALERT_CONFIG` on channels 2, 3 and 4 to pick
up enumerations that until then only channel 1 had. Three of the four copies had
quietly drifted. Nobody noticed, because they were copies, and nobody reads the
third copy.

One thing worth saying, because "regenerate and see if it compiles" is not a
verification strategy. The mock tests that already existed assert literal
addresses --- `0x01`, `0x09`, `0x11`, `0x19` for bus voltage; `0x05`, `0x0D`,
`0x15`, `0x1D` for calibration --- and they still passed. That checks the
block's address arithmetic against the datasheet rather than against the
generator that produced it.

A code generator moves where "smaller" pays. In hand-written code, deleting
three thousand lines is a refactor somebody has to review.  Here it was a
consequence of writing down, once, that the part repeats itself.[^ddsl]

## Pick the unit that makes the arithmetic exact

The last post had one quantity and one newtype: `Celsius(i16)`, holding the
part's own units, with the division by 128 pushed out to a rendering
method. This part has five quantities, each with a different LSB, and the choice
of unit stops being a matter of taste.

| Type           | Unit | Why that one                                 |
|----------------|------|----------------------------------------------|
| `ShuntVoltage` | nV   | both LSBs, 2500 and 625, are whole nanovolts |
| `BusVoltage`   | µV   | the LSB is 1600                              |
| `Current`      | nA   | derives from `CURRENT_LSB`                   |
| `Power`        | nW   | `32 × CURRENT_LSB`                           |
| `Energy`       | nJ   | `32 × CURRENT_LSB`, accumulated              |

The rule that picks each row is the same: use whichever unit makes the
conversion from the raw register *exact*. Not approximately exact. Exact, in
integers, with no rounding step to reason about.

Which brings up the constant. Datasheet Equation 1 computes the calibration
register, and it has `0.00512` in it. A decimal constant in a datasheet equation
reads like a demand for floating point, and I took it that way for about a day.

It is not. That constant is a unit artifact --- it is what the coefficient looks
like when you write the equation in amps and ohms.  Write it in nanoamps and
microohms instead and it becomes $5.12 \times 10^{12}$, which is an integer, and
the whole calculation follows it:

```rust
SHUNT_CAL = 5_120_000_000_000 / (lsb_na * shunt_uohm * divisor)
```

No floats. Rounds to nearest rather than truncating. And two more things fall
out of the types around it.

`CurrentLsb` and `ShuntResistance` are `NonZeroU32`, so that division cannot
divide by zero, and the compiler knows it without an `if` saying so --- the same
move as the last post's observation that `self.averaging as u16` cannot exceed
3.

And a `SHUNT_CAL` of zero is rejected outright rather than clamped, because a
device with a zero calibration register reports zero current indefinitely
(§8.1.2). An out-of-range calibration returns `ShuntCalOverflow` or
`ShuntCalUnderflow`. It does not quietly pick the nearest legal value and let
you ship it.

`f32` survives in exactly the place it survived last time: the `to_*` methods,
at the point where a number is about to be read by a human.

There is one wrinkle the last post did not have to deal with, and it is worth
saying it explicitly because it is the kind of thing that silently ruins a
measurement. Each type offers a base-unit accessor --- `as_nanovolts`,
`as_nanoamps`, `as_nanowatts` --- which is exactly the stored value and
therefore lossless. It also offers coarser ones, `as_microamps` and friends,
because most callers do not want to think in nanoamps.

Those divide. Dividing integers truncates. So `as_microamps` is a lossy accessor
on a type whose entire justification is that it is not lossy, and the only
defence available is to say so in the documentation and put the lossless ones
first. I would rather have found a type-level answer to that. I did not, and
pretending otherwise would undercut everything above: a newtype narrows what a
value can be, and it does not stop you from writing a method that throws part of
it away.

## A small type that still means the wrong thing

Here is the one I did not see coming, and it is the reason this post exists
rather than being a footnote on the last one.

`Celsius(i16)` is self-describing. Twenty-four and a quarter degrees is
twenty-four and a quarter degrees, and it will still mean that tomorrow, on a
different part, in a different program.  `ShuntVoltage` is the same: forty
million nanovolts is forty millivolts, permanently.

An alert *threshold* is not like that. It is a count in `ALERT_LIMIT`, and what
the count means comes from the target channel's `AdcRange` and `CurrentLsb` ---
state that lives in a different register and can change after the threshold is
armed.

The typing still does real work. Each variant carries its threshold in the unit
that variant implies:

```rust
pub enum Alert {
    ShuntOver(ShuntVoltage),
    ShuntUnder(ShuntVoltage),
    BusOver(BusVoltage),
    BusUnder(BusVoltage),
    PowerOver(Power),
}
```

so a bus threshold cannot be paired with a shunt comparison. There is no way to
spell it. That was the whole reason alerts had been deferred: the limit register
reinterprets its format according to the function it is paired with, and a `u16`
setter would have made the mismatch a runtime error instead of an
unrepresentable one.

But the types are not sufficient, and three consequences follow.

**Encoding became the fallible direction.** The last post had `encode` total and
`decode` partial, because a reserved `Mode` encoding could come back off the
bus. Here it inverts. Decoding a measurement is total --- every bit pattern the
part produces is a real quantity. Encoding a *threshold* can fail, because a
caller can ask for one the register cannot hold. The module that had been
documented as "pure and total" had to give up half that claim, and the note
recording why is the most useful sentence in the change:

> Totality was a consequence of every function being a decode, not a goal.

**Failure happens before the bus does.** A shunt or power threshold on a channel
that has never been calibrated cannot be encoded at all, so it returns
`NotCalibrated` without issuing a single transaction:
`shunt_and_power_alerts_on_an_uncalibrated_channel_do_not_touch_the_bus`.

**Recalibration silently rescales anything already armed.** The two ADC ranges
differ by a factor of four. Arm a threshold under `Range0`, recalibrate to
`Range1`, and the device is now enforcing a number that means something else ---
quietly, correctly, and not what anybody asked for. Nothing in the silicon
couples them.

So `calibrate` disarms the affected slots, and the ordering carries the weight:
disarm *before* writing `CONFIG2.RANGE`, because between a range change and a
later disarm there is a window in which the part enforces the old threshold
against the new scale. Bus thresholds are absolutely scaled and are left
alone. And if a disarm write fails, the operation returns before `RANGE` is
touched at all, so a slot that is still armed is still armed against the scale
it was programmed for:

```rust
#[tokio::test]
async fn a_failed_disarm_aborts_before_the_range_is_rewritten() { /* ... */ }
```

Which forces a correction to the last post. "The value is parsed once at the
edge, and nothing downstream re-checks it" is true of values.  It is not true of
values whose *scale* is mutable state somewhere else in the system. For those,
making the type smaller does not help at all. What helps is making the operation
that moves the scale responsible for everything that depended on it --- which is
not a type-system property. It is an ordering property, and you get it by
writing it down and testing it.

One more asymmetry falls out, and I like it because it is a cache that lies in a
useful direction. Thresholds round to the nearest LSB on the way into the
register, so the count the device holds is usually not the number you asked
for. Reading a slot back through `Ina4230::alert` returns the value you
supplied, not the value the part is enforcing --- because the driver kept your
version and never asks the device.

That is arguably wrong. It is also the only answer that lets a caller compare
what they configured against what they meant, rather than against a rounded
count they would have to un-scale by hand. The honest version is to document
which of the two you are getting, which the crate does. There was no answer here
that was true in both directions at once, and noticing that is most of the work.

## The shell grew state

The last post ended a section with a line I liked well enough to repeat here:
*the shell has no logic; the core has no dependencies.*

Exactly half of that survived.

The core half held completely. `units` and `convert` have no bus, no `async`, no
HAL, and no floating point. They build for the host as readily as for the
target, and every test worth running runs against them in milliseconds.

The shell did not stay dumb. It carries two caches --- `[Option<Calibration>;
4]` and `[Option<(Channel, Alert)>; 4]` --- and both are required, not only
convenient.

Calibration is needed to scale every measurement the part produces, and a bus
round-trip per reading is the wrong price. The alert cache is required for a
subtler reason: invalidation has to know which slots point at the channel being
recalibrated and which of those are shunt or power alerts. Without the cache,
that question costs four register reads on every `calibrate` call --- on a path
that is otherwise two writes. That is, this is optimization trick because the
real world is, well, complex.

What fell out of arguing about it is a rule I plan to keep for the foreseeable
future:

> Cache only what another operation needs in order to be correct.

That is why `AlertPinConfig` --- polarity, latching, and the two mask bits ---
is deliberately *not* cached. It is never needed to interpret a reading, so a
cache would buy nothing, and it would inherit a coherence problem for free.

Because the coherence problem is real, and the honest thing is to write it down
rather than hope. A cache is a claim about a device you do not control. This one
records only the driver's own successful writes. It cannot see a power cycle, an
EN-pin toggle, a General Call reset, or a second controller on the bus doing
whatever it likes.

State was not the only thing that crept in. `CONFIG2` holds the ADC range in
bits 3:0, the alert-pin behaviour in bits 7:4, and the energy accumulator resets
in bits 11:8 --- three unrelated concerns that three different methods each need
to touch without disturbing the other two. So every one of them is a
read-modify-write, and each carries an argument about why that is safe: bit 15
is `RST` and bits 11:8 are `ACC_RST`, both write-one-self-clearing, both reading
back zero, so writing back what you read cannot retrigger a device reset or
silently zero somebody's energy total.

That reasoning is not in the type system, and it cannot be. It is a paragraph of
prose attached to three methods, and if it is wrong the symptom is that
calibration quietly stops meaning anything. There is one small consolation:
`AlertPinConfig`'s `Default` maps exactly onto the `CONFIG2` reset value of
`0x0000`, so the default is the literal power-on state of the silicon rather
than a convention somebody invented and will later have to remember.

The last post buried a footnote about this moment --- that when an erratum
workaround lands in the shell, "the shell does have logic in it, does need
tests, and they are the expensive kind." This is that moment, arriving with no
erratum involved at all. Ordinary device state was enough. And the tests are
correspondingly less pleasant to write:

```rust
async fn reset_clears_cache_even_when_the_write_fails() { /* ... */ }
async fn a_failed_reset_keeps_the_alert_cache_so_a_later_calibrate_still_disarms() { /* ... */ }
async fn calibrate_all_invalidates_the_channels_it_did_not_reach() { /* ... */ }
```

Every one of those names is a sentence about a partial failure. That is what it
costs to put state in the shell, and it is worth paying only when something else
needs the state in order to be correct.

## When you stop being able to count

The last post ended on arithmetic, and it was the part I was most pleased with:
$(u8, u8, u8)$ is $16{,}777{,}216$ reachable states of which 96 are legal, and
$(\mathtt{Averaging}, \mathtt{ConversionCycle}, \mathtt{Mode})$ is 96 reachable
states, all legal. The illegal region gone, not guarded.

Try that sum on this part and it does not terminate.

Whether `Alert::ShuntOver(ShuntVoltage::from_nanovolts(n))` is legal depends on
the target channel's `AdcRange`, which is runtime state in another register. And
`Calibration::new` takes two `NonZeroU32`s and an `AdcRange`, where the legal
subset is not an enumeration at all but an inequality --- `SHUNT_CAL` has to
land in $[1, 65535]$ after rounding. There is no smaller type to reach for. The
constraint is arithmetic, not structural.

So the illegal region cannot be deleted here, and three things replace counting
it. Which one applies is decided by the size of the domain, and nothing else.

**Walk it, where it is walkable.** All 65,536 bus voltage codes. All 131,072
shunt voltage codes, across both ADC ranges. All 65,536 threshold round-trips
per range. All 2,048 channel-mask operations.  All 16 address strappings. On the
host, natively, in well under a second, with a real panic message when something
breaks. The last post's line about proof by exhaustion that runs in microseconds
survives a part with nine times the registers and five quantities instead of
one.

**Reach for `proptest` only where you cannot.** Calibration, where the input
space is genuinely too large. And with the property *shape* from the last post,
not merely the tool --- `calibration_fails_only_on_range` asserts that range
violations are the **only** way the function fails. Not that bad input is
rejected; that nothing else is. Any future edit that teaches it a new failure
mode breaks that test immediately.

**Borrow the datasheet's own numbers.** This is the leg the last post did not
have, and could not have, because a part with a 1/128 °C LSB has no worked
example to offer. This one does. TI computes a `SHUNT_CAL` of 1280 in §8.2.2.3,
and a shunt limit of $-80\,\mathrm{mV} / 2.5\,\mathrm{µV} = 32000 =
\mathtt{0x8300}$ in §7.1.6, and a minimum `CURRENT_LSB` from Equation 2. All
three are tests. The reasoning, from the commit that added them:

> A test transcribed from the code it tests proves only self-consistency.

There is one more counting question hiding in the API, and it is the one
`#[non_exhaustive]` exists to answer: *can this domain grow?*

`Ina4230Error` is marked. Its domain is open --- not because the part can
develop new failure modes, but because I can discover them, and this driver has
added one per feature so far. Marking it costs a single breaking change instead
of one for every variant I have not thought of yet.

`Alert`, `AlertPinConfig`, `AlertLatch` and `AlertPolarity` are deliberately
*not* marked, and the reason is the most satisfying sentence in the whole
design: their domains are closed by the silicon. `ALERT_MASK` has exactly five
usable encodings. `CONFIG2` has exactly four alert bits. Neither can ever grow a
fifth, because the die is fixed and the datasheet is finished. Marking them
would be actively harmful --- on a struct it forbids construction with
`..Default::default()`, and on an enum it forces a wildcard arm in every
external `match`, suppressing exactly the missed-variant error that makes an
enum worth having.

So the attribute is not a stylistic default. It is a claim about whether a set
can gain members, and for a device driver that claim usually has a physical
answer.

Which leaves the thesis needing a repair, and it is a small one.

For *configuration* --- `Mode`, `Averaging`, `OperatingMode`, `AlertPolarity`,
`AlertLatch`, every field where the silicon enumerates its own options ---
"illegal states are unrepresentable" is still literally, arithmetically
true. That is most of the API surface, and it is the part where the last post's
argument needs no qualification at all.

For *quantities*, the illegal region cannot be deleted. It can only be
concentrated. `Calibration::new` is the single fallible step in configuring this
part. `Celsius::try_from_degrees` was the single fallible step in the one before
it. Both drivers have exactly one place where a legal-looking input is turned
away, and everything downstream of that place holds something that cannot be
wrong.

So the number worth counting stopped being reachable states somewhere between
two registers and thirty-six. What replaced it is smaller and easier to check:
how many functions can say `Err`.

## What I would not claim

Three things, because the rest of this post has been arguing in one direction.

**The caches are the weak point, and documenting them is not the same as fixing
them.** I can write down that a cache cannot observe a power cycle, an EN-pin
toggle, a General Call reset, or another controller on the bus. I have written
it down. Someone will still put two controllers on that bus, and the driver will
be confidently wrong. That is a genuine regression against the first driver,
which had no state and therefore nothing to be stale.

**The alert paths are mock-tested more thoroughly than they are bench-tested.**
The examples run against a real INA4230 from a laptop over a [Pico de
Gallo][pdg] bridge,[^bench] which is enough to prove the registers do what the
datasheet says. Provoking a genuine overcurrent excursion on demand, repeatedly,
is different work and I have not done it. Some of the alert section is argued
rather than demonstrated, and you should read it that way.

**All of this was affordable because it happened before 1.0.** The last post's
closing advice was to spend the design budget on exactly those invariants you
are willing to still be defending at `1.0`.  Restructuring the alert registers
was a breaking change; `cargo-semver-checks` flagged it on the release pull
request, which is the expected and correct outcome, and it cost a bump from 0.1
to 0.2.  Six months from now the same change costs somebody else a migration,
and I would probably not make it.

Make smaller things. Then count how many of them can still say `Err`.

[^ddsl]: Both drivers describe their registers in a small declarative
language and generate the register access layer with
[`device-driver`](https://github.com/diondokter/device-driver). It is
worth being precise about where that sits, because it is not the
functional core. The generated layer is a typed spelling of the
address map and nothing more --- it knows that `SHUNT_VOLTAGE` on
channel 3 lives at `0x11` and is signed sixteen bits, and it knows
nothing about nanovolts. The core sits above it and turns bit
patterns into quantities; the shell sits above that and moves bytes.
Three layers, and the generated one is the only one I do not have to
review.

[^bench]: The five examples in the crate drive a real part from a host
machine, which is a habit from two earlier posts ---
[Writing embedded drivers without an MCU](@/posts/writing-embedded-drivers-without-an-mcu/index.md)
and [Examples that run](@/posts/examples-that-run/index.md). Worth
repeating here only because it is what makes the division of labour in
this post practical: the exhaustive tests answer arithmetic questions
on a laptop in milliseconds, and the part on the bench is left to
answer the one question it is uniquely qualified for, which is whether
the silicon agrees with its own datasheet.

[msm]: @/posts/making-smaller-things/index.md
[tmp]: https://github.com/OpenDevicePartnership/tmp108
[ina]: https://github.com/OpenDevicePartnership/ina4230
[ds]: https://www.ti.com/lit/ds/symlink/ina4230.pdf
[pdg]: https://github.com/OpenDevicePartnership/pico-de-gallo
