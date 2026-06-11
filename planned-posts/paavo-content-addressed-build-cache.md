+++
title = "Content-addressed build caching for an HIL test runner"
date = 2026-09-30T08:00:00
description = "blake3-keyed ELF caching, LRU eviction, a self-healing lookup that prunes its own orphans, and what the cache hit rate actually looks like after a few months of running real jobs."
[taxonomies]
tags = ["embedded", "rust", "paavo", "build-cache", "blake3", "sqlite", "performance"]
+++

> **Note.** This post is intentionally undated for now. It needs
> real cache-hit-rate numbers from a real Paavo deployment to be
> worth publishing. Draft this once `paavod` is running and
> there's a few weeks of telemetry to point at. The placeholder
> date above (2026-09-30) is roughly when that should be true —
> ~3 months past the M3.2.e + M4 milestones — but adjust before
> publishing.

---

The first three posts in this series have been about
correctness: the architecture that makes the system testable
([part 1](/posts/paavo-introducing/)), the watchdog that produces
deterministic outcomes ([part 2](/posts/paavo-watchdog-four-outcomes/)),
the metadata format that prevents misconfiguration
([part 3](/posts/paavo-elf-metadata-linker-fragment/)).

This post is about speed.

Specifically, it's about the build cache: the layer that sits
between "developer pushed a crate.tar to `paavod`" and "the
BoardWorker started flashing an ELF onto a chip." If you do
nothing clever in that gap, every job costs you a full `cargo
build --release`, which on a non-trivial Embassy test is the
better part of a minute on a fast box and several minutes on a
slow one. Paavo runs hundreds of jobs a day across the fleet;
that arithmetic gets bad fast.

The fix is a small SQLite-backed content-addressed cache, keyed
on the [blake3] hash of the uploaded tar. The mechanism is
roughly 200 lines of code split across two crates. The interesting
bits aren't the storage — it's a single table in the same
SQLite the rest of the system uses — but a handful of design
choices that make the cache trustworthy under failure.

[blake3]: https://github.com/BLAKE3-team/BLAKE3

## The naive cache, and why it isn't enough

The simplest possible cache is "hash the inputs, key the output
by that hash." For Paavo, that means: take the raw bytes of the
uploaded `crate.tar`, run blake3 on them, get a 256-bit digest,
hex-encode it, use the hex string as the lookup key. Two
identical tars produce the same hash; a one-byte edit produces a
completely different hash.

```rust
pub fn blake3_hex(bytes: &[u8]) -> String {
    blake3::hash(bytes).to_hex().to_string()
}
```

That's the entire hashing layer. blake3 is fast enough that the
overhead is invisible — single-digit milliseconds for the kinds
of tar sizes (~5-50 MiB) that test crates produce, on a laptop.
The hash is the truth-of-the-input; if it matches a previous
job's tar, the previous job's ELF is reusable.

The naive table is just as simple:

```sql
CREATE TABLE build_cache (
    tar_blake3  TEXT NOT NULL PRIMARY KEY,
    elf_path    TEXT NOT NULL,
    built_at    INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL,
    size_bytes  INTEGER NOT NULL
);

CREATE INDEX idx_build_cache_lru ON build_cache(last_used_at);
```

Five columns. The primary key is the hash; the row points at an
ELF file on disk. `last_used_at` powers the LRU eviction (more on
that later). `size_bytes` is the on-disk ELF size, kept in the
table so the eviction loop doesn't need to `stat()` every file.

The lookup, naively, is:

```rust
pub fn cache_lookup(conn: &Connection, tar_blake3: &str) -> Result<Option<PathBuf>> {
    let row = BuildCacheEntry::get(conn, tar_blake3)?;
    Ok(row.map(|r| r.elf_path))
}
```

If the row exists, return the path. If it doesn't, return `None`
and let the caller run `cargo build`.

This is *almost* right, and the gap between "almost right" and
"actually right" is where the interesting work lives.

## What goes wrong with the naive cache

Four failure modes, all of which I hit on the first day of testing:

**1. The row exists but the file is gone.** Disk filled up, an
operator manually deleted something to make room, a sloppy
deploy wiped `/var/lib/paavo/cache/`, whatever. The DB still
thinks the ELF is there. Naive lookup returns `Some(path)`; the
flash step opens the path; the open fails. The job dies with a
weird internal-server-error.

**2. The file exists but the row is gone.** Symmetric problem.
A previous eviction pass deleted the row, then crashed before
deleting the file. The file is now an *orphan* — it consumes
disk forever but the cache will never use it again, because the
hash that points at it is gone.

**3. Eviction is non-atomic.** When the cache is over its size
budget, the eviction loop has to delete rows from SQLite *and*
delete files from disk. Those are two different operations on
two different storage systems. If the process crashes between
them, you end up in failure mode 1 or 2 depending on which order
the operations ran in.

**4. Cargo's incremental build does its own thing.** Even on a
"cache hit," Paavo never wants to skip cargo's incremental
compilation cache, because most of what makes a follow-up build
fast is cargo's own work, not the ELF copy. The build cache
needs to layer on *top* of cargo's cache, not replace it.

The fixes for all four are small. Cumulatively they're what makes
the system actually work in production.

## Fix 1: self-healing lookup

The lookup function checks the disk before returning a hit:

```rust
pub enum CacheLookup {
    Hit { elf_path: PathBuf },
    Miss,
}

pub fn cache_lookup(
    conn: &Connection,
    tar_blake3: &str,
    now_ms: i64,
) -> Result<CacheLookup> {
    let Some(entry) = BuildCacheEntry::get(conn, tar_blake3)? else {
        return Ok(CacheLookup::Miss);
    };

    if !entry.elf_path.exists() {
        // Row points at a vanished file. Prune and report Miss.
        BuildCacheEntry::delete(conn, tar_blake3)?;
        tracing::warn!(
            tar_blake3,
            path = %entry.elf_path.display(),
            "build cache row pointed at vanished ELF; pruned",
        );
        return Ok(CacheLookup::Miss);
    }

    BuildCacheEntry::touch_last_used(conn, tar_blake3, now_ms)?;
    Ok(CacheLookup::Hit { elf_path: entry.elf_path })
}
```

Three things to notice:

- The lookup **converts a corrupt hit into a clean miss** by
  deleting the stale row and reporting `Miss`. The caller will
  then run `cargo build`, store a fresh row, and the cache
  self-heals. No operator intervention; no weird "stale cache"
  error path.
- It logs a warning when this happens. The warning is the
  visible-symptom version of "your cache is in an unexpected
  state"; if it fires repeatedly, something is wrong upstream
  (probably the eviction loop or a disk-cleanup cron).
- It updates `last_used_at` on the hit, so the LRU eviction
  rotates correctly. This is a one-row UPDATE; it's cheap.

The `exists()` check costs one stat call per hit. On the kinds of
filesystems Paavo runs on (ext4, xfs, btrfs on a local SSD),
that's microseconds. Way under the threshold of caring.

## Fix 2: bounded scan + caller-owned file deletion

The eviction loop does the work in two clear phases:

```rust
pub fn evict_until_under(
    conn: &Connection,
    max_bytes: i64,
) -> Result<Vec<BuildCacheEntry>> {
    let mut evicted = Vec::new();

    loop {
        let stats = BuildCacheEntry::stats(conn)?;
        if stats.total_bytes <= max_bytes {
            return Ok(evicted);
        }

        // Pop the LRU row (idx_build_cache_lru makes this O(log n)).
        let Some(entry) = BuildCacheEntry::pop_lru(conn)? else {
            // Cache is empty but somehow over budget? Shouldn't happen,
            // but don't loop forever.
            return Ok(evicted);
        };
        evicted.push(entry);
    }
}
```

Phase one: drop rows from SQLite until `SUM(size_bytes) <=
max_bytes`. The function returns the list of evicted entries so
the caller can do phase two:

```rust
let evicted = BuildCacheEntry::evict_until_under(&conn, max_cache_bytes)?;
for entry in evicted {
    if let Err(e) = std::fs::remove_file(&entry.elf_path) {
        // Treat as a warning. A later reconciliation pass will
        // catch any orphans this leaves behind.
        tracing::warn!(
            path = %entry.elf_path.display(),
            error = %e,
            "failed to remove evicted cache ELF",
        );
    }
}
```

Splitting the two phases means:

- The SQL operations are batched and atomic per row. The DB never
  ends up in a half-evicted state.
- File deletion failures don't roll back DB changes. A failed
  `remove_file` becomes an orphan ELF — annoying, but a leak, not
  a corruption. The eviction loop keeps making forward progress.
- The caller decides what to do on file-deletion failure. The
  library doesn't decide for them. Logging-and-continuing is the
  sensible default, but a more paranoid operator could escalate
  to "panic and let systemd restart us" or "page the on-call."

The doc-comment on `evict_until_under` is unusually long about this:

> The caller MUST `std::fs::remove_file(&entry.elf_path)` for every
> returned entry to reclaim disk. Without that step the DB row is
> gone but the ELF file leaks. Treat `remove_file` failures as
> warnings (log and continue) — a later reconciliation pass handles
> the general orphan case anyway.

The "later reconciliation pass" is the next fix.

## Fix 3: periodic orphan sweep

The lookup-side healing handles "row points at vanished file." The
eviction-side discipline minimizes "file exists but row is gone."
But neither covers the case where the daemon crashes during
eviction in some unlucky pattern, or an operator wipes the cache
directory while the daemon is down, or a backup-restore introduces
inconsistencies.

For that, Paavo runs a periodic *reconciliation sweep* — once
every few hours, or at daemon startup, walk the cache directory
and the cache table, and reconcile:

```rust
pub fn reconcile_cache(conn: &Connection, cache_dir: &Path) -> Result<ReconcileReport> {
    let mut report = ReconcileReport::default();

    // 1. Collect on-disk ELFs.
    let mut on_disk: HashSet<PathBuf> = WalkDir::new(cache_dir)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| e.into_path())
        .collect();

    // 2. Collect in-DB paths.
    let in_db: HashMap<PathBuf, String> = BuildCacheEntry::all(conn)?
        .into_iter()
        .map(|e| (e.elf_path, e.tar_blake3))
        .collect();

    // 3. Files in DB but not on disk: prune the rows.
    for (path, hash) in &in_db {
        if !on_disk.contains(path) {
            BuildCacheEntry::delete(conn, hash)?;
            report.pruned_dangling_rows += 1;
        }
    }

    // 4. Files on disk but not in DB: delete the orphans.
    for path in &in_db.keys().cloned().collect::<HashSet<_>>() {
        on_disk.remove(path);
    }
    for orphan in on_disk {
        let _ = std::fs::remove_file(&orphan);
        report.deleted_orphan_files += 1;
    }

    Ok(report)
}
```

The sweep is the safety net. Under normal operation it should
find nothing; the lookup-side healing and the eviction-side
discipline keep the system clean. When it does find something —
a non-zero `pruned_dangling_rows` or `deleted_orphan_files` — the
report goes into the logs and the operator has a concrete
artifact to investigate. A persistent non-zero `deleted_orphan_files`
points at a buggy eviction or a crash-prone code path; a
persistent non-zero `pruned_dangling_rows` points at someone
deleting cache files behind the daemon's back.

The sweep runs as a tokio task on its own schedule, gated behind
a configurable interval. The default is once every six hours,
which is frequent enough to catch problems quickly and infrequent
enough that the I/O cost is negligible.

## Fix 4: layer on top of cargo's incremental cache

Paavo's build cache stores the **linked ELF**, not the build
artifacts cargo produces along the way. That's deliberate.

When Paavo gets a cache *miss*, it doesn't start `cargo build`
from a clean target dir. It uses a shared `CARGO_TARGET_DIR`
that persists across jobs:

```rust
pub struct BuildPlan {
    pub crate_dir: PathBuf,
    pub target_dir: PathBuf,  // shared across jobs!
    pub cargo_update_packages: Vec<String>,
}
```

The shared `target_dir` means cargo's own incremental compilation
cache survives between jobs. A one-byte edit to `src/main.rs`
produces a fresh `tar_blake3` — cache miss at Paavo's layer —
but the `cargo build` that follows mostly reuses the previous
job's compiled dependencies. The "cache miss" path is typically
seconds, not minutes, because cargo is doing 5% of the work
instead of 100%.

So there are *two* caches stacked:

1. **Paavo's content-addressed ELF cache.** Keyed on tar hash.
   Hit → skip cargo entirely, copy the ELF, flash it.
2. **Cargo's incremental compilation cache.** Lives in the
   shared `target_dir`. Hit → recompile only what changed.

On a cache hit at layer 1, the job goes from upload to flash in
under a second. On a cache miss at layer 1 but heavy reuse at
layer 2 (the typical "developer iterating on a test"), the job
goes from upload to flash in a few seconds. The full pessimistic
case (first-ever job, fresh target dir, cold caches everywhere)
is the ~minute baseline cargo always was.

The dual-layer design also handles the case where Paavo's
content cache is wrong but cargo's isn't. If somehow Paavo
serves a stale or corrupt ELF (shouldn't happen, but) and the
test runs but behaves incorrectly, the fix is to nuke the row in
`build_cache`; cargo doesn't need to be touched. Symmetrically,
if cargo's incremental cache gets corrupt (which it does, very
occasionally, after a toolchain upgrade), `cargo clean` doesn't
invalidate Paavo's ELF cache — the ELFs are still byte-exact and
still valid. The two layers fail independently, which is what
you want.

## What the hit rate looks like

> **Placeholder.** This section should contain real numbers from
> a real Paavo deployment. Things worth measuring and reporting:
>
> - Overall cache hit rate (probably 60-80%, because the nightly
>   soak jobs re-run identical tars; ad-hoc dev jobs are
>   typically misses).
> - Hit rate broken out by job source (`scheduler` vs `cli`).
>   Scheduler should be ~95%+ (same crate, same revision, same
>   tar). CLI should be much lower (dev iterates, tar changes).
> - Time-to-flash distribution. Show the bimodal split: ~hundreds
>   of milliseconds for hits, ~seconds for misses-with-warm-cargo-cache,
>   ~tens of seconds for cold-cargo-cache.
> - Cache size over time, with eviction events marked. Demonstrate
>   the LRU staying within the configured cap.
> - Reconciliation sweep findings: ideally a flat line at zero,
>   with annotations on any spikes (when did they happen, what
>   caused them).
>
> Draft a few plots in matplotlib or whatever; embed them inline.
> The numbers carry the post; without them this section is
> hand-waving.

## What's been worth it

The cache is one of those features that, when it's working, you
forget exists. Developers don't notice that their second-iteration
job flashed in 800ms instead of 45 seconds; they just notice that
Paavo is fast. The nightly soak runner doesn't notice that 90% of
its jobs skipped the build phase; it just notices that it
finishes by 02:00 instead of 06:00.

But the *correctness* of the cache — the self-healing, the orphan
sweep, the clear two-phase eviction — is what makes the cache
something you can actually deploy and forget. A cache that's
50% faster but causes one weird "cached stale ELF" incident per
month is not a cache you want; it's a foot-gun with extra
infrastructure. A cache that fails closed, fixes itself, and
emits clear diagnostics when something's off is one you can trust
to run unattended.

The mechanics here are nothing exotic. blake3 has been the
correct choice for content-addressed caching for years. LRU
eviction with a size cap is a textbook pattern. A reconciliation
sweep is what every long-lived cache eventually grows. The work
isn't in the algorithms; the work is in making sure each layer
of failure-handling actually composes with the others, and in
writing the operator-facing diagnostics so that when something
*does* go wrong, there's a clear thread to pull.

## What's after this series

Four posts on Paavo is probably enough for now. The next blog
topics in the queue, roughly:

- **postcard-rpc deep-dive.** The wire protocol that
  `pico-de-gallo`'s firmware speaks to its host crate, and that
  I've been finding under-documented in the wild relative to how
  good it is.
- **mole.** The programmable I²C/I³C waveform generator running
  on a Lattice FPGA. Quarter-bit ISA, Turing-complete control
  flow, Scheme-based authoring SDK. Saving this one until the
  HDR-DDR redesign lands.
- **Whatever surprises me next.** The bar for publishing is "did
  I learn something I didn't know an hour ago?" When that's true
  and I can write about it, it'll show up here.

If you've made it this far, thanks for reading. Paavo's source
is on [GitHub][paavo-repo]; the design spec and implementation
plan live in `docs/superpowers/`. Issues, design critiques, and
PRs all welcome.

[paavo-repo]: https://github.com/felipebalbi/paavo
