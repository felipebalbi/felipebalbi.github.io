+++
title = "Paavo: an HIL test runner for embedded Rust"
date = 2026-06-24T08:00:00
description = "Introducing Paavo — the runner that lives on the other end of the 'cargo test against real silicon' loop. Architecture, design rationale, and what's coming."
[taxonomies]
tags = ["embedded", "rust", "paavo", "hil", "testing", "embassy"]
+++

The [last post](/posts/writing-embedded-drivers-without-an-mcu/)
ended with a throwaway line about CI:

> Real CI. This is the one I'm most excited about. `cargo test` runs
> on a CI runner that has a Pico de Gallo plugged into it, with real
> chips wired up. Every PR exercises the driver against real silicon.

The runner on the other end of that pipe is called *Paavo*. This
post is what it is, how it's put together, and why.

## The problem

Two workflows want to share the same fleet of boards.

The first is **nightly automation**: build [embassy-rs/embassy]'s
`main` against the test corpus, flash each test onto the appropriate
board, capture the result, repeat for hundreds of tests. Long-running
[soak] tests live in the same bucket — start them at midnight, let
them run for hours, surface anything that broke.

[embassy-rs/embassy]: https://github.com/embassy-rs/embassy
[soak]: https://en.wikipedia.org/wiki/Soak_testing

The second is **ad-hoc developer requests**: "I'm working on the I²C
driver, I want to push my branch to an mcxa266 and see what
happens." Same fleet, very different latency expectations. The dev
wants the result in minutes, not at 06:00 tomorrow.

The naive answer is "stand up two systems." That's wrong, for a
boring reason: there is exactly one set of boards. They're wired
into the harness on one bench, in one room, plugged into one Linux
box. Splitting the queue across two systems means somebody has to
arbitrate which jobs get which boards, and you've just reinvented a
scheduler with extra steps.

The right answer is one runner that knows about both workflows,
schedules them sensibly against the same physical fleet, and gets
out of the way.

That runner is Paavo. Named after [Paavo Nurmi], the *Flying Finn*,
because the soak jobs are nightly long-distance work and naming
projects is fun.

[Paavo Nurmi]: https://en.wikipedia.org/wiki/Paavo_Nurmi

## What Paavo isn't

The list of non-goals is shorter than the list of goals and gets at
the shape faster:

- **Not a CI replacement.** Paavo doesn't gate PRs, doesn't comment
  on commits, doesn't render badges. CI calls Paavo; Paavo doesn't
  call CI.
- **Not multi-tenant.** Single team, single fleet, trusted network
  perimeter. No auth in v1. If you want LAN access, put Tailscale
  in front of it.
- **Not cross-platform on the daemon.** Linux only. The CLI works
  anywhere, because it's an HTTP client.
- **Not a teleprobe fork.** Embassy's [teleprobe] is the prior art
  here and it does its job well, but it's a binary with no library
  surface, so Paavo talks to [`probe-rs`] and [`defmt-decoder`]
  directly. The test-source-side macros (`target!()`,
  `timeout!()`) are kept; the runner internals are not.

[teleprobe]: https://github.com/embassy-rs/teleprobe
[`probe-rs`]: https://probe.rs
[`defmt-decoder`]: https://crates.io/crates/defmt-decoder

## The shape: three binaries, one workspace

Paavo ships as three separate processes:

```
            ┌──────────────┐         ┌──────────────┐
            │  paavo-cli   │         │  paavo-web   │
            │ (developer)  │         │ (read-only)  │
            └──────┬───────┘         └──────┬───────┘
                   │ HTTP                   │ SQLite
                   │                        │ (read-only)
                   ▼                        ▼
            ┌─────────────────────────────────────┐
            │              paavod                 │
            │  HTTP + scheduler + BoardWorkers    │
            └──────────────┬──────────────────────┘
                           │
                           │ SQLite (WAL, single writer)
                           ▼
                   ┌───────────────┐
                   │  paavo.sqlite │
                   └───────────────┘
```

- **`paavod`** is the headless daemon. It owns the HTTP API, the
  scheduler, one OS thread per board (the *BoardWorker*), and the
  SQLite writer. Supervised by systemd in production.
- **`paavo-web`** is a read-only viewer. Server-side HTML, no
  client framework. It opens the SQLite database in read-only mode
  and renders dashboards.
- **`paavo-cli`** is the developer-facing terminal client. HTTP
  client to `paavod`. Subcommands for submitting jobs, watching
  log streams, listing the fleet.

Three binaries instead of one because they have different uptime
expectations and different blast radius. The daemon needs to stay
up; if the web UI panics on a malformed query, that mustn't poison
an in-flight job. The CLI lives on developer laptops, sometimes on
macOS or Windows, and doesn't need any of the daemon's deps. Each
binary brings in only what it actually uses.

The IPC between daemon and web UI is the SQLite file. WAL mode,
busy-timeout configured, foreign keys on. One writer (the daemon),
one reader (the web UI). No bespoke protocol, no message queue, no
service mesh. The database *is* the protocol.

## The workspace

There are ten crates. Three are the binaries above; seven are
libraries. The split looks like over-engineering and isn't:

```
paavo-proto    ── pure data types (no workspace deps)
paavo-meta     ── no_std test-source macros (no deps at all)
paavo-db       ── SQLite + migrations + typed row helpers
paavo-build    ── cargo invocation + tar + blake3 (no internal deps)
paavo-probe    ── probe-rs/defmt-decoder + ELF section parser
paavo-runner   ── per-job BoardWorker + watchdog
paavo-core     ── scheduler, quarantine, glue
paavod         ── daemon binary (axum, tokio)
paavo-cli      ── developer CLI binary
paavo-web      ── read-only web UI binary
```

The dependency rules are strict and enforced from day one:

- `paavo-proto` depends on nothing in the workspace. Every other
  crate's wire types come from here.
- `paavo-meta` is `no_std` and has *zero* dependencies — it ships
  inside the test crate that gets cross-compiled for Cortex-M.
- `paavo-build` has no internal deps either. Cargo invocation and
  tar unpacking don't need to know about the rest of the system.
- `axum`, `tokio`, and `tower-http` only appear in the two binary
  crates that need them (`paavod`, `paavo-web`). The libraries are
  runtime-agnostic.
- Library crates use `thiserror` for typed errors. Binaries use
  `anyhow`. The boundary is mechanically enforceable: a library
  pulling in `anyhow` is a code-review smell that pops out
  immediately.

The point of this discipline isn't aesthetic. It's that the system
is testable. `paavo-runner` can be exercised end-to-end with a
fake `ProbeSession` in milliseconds; `paavo-core::scheduler` runs
against an in-memory SQLite with hand-injected timestamps;
`paavo-build` is a pure-compute crate that doesn't need a probe or
a database to test. The strict boundaries are what make that
possible.

## The job lifecycle

A job starts as a multipart POST to `paavod`:

```
POST /jobs
Content-Type: multipart/form-data

[crate.tar] + [{
    "priority": "interactive",
    "submitter": "felipe",
    "board_selector": { "kind": "mcxa266" },
    "inactivity_timeout_ms": 120000,
    "hard_max_ms": 900000
}]
```

What happens next, in order:

1. **Hash the tar.** Paavo computes a [blake3] digest of the
   uploaded bytes. This becomes the job's `tar_blake3` and the
   lookup key for the build cache. (Post 4 of this series goes
   deep on this; for now just know that two identical tars produce
   the same digest, and a one-byte edit produces a fresh one.)
2. **Validate the selector.** The board selector matches at least
   one board in the fleet, or the request is rejected with
   `400 SelectorNeverMatches` at enqueue time. A typo like
   `"mcxap266"` doesn't sit in the queue forever; it fails
   immediately, at the caller.
3. **Validate the timeouts.** Each individual timeout is checked
   against a daemon-wide ceiling (default 8 hours). A 24-hour
   `hard_max_ms` is rejected at enqueue with `OverCeiling`. Again:
   fail at the caller, not in the depths.
4. **Insert the job row.** Atomic SQLite insert. The job's state
   is `submitted`.
5. **The scheduler picks it up.** Highest priority first, oldest
   `submitted_at` as the tiebreaker. Jobs that have been waiting
   more than six hours get auto-promoted to interactive priority
   so a flood of nightly jobs can't starve a developer's
   afternoon push.
6. **Board selection.** Among healthy boards matching the
   selector, the scheduler picks the least-recently-used one. The
   LRU pick spreads load across the fleet and surfaces flaky
   boards faster — if board A is failing 30% of jobs and board B
   is failing 1%, LRU rotation means both see roughly the same
   number of jobs, and the bad one's failure rate stands out.
7. **Build (or cache hit).** If `build_cache` already has an ELF
   for this `tar_blake3` and the file still exists on disk, skip
   the build. Otherwise run `cargo build --release` in a sandboxed
   target dir, hash the ELF, store the cache entry.
8. **Dispatch.** The daemon transfers ownership of the job to a
   *BoardWorker* — one OS thread per physical board, blocked on a
   `crossbeam_channel` for incoming work.
9. **Run.** The BoardWorker attaches the probe, flashes the ELF,
   spins up a watchdog thread, and streams `defmt` frames out
   over a channel. Post 2 of this series is entirely about what
   happens in this step and why.
10. **Terminate.** The job ends in one of eight terminal outcomes
    (`Passed`, three flavours of `Failed`, two of `TimedOut`, two
    of `Aborted`). The outcome is written to the database, the
    log stream is closed, the BoardWorker releases the probe and
    goes back to listening.

That's the whole pipeline. Steps 1–4 are HTTP-handler work; step 5
is the scheduler; steps 6–8 are dispatch; step 9 is the per-job
runner; step 10 is finalization. Each step has a clear owner crate
and a clear boundary; you can replace any one of them without
touching the others.

[blake3]: https://github.com/BLAKE3-team/BLAKE3

## The seven states and eight outcomes

Every job moves through a finite state machine:

```
                       ┌────────┐
                       │Submitted│
                       └────┬───┘
                            │ scheduler picks it up
                            ▼
                       ┌────────┐
                       │Building│
                       └────┬───┘
                            │ build succeeds (or cache hit)
                            ▼
                       ┌────────┐
                       │Running │
                       └────┬───┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
   ┌────────┐         ┌────────┐          ┌────────┐
   │ Passed │         │ Failed │          │TimedOut│   ┌────────┐
   └────────┘         └────────┘          └────────┘   │Aborted │
                                                       └────────┘
```

The terminal states are `Passed`, `Failed`, `TimedOut`, and
`Aborted`. Internally each of those expands into a richer
*outcome* that carries the why. There are eight distinct ones,
because the distinction matters for what Paavo does next:

| Outcome                          | What it means                            | Quarantines the board? |
|----------------------------------|------------------------------------------|------------------------|
| `Passed`                         | Test printed `Test OK`, then `bkpt`      | No                     |
| `Failed { TestErr }`             | Panic frame, or `bkpt` without `Test OK` | No                     |
| `Failed { BuildErr }`            | `cargo build` failed                     | No                     |
| `Failed { InfraErr }`            | Probe disconnect, flash error, etc.      | **Yes**                |
| `TimedOut { Inactivity }`        | No defmt frame for N seconds             | Maybe[^inactivity]     |
| `TimedOut { HardMax }`           | Job ran past wall-clock budget           | No                     |
| `Aborted { User }`               | Developer hit Ctrl-C in `paavo-cli`      | No                     |
| `Aborted { DaemonShutdown }`     | `paavod` got SIGTERM mid-run             | No                     |

[^inactivity]: Only if the BoardWorker couldn't release the probe
    cleanly when the inactivity watchdog fired. A test that hangs
    on a `wfi` but releases the probe is a test bug, not an infra
    bug; the same hang but the probe is also wedged probably means
    the board needs a power cycle.

A board that accumulates three consecutive `Failed { InfraErr }`
outcomes (or the conditional inactivity case above) gets
auto-quarantined: marked unhealthy in the `board` table, removed
from the scheduler's candidate set, and skipped on future
selectors. The reason is logged (`auto: 3 consecutive infra
failures`) and the web UI surfaces it. Manual unquarantine clears
the counter and brings the board back.

The split between *terminal state* and *outcome* is deliberate.
The state machine has seven values because that's what the SQL
`CHECK` constraint enforces; the outcome enum has eight variants
because that's what the business logic cares about. The two layers
don't have to be the same shape, and trying to make them the same
shape leads to either a state-machine explosion or a lossy
outcome representation. Keeping them separate keeps each one
honest.

## What's actually running today

Honesty section. As of this post:

- The seven foundation crates (`paavo-proto`, `paavo-meta`,
  `paavo-db`, `paavo-build`, `paavo-probe`, `paavo-runner`,
  `paavo-core`'s skeleton) are complete and tested. Around 80
  tests across the workspace, fully deterministic.
- The three binaries (`paavod`, `paavo-cli`, `paavo-web`) are
  four-line `println!` stubs. The HTTP layer, the CLI surface,
  and the web UI all land in upcoming milestones.
- The real `probe-rs`-backed `ProbeSession` is stubbed; the trait
  surface is stable, the integration with hardware lands when the
  lab box is ready.

This post is about the architecture you'll be running against once
those last pieces land. The work behind it is real and committed;
the binary that ties it all together is what's left to wire up.

## What's next in this series

The next three posts go deeper on the parts that were most fun to
design:

- **Post 2** — *The four deterministic outcome paths of the Paavo
  watchdog.* One OS thread per board, one watchdog thread per job,
  a small shared state machine, and four exit conditions that
  collapse into eight outcomes. Why threads not tasks. Why the
  inactivity clock and the hard-max clock have different epochs.
  Why exact-match `"Test OK"` instead of `contains`.
- **Post 3** — *Embedding metadata in ELF: paavo-meta's macros
  and the linker fragment.* Three macros, one tiny linker
  fragment, a `cfg_attr` trick that lets the same crate compile
  both for the host and for `thumbv8m.main-none-eabihf`, and a
  parser philosophy that treats missing sections as fine and
  malformed sections as crimes.
- **Post 4** — *Content-addressed build caching with blake3.* The
  cache hit rate, the LRU eviction, the self-healing cache lookup
  that prunes its own orphan rows. Once there are real numbers
  from real runs.

Paavo's source lives [on GitHub][paavo-repo]; the design spec and
the implementation plan are in `docs/`. Pull requests and design
critiques welcome.

[paavo-repo]: https://github.com/felipebalbi/paavo
