+++
title = "Waking up without a critical section"
date = 2026-06-13T21:38:00
description = "embassy-rs/embassy#6111 replaces embassy-sync's critical-section-based AtomicWaker with a lockless three-state one ported from futures::task::AtomicWaker. Why fetch_or is the right primitive on the register/wake race, what the wake path no longer has to do, and why the old behavior is still available as CriticalSectionWaker."
[taxonomies]
tags = ["embedded", "rust", "embassy", "async", "atomics", "concurrency"]
+++

A GPIO interrupt fires. The handler's job is small: flip a status
bit, call `waker.wake()`, return. The task that was parked on that
pin is now ready to run, and the executor will get to it on the next
poll.

Until you look at what `waker.wake()` actually did. On every embassy
target, until very recently, that one call took out a critical
section — a global interrupt disable, held for the duration of the
register-or-wake operation — because the `AtomicWaker` in
`embassy-sync` was a typedef-style alias for
`GenericAtomicWaker<CriticalSectionRawMutex>`. On Cortex-M that is
exactly what it sounds like: the wake path raised BASEPRI (or
disabled IRQs outright on older parts), did its bookkeeping, and
lowered it again. A higher-priority interrupt that arrived in the
middle had to wait. One async task's wake imposed a jitter floor on
every other interrupt in the system.

[embassy-rs/embassy#6111](https://github.com/embassy-rs/embassy/pull/6111)
replaces that. The new `AtomicWaker` is a port of
[`futures::task::AtomicWaker`](https://docs.rs/futures/latest/futures/task/struct.AtomicWaker.html)
— the one that has been doing this on `std` targets for years —
adapted for `no_std`. The trade I made is concrete: a small atomic
state machine in place of the critical section, plus one documented
semantics change that `Future::poll` already required callers to
honor. The old behavior is kept verbatim under a new name,
`CriticalSectionWaker`, for the chips and the call sites that still
want it.

## What the type used to be

The pre-#6111 definition is essentially one line:

```rust
pub type AtomicWaker = GenericAtomicWaker<CriticalSectionRawMutex>;
```

`GenericAtomicWaker<M>` wraps an `Option<Waker>` in a
`Mutex<M, Cell<...>>`. With `M = CriticalSectionRawMutex`, every
`register()` and every `wake()` calls `critical_section::with(...)`,
which on Cortex-M expands to "disable interrupts, run the closure,
restore the previous mask." The closure itself is tiny — replace a
cell, maybe clone, maybe call `wake_by_ref` — but the whole CPU is
now refusing to acknowledge anything else for the duration.

That is fine when `wake()` is a background bookkeeping call. It is
much less fine when `wake()` is itself a high-priority IRQ handler
whose latency you care about, or when a *different* high-priority
IRQ arrives during the wake and has to wait for an unrelated
cell-swap to finish before it gets to run.

## The three-state machine

The new type, in `embassy-sync/src/waitqueue/atomic_waker.rs`,
replaces the mutex with a packed `AtomicUsize` and three constants:

```rust
const WAITING: usize     = 0;
const REGISTERING: usize = 0b01;
const WAKING: usize      = 0b10;
```

The important thing about those constants — and this came out of
review, so it's now spelled out in the source comments — is that
they are **flags, not exclusive states**. `REGISTERING | WAKING`
(0b11) is a legal, intentional state of the machine. It means a
`register()` is mid-flight *and* a `wake()` arrived while it was
running, and the wake has been flagged as pending. The encoding is
load-bearing: only the holder of `REGISTERING` or `WAKING` (and
never both at once, by virtue of the protocol) is allowed to touch
the underlying waker cell, and the 0b11 state is what tells the
in-flight `register()` that there is wake duty waiting for it on
the way out.

The wake side is short and worth showing in full:

```rust
match self.state.fetch_or(WAKING, AcqRel) {
    WAITING => {
        // We own the cell. Fire the wake, release.
        unsafe {
            if let Some(w) = &*self.waker.get() {
                w.wake_by_ref();
            }
        }
        self.state.swap(WAITING, Release);
    }
    _ => {
        // Somebody else owns the cell. They'll deliver the wake.
    }
}
```

Two arms, and that is the whole thing. If the prior state was
`WAITING`, we are now the sole owner of the cell via the `WAKING`
bit; we invoke the stored waker by reference, then drop the bit. If
the prior state was `REGISTERING`, the OR has produced
`REGISTERING | WAKING`, and we deliberately do nothing else,
because the in-flight `register()` will see our bit on its way out
and deliver the wake itself.

## Why fetch_or and not compare_exchange

This is the load-bearing decision in the whole PR, and review asked
the obvious question: shouldn't this be a `compare_exchange`? It's
the safer default, the one you reach for first.

It would be silently wrong here. Consider the race:

1. A task calls `register(cx.waker())`. The CAS from `WAITING` to
   `REGISTERING` succeeds; we now own the cell and start installing
   the new waker.
2. Mid-installation, an interrupt fires and calls `wake()`.

If `wake()` did `compare_exchange(WAITING, WAKING, ...)`, that CAS
would observe `REGISTERING`, fail, and *silently return* — there is no
prior value it could have CAS-ed against that would have encoded "a
wake is pending." The in-flight `register()` would then finish, CAS
its own state cleanly back from `REGISTERING` to `WAITING`, and never
learn that anything happened. The waker is installed, nobody is going
to invoke it, the task is hung. The kind of bug you find at 2am after
staring at the screen for an hour wondering why an interrupt that you
can *see* on the registers isn't producing a task wakeup.

`fetch_or(WAKING, AcqRel)` doesn't have that failure mode. The
wake's contribution to the state word is unconditional and visible
to whoever observes the state next. The in-flight `register()`
tries to release with `compare_exchange(REGISTERING, WAITING, ...)`
on its way out; that CAS will *fail* exactly when a wake raced it,
because the observed state is now `REGISTERING | WAKING`, not
`REGISTERING`. The failure branch is the handoff signal: it takes
the freshly-installed waker out of the cell, clears both bits with
a `swap`, and invokes the waker *after* releasing the cell — so
that a user waker that re-enters `register()` or `wake()` on the
same `AtomicWaker` from inside `wake()` cannot deadlock against
our own state.

The wake is delivered exactly once, by whichever side observes the
contention. The handoff is structural, not best-effort. `fetch_or`
is doing more than "atomic OR" here; it is the data structure that
makes the race path correct.

## What changed in the contract

There is one user-visible semantic difference, and the PR owns it
in the CHANGELOG: `AtomicWaker::register()` now wakes the
*previously registered* waker if you replace it with a different
one. This matches what
[`WakerRegistration`](https://docs.rs/embassy-sync/latest/embassy_sync/waitqueue/struct.WakerRegistration.html)
has always done. The pre-#6111 `AtomicWaker` would silently drop
the old waker on the floor.

The reason this is safe is that `Future::poll` is already
documented to require it. The relevant line in the
[`Future::poll`](https://doc.rust-lang.org/std/future/trait.Future.html#tymethod.poll)
contract:

> Note that on multiple calls to `poll`, only the `Waker` from the
> `Context` passed to the most recent call should be scheduled to
> receive a wakeup.

A correctly-written driver already re-registers on every poll,
because the `Waker` it gets on the *n*-th poll is not guaranteed
to be the one it got on the *(n-1)*-th. Waking the evicted
predecessor formalizes that: if a different task ends up polling
the same future, the displaced task gets a chance to re-register
itself instead of vanishing. This was a late commit, prompted by
review, and it is what the CHANGELOG entry calls out by name.

## Two siblings now, on purpose

The previous behavior didn't disappear. It moved into its own
module, `embassy-sync/src/waitqueue/critical_section_waker.rs`, and
got a name that says what it is:

```rust
pub struct CriticalSectionWaker {
    waker: GenericAtomicWaker<CriticalSectionRawMutex>,
}
```

This is a verbatim preservation of the old `AtomicWaker` body —
the same `wake_by_ref + restore` flow, the same lack of an atomic
state machine, the same critical section on the wake path. Call
sites that depended on the old "replace without waking" semantics
keep working with a one-word rename.

The asymmetry is deliberate, and I want to be explicit about it
because it matters for anyone reading older code: the new
`AtomicWaker` wakes evicted predecessors; `GenericAtomicWaker<M>`
does not, and `CriticalSectionWaker` (being a wrapper around it)
does not either. Review caught an earlier commit that tried to
bring `GenericAtomicWaker` along to the new semantics, and I
reverted it. A public type that has been in the tree for years
should not change behavior under callers without a much louder
migration story than this PR is in a position to write — and
there's no need for one. Callers who want the new semantics have
`AtomicWaker`; callers who want the old have the type they were
already using.

## thumbv6 doesn't have the atomics

The lockless state machine assumes you can do
`AtomicUsize::{compare_exchange, swap, fetch_or}` on the target.
Cortex-M0/M0+ (thumbv6m) can't — no LDREX/STREX in the instruction
set, so the LLVM atomics for anything past naked load/store aren't
there. The same goes for a handful of other targets in the embassy
matrix (Xtensa S2, AVR, RV32I — anything missing the relevant
`target_has_atomic` cfg).

The PR's solution is the obvious one: gate the new type on
`#[cfg(target_has_atomic = "32")]`, and on every other target make
`AtomicWaker` a type alias for `CriticalSectionWaker`. Callers see
the same type name; the implementation behind it is the one the
hardware can actually run. The sibling type isn't there for
nostalgia. Some chips genuinely need it.

## What the wake path no longer does

The benchmark on the bench was a GPIO IRQ wake on an FRDM-MCXA266
(Cortex-M33 at 180 MHz); the lockless `wake()` body came out about
290 ns shorter than the critical-section one. I'm not making that
the headline. The wall-clock delta is small enough to be well
within run-to-run noise on a 180 MHz core; if the structural
change weren't real, I would not trust that number to mean
anything.

The structural change is real, and it is what the PR is actually
about. On any embassy target with `AtomicUsize`-class atomics —
which is most of them — the wake path of `AtomicWaker` no longer
disables interrupts. An IRQ handler that calls `waker.wake()`
doesn't enter a critical section, doesn't pay the enter/exit cost
on every wake, and doesn't impose a jitter floor on whatever
higher-priority interrupt would otherwise have preempted it. The
worst case for a preempting IRQ is now an `AtomicUsize` op or two
and a `wake_by_ref` call. On chips that *can't* do the atomics, the
fallback is the type that was already in the tree; nothing
regresses.

None of this is novel. `futures::task::AtomicWaker` has been doing
this on `std` for years, and the algorithm here is a direct port.
What changes is that `embassy-sync` — and every embassy driver
that uses `AtomicWaker` for IRQ-to-task wakeups — gets that
property by default. A single-waiter waker can hand off the
register-vs-wake race with a `fetch_or`-based state machine instead
of a critical section, and the cost is one documented semantics
change that `Future::poll` already required callers to honor.
