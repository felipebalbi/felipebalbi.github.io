# planned-posts

Drafts for upcoming posts. This directory sits **outside** Zola's
`content/` tree so the live site doesn't render it. Drafts live here
until they're ready to move into `content/posts/<slug>/index.md`.

## Promotion checklist

When a draft is ready to publish:

1. `mkdir content/posts/<slug>/`
2. `mv planned-posts/<slug>.md content/posts/<slug>/index.md`
3. Update the `date = ...` front matter to the actual publish date.
4. Drop any referenced images into `content/posts/<slug>/`.
5. `zola serve` and proof-read locally.
6. `git add content/posts/<slug>/` and commit.
7. Push, wait for the GitHub Action to deploy.
8. Open a TWiR PR (`rust-lang/this-week-in-rust`) adding the post link
   to the appropriate section (`Crate of the Week` is reserved; use
   `Read of the Week`-ish sections for write-ups).

## Current series

**Paavo** — a four-post series introducing the HIL test runner, in the
recommended publish order:

| # | Slug                                 | Status     |
|---|--------------------------------------|------------|
| 1 | `paavo-introducing`                  | drafted    |
| 2 | `paavo-watchdog-four-outcomes`       | drafted    |
| 3 | `paavo-elf-metadata-linker-fragment` | drafted    |
| 4 | `paavo-content-addressed-build-cache`| drafted    |

Post 4 is gated on M3.2.e + M4 landing (real cache numbers required).
Posts 1–3 can publish on the established 2-week cadence as-is.
