+++
title = "The four deterministic outcome paths of the Paavo watchdog"
date = 2026-07-08T08:00:00
description = "One OS thread per board, one watchdog per job, a tiny shared state machine, and four exit conditions that collapse cleanly into eight terminal outcomes. Why threads, not tasks. Why exact-match Test OK."
[taxonomies]
tags = ["embedded", "rust", "paavo", "hil", "concurrency", "watchdog"]
+++

The [previous post](/posts/paavo-introducing/) introduced Paavo at the
architecture level and promised a deeper look at what happens during
step 9 of the lifecycle — the part where a job actually runs against
real silicon. This is that post.

The short version: every job spawns two OS threads. One drives the
probe; the other watches the clock. They communicate through a tiny
piece of shared state and one bounded channel. The watchdog exits in
exactly one of four conditions, and those four conditions map cleanly
onto the eight terminal outcomes from the previous post. The whole
thing is small, deterministic, and finishes in milliseconds even when
something goes wrong.

The longer version is more fun.

## Why OS threads, not tokio tasks

The `probe-rs` API is blocking. There's no `async fn read_frame()`;
there's `fn read()` that may park your thread until the chip says
something or the USB timeout fires. You *can* shove that inside a
`spawn_blocking` and pretend it's async, but you've now got two
runtimes — tokio's executor scheduling your awaits, and the OS
scheduler underneath running the actual blocked thread. When you
need to abandon a stuck probe call, you're negotiating with both
of them.

The other option is to skip the pretense. One OS thread per board,
blocking happily on `probe-rs` calls. When something goes wrong —
the probe hangs, the chip wedges, the USB cable comes loose — the
thread is just a thread. It can be detached. It can be ignored
while the rest of the daemon keeps running. Eventually it'll
unblock or the process will restart; either way the rest of the
fleet keeps working.

That's the architectural pick: **one BoardWorker OS thread per
physical board, paired with one watchdog OS thread per running
job, with a small shared `Arc<WatchdogState>` between them and a
single bounded channel for the "I'm done, you can stop watching"
signal.**

Everything else in this post follows from that choice.

## The actors

Three threads are involved in any running job:

```
                ┌──────────────────────────┐
                │     paavod tokio task    │
                │  (HTTP request handler)  │
                └────────────┬─────────────┘
                             │
                             │ crossbeam_channel: dispatch
                             ▼
                ┌──────────────────────────┐         ┌──────────────────────────┐
                │       BoardWorker        │◄────────│       Watchdog           │
                │      (OS thread)         │  Arc<…> │      (OS thread)         │
                │                          │ shared  │                          │
                │  probe-rs calls live     │ state   │  ticks every 100 ms,     │
                │  here; blocks freely     │         │  checks four conditions  │
                └────────────┬─────────────┘         └──────────────────────────┘
                             │
                             │ crossbeam_channel: log frames
                             ▼
                ┌──────────────────────────┐
                │     paavod tokio task    │
                │  (NDJSON log streamer)   │
                └──────────────────────────┘
```

The HTTP layer dispatches a job to a BoardWorker by sending a small
`JobInputs` struct on a `crossbeam_channel`. The BoardWorker
receives it, calls a `make_session: FnOnce() -> Result<Box<dyn
ProbeSession>>` closure (so the actual probe-rs construction
happens *on* the worker thread, not on the dispatcher's), then
spawns the watchdog and enters the main run loop. Log frames go
back out on another `crossbeam_channel` that a tokio task on the
HTTP side reads and forwards to whichever `paavo-cli` or web client
is subscribed.

Crossbeam channels in both directions because they're cheap to
poll from blocking code and cheap to await from async code (the
`Sender` side is happy on the tokio task; the `Receiver` side
parks the OS thread without involving tokio). No tokio
runtime ever runs on the worker or watchdog threads.

## The shared state

The BoardWorker and the watchdog share a single `Arc<WatchdogState>`:

```rust
struct WatchdogState {
    last_activity: Mutex<Instant>,
    started_at: Instant,
    stop_reason: Mutex<Option<StopReason>>,
}

enum StopReason {
    Inactivity,
    HardMax,
    UserCancel,
    DaemonShutdown,
}
```

That's it. Three fields. The worker writes to `last_activity` every
time a defmt frame comes in (`state.touch(Instant::now())`); the
watchdog reads it to check the inactivity budget. The watchdog
writes to `stop_reason` when it decides the job should die; the
worker reads it at the top of every loop iteration to find out.

You might expect a notification channel from the watchdog to the
worker — something like "wake up, I've decided you should stop."
There isn't one, and the absence is deliberate. The worker is
nearly always inside `session.next_event(timeout_ms)`, which is a
blocking call. A channel send wouldn't unblock it; the worker
wouldn't see the message until `next_event` returned anyway. So
the worker just polls `state.stop_reason()` between iterations.
The blocking `next_event` call uses a 500 ms timeout, so the
worker checks the stop flag at most 500 ms after the watchdog
sets it. For a system whose default inactivity timeout is 120
seconds and whose hard-max is measured in minutes to hours, half
a second of latency on the stop path is invisible.

The "I'm done, you can stop watching" signal in the other direction
*does* use a channel:

```rust
let (worker_done_tx, worker_done_rx) = crossbeam_channel::bounded(1);
```

The worker sends `()` on this channel right before it joins the
watchdog. The watchdog's tick loop checks this channel first on
every tick; if it's been signalled, the watchdog returns
immediately. Without this channel, the watchdog would keep
ticking forever after a natural pass, eating CPU and delaying
process shutdown. (This was lesson M2.2, surfaced during
implementation: the first version didn't have it, and the test
that proved it was needed was the very first test that exercised
a passing job through a real watchdog tick loop.)

## The four exit conditions

Here is the watchdog's tick loop, mildly elided:

```rust
fn run_watchdog(
    state: Arc<WatchdogState>,
    cancel_rx: Receiver<RunCommand>,
    worker_done_rx: Receiver<()>,
    inactivity: Duration,
    hard_max: Duration,
) {
    loop {
        // 1. Did the worker finish naturally?
        if worker_done_rx.try_recv().is_ok() {
            return;
        }

        // 2. Did the user (or daemon shutdown) cancel us?
        if let Ok(cmd) = cancel_rx.try_recv() {
            state.set_stop_reason(match cmd {
                RunCommand::Cancel => StopReason::UserCancel,
                RunCommand::DaemonShutdown => StopReason::DaemonShutdown,
            });
            return;
        }

        let now = Instant::now();

        // 3. Inactivity budget blown?
        if now.duration_since(*state.last_activity.lock()) > inactivity {
            state.set_stop_reason(StopReason::Inactivity);
            return;
        }

        // 4. Hard-max budget blown?
        if now.duration_since(state.started_at) > hard_max {
            state.set_stop_reason(StopReason::HardMax);
            return;
        }

        thread::sleep(Duration::from_millis(100));
    }
}
```

The order matters. `worker_done` is checked first because it's the
happy path; if the worker has already decided the job's outcome,
none of the other checks should run. `cancel` is checked second
because it's an explicit user action and should beat any
about-to-fire timeout. Inactivity is checked before hard-max
because they often fire close together at the end of a stuck job,
and inactivity is the more *specific* diagnosis ("the test stopped
talking") versus hard-max's vaguer "we ran out of time."

The watchdog has **exactly four exit paths**. In order:

1. **`worker_done_rx` fires.** The worker reached a natural
   terminal outcome — `Passed`, `Failed(TestErr)`, `Failed(InfraErr)`
   from a probe disconnect, whatever. The watchdog exits silently
   with `stop_reason` still `None`. The outcome was decided by the
   worker; the watchdog didn't force anything.

2. **`cancel_rx` fires.** Either a developer hit Ctrl-C in
   `paavo-cli`, or `paavod` received SIGTERM and is draining
   in-flight jobs. The watchdog sets `stop_reason = UserCancel`
   or `DaemonShutdown` and returns.

3. **Inactivity timeout.** No defmt frame has arrived for longer
   than the configured window. The watchdog sets `stop_reason =
   Inactivity` and returns. Default window is 120 seconds; tests
   can override per-test via the `inactivity_timeout!()` macro
   (post 3).

4. **Hard-max timeout.** The job has been running longer than its
   wall-clock budget. The watchdog sets `stop_reason = HardMax`
   and returns. Default is 15 minutes for ad-hoc dev jobs, 4
   hours for scheduled soaks; both bounded by an 8-hour
   daemon-wide ceiling.

Four exit paths is the entire surface of the watchdog. That's
deliberately small. Every additional path is another way for the
system to surprise its operator.

## Two clocks with different epochs

Here is the subtlety that bit me during implementation. The
inactivity clock and the hard-max clock measure different things,
so they have **different starting points**.

The hard-max clock starts when the BoardWorker thread spawns,
which is also when the watchdog spawns. From the operator's point
of view, that's "when the job started" — the moment Paavo
committed time on a board to this job. The hard-max budget is
about *not letting one job hog a board forever*, regardless of
what the job is doing.

The inactivity clock has a different job. It's asking: "is the
test still alive?" Defmt frames are the heartbeat. No frames for
N seconds means the test has hung — wfi'd, deadlocked, jumped to
a bad address, whatever. But "no frames for N seconds" only makes
sense *after the test is actually running*, and the test can't
print anything until the probe has attached, the ELF has been
flashed, RTT has been initialized, and the chip has been reset
out of halt. That sequence takes a few seconds on a good day and
can take tens of seconds on a board that's being stubborn.

If the inactivity clock starts when the watchdog spawns, every
job would trip inactivity during its own probe-attach phase.
Useless.

So the worker resets the inactivity clock immediately after
`make_session()` returns successfully:

```rust
let session = make_session()?;
state.touch(Instant::now());  // inactivity clock starts NOW
```

The hard-max clock is not reset. Probe attach time counts against
the wall-clock budget, because that's what wall-clock budgets are
for. Inactivity counts only from "the chip is alive and should be
talking."

Two clocks, two epochs, one shared state struct. Roughly fifty
lines of code, including comments. The comments are longer than
the code because the *reason* is the interesting part.

## How the worker decides natural outcomes

The watchdog handles the four "something went wrong, forcibly
stop" paths. The worker handles the other four — the natural
outcomes — through the event stream:

```rust
loop {
    if let Some(reason) = state.stop_reason() {
        return finalise_for_stop(reason, ...);
    }

    match session.next_event(500) {
        Ok(Some(Event::LogFrame(frame))) => {
            state.touch(Instant::now());
            if frame.level == LogLevel::Info
                && frame.message.trim() == "Test OK"
            {
                seen_test_ok = true;
            }
            let _ = log_tx.send(frame);
        }
        Ok(Some(Event::Bkpt)) if seen_test_ok => {
            return JobOutcome::Passed;
        }
        Ok(Some(Event::Bkpt)) => {
            return JobOutcome::Failed(TerminalOutcome::TestErr {
                message: "bkpt without preceding Test OK".into(),
            });
        }
        Ok(Some(Event::Panic { message })) => {
            return JobOutcome::Failed(TerminalOutcome::TestErr {
                message,
            });
        }
        Ok(Some(Event::Disconnect)) => {
            return JobOutcome::Failed(TerminalOutcome::InfraErr {
                stage: "probe_disconnect".into(),
                message: "probe disconnected mid-run".into(),
            });
        }
        Ok(None) => continue,  // 500ms timeout, loop and re-check stop
        Err(e) => {
            return JobOutcome::Failed(TerminalOutcome::InfraErr {
                stage: "probe_io".into(),
                message: format!("{e}"),
            });
        }
    }
}
```

A few things to notice.

**The pass condition is `Test OK` *then* `bkpt`, in that order.**
A `bkpt` instruction on its own is ambiguous — it could mean the
test passed, or it could mean the chip hit a debugger breakpoint
from a panic, an assertion, or a wedged interrupt handler. The
test convention (inherited from Embassy's test harness) is to
print `defmt::info!("Test OK")` and *then* hit `bkpt`. Paavo
treats those two events as a pair: bkpt with `seen_test_ok = true`
means pass; bkpt without is a test failure.

**The match is exact**: `frame.message.trim() == "Test OK"`, not
`frame.message.contains("Test OK")`. The first implementation used
`contains`, and the first test caught the false-positive case
immediately — a test that logged "this isn't a Test OK"
accidentally passed. The `trim()` is there for leading or trailing
whitespace tolerance because some defmt formatters add it; the
equality is strict for everything else. This was lesson M2.2.

**Probe disconnect is an infra error, not a test failure.** The
USB cable came loose, or the chip dropped off the bus, or the
probe's firmware glitched. None of those are the test's fault.
The board worker bumps its `consecutive_infra_failures` counter;
three in a row and the board auto-quarantines.

**The 500 ms timeout on `next_event`.** This is the polling
interval between stop-flag checks. It's long enough that the
worker isn't spinning when nothing is happening, short enough
that a forced stop from the watchdog takes at most half a second
to take effect. There's nothing magic about 500 ms; 250 or 1000
would both work fine. It's the smallest number large enough that
the overhead of repeatedly waking and re-checking is negligible.

## Eight outcomes, four watchdog paths, one worker

Putting it all together. There are eight possible terminal outcomes:

| Outcome                          | Decided by | How                                           |
|----------------------------------|------------|-----------------------------------------------|
| `Passed`                         | Worker     | Got `Test OK` then `bkpt`                     |
| `Failed { TestErr }`             | Worker     | Got panic, or `bkpt` without `Test OK`        |
| `Failed { InfraErr }`            | Worker     | Probe disconnected or threw an error          |
| `Failed { BuildErr }`            | Daemon     | (Decided pre-dispatch; not the worker's path) |
| `TimedOut { Inactivity }`        | Watchdog   | No defmt frame for N seconds                  |
| `TimedOut { HardMax }`           | Watchdog   | Job ran past wall-clock budget                |
| `Aborted { User }`               | Watchdog   | `paavo-cli` sent Cancel                       |
| `Aborted { DaemonShutdown }`     | Watchdog   | `paavod` got SIGTERM mid-run                  |

The four watchdog exit paths map 1:1 onto the bottom four rows.
The worker handles the top three. `BuildErr` happens before the
worker exists, so it doesn't fit on either side of this diagram —
it's decided by the build step before dispatch ever happens.

That mapping is what makes the system tractable to reason about.
You can ask "what produced this outcome?" and trace it back to
exactly one decision point — either the watchdog or the worker,
either a clock fired or an event arrived. There is no path that
ends in `Passed` without the worker seeing a bkpt. There is no
path that ends in `TimedOut { HardMax }` without the watchdog's
hard-max branch firing. The state machine has no dark corners.

## Testing it without a probe

All of this is testable without ever touching real hardware,
which is the other reason the OS-thread design pays off. The
`ProbeSession` trait is one method:

```rust
pub trait ProbeSession: Send {
    fn next_event(&mut self, timeout_ms: u32) -> Result<Option<Event>>;
}
```

The real implementation wraps `probe-rs` and `defmt-decoder`. The
test implementation is 30 lines:

```rust
pub struct FakeSession { rx: Receiver<Event> }

impl ProbeSession for FakeSession {
    fn next_event(&mut self, timeout_ms: u32) -> Result<Option<Event>> {
        match self.rx.recv_timeout(Duration::from_millis(timeout_ms.into())) {
            Ok(ev) => Ok(Some(ev)),
            Err(RecvTimeoutError::Timeout) => Ok(None),
            Err(RecvTimeoutError::Disconnected) => Ok(None),
        }
    }
}

pub struct FakeScript { tx: Sender<Event> }

impl FakeScript {
    pub fn log(&self, level: LogLevel, msg: &str) { /* ... */ }
    pub fn bkpt(&self) { /* ... */ }
    pub fn panic(&self, msg: &str) { /* ... */ }
    pub fn disconnect(&self) { /* ... */ }
}
```

A test for the pass path looks like this:

```rust
#[test]
fn pass_path_test_ok_then_bkpt() {
    let (session, script) = fake_session();

    let handle = run_job(
        JobInputs { /* ... */ inactivity_timeout_ms: 5_000, hard_max_ms: 10_000, .. },
        JobOutputs { log_tx, },
        move || Ok(Box::new(session)),
    );

    script.log(LogLevel::Info, "Test OK");
    script.bkpt();

    let outcome = handle.join();
    assert_eq!(outcome, JobOutcome::Passed);
}
```

The whole thing runs in milliseconds because the inactivity and
hard-max budgets are deliberately small in the test. The `200ms`
inactivity test for "what happens when no frame ever arrives"
genuinely sleeps for 200 ms and then asserts; there's no clock
mocking, no faking time, no `tokio::time::advance`. It's just a
short timeout and a real `thread::sleep` in the watchdog. The
whole worker test suite — pass, every flavour of failure, both
timeouts, both abort variants — runs in under two seconds.

That speed is why the design pays off. When watchdog behaviour is
the kind of thing you can iterate on in a 200 ms test loop, you
notice antipatterns immediately. The `worker_done` channel was
added the first time a test hung because the watchdog kept
ticking after a natural pass. The exact-match `Test OK` was added
the first time a test passed by accident because the message
contained `"Test OK"` as a substring. Both of those were caught in
the test suite within minutes of the first run.

## What's still stubbed

In the interest of honesty: as of this post, the `RealSession`
that wraps `probe-rs` is stubbed. The trait surface, the
`RealSessionOptions` struct (`probe_selector`, `chip_name`,
`elf_path`, `skip_post_load_reset` for the NXP RT685S quirk), and
all the worker/watchdog logic are stable and tested through the
fake. The actual `probe_rs::Session::attach` and
`defmt_decoder::Frame` decoding land in milestone 6.4, alongside
the lab box bring-up.

The reason the stub exists today rather than waiting is that
having the trait in its final shape means `paavod` can compile
against it now, and the dispatch path can be wired up without
waiting for hardware. The day the real session ships, nothing
above this layer needs to change — that's the entire point of
the abstraction.

## What's next in this series

The next post is about [paavo-meta]: the three macros that test
authors put in their test source (`target!`, `timeout!`,
`inactivity_timeout!`), the tiny linker fragment that preserves
the ELF sections those macros emit, and the host-side parser that
reads them back out. It's a small piece of code that involves a
surprising amount of careful linker-fu, a `cfg_attr` trick that
lets the same crate compile both for host and for embedded
targets, and a parser philosophy that treats missing sections as
fine but malformed sections as crimes.

[paavo-meta]: https://github.com/felipebalbi/paavo/tree/main/crates/paavo-meta
