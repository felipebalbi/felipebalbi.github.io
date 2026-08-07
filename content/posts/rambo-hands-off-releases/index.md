+++
title = "rambo v0.1.2: the boring release is the point"
date = 2026-08-07T09:00:00
draft = false
description = "rambo v0.1.2 ships no new features on purpose. It's the release plumbing that makes the next feature ship itself: every tag now builds pre-built binaries for Linux, macOS, and Windows, and dependabot keeps the supply chain fresh."
[taxonomies]
tags = ["rust", "embedded", "rambo", "release", "ci"]
+++

[rambo][rambo] — the CLI that maps which of a Cortex-M's SRAM a boot ROM
clobbers before your firmware's first instruction runs — is at v0.1.2.
If you're looking for a new feature, there isn't one:

```
### Other
- (deps) bump dependencies
- add dependabot configuration
- chain release binaries from release-plz workflow
- allow manual dispatch of release workflow
```

Every flag you might reach for --- the `--json` reports, the
`--expectations` RAM contracts, custom chip descriptions --- shipped
back in v0.1.1, which I [already wrote up][intro]. v0.1.2 is pure
plumbing, and that's the point.

<!-- more -->

The one change you'll actually notice: **every tagged release now ships
pre-built binaries** for Linux, macOS (Intel and Apple Silicon), and
Windows, built and attached automatically. No Rust toolchain required to
run rambo — download, unzip, go.

Under the hood that's the whole release pipeline earning its keep. I
write [conventional commits][cc]; [release-plz] keeps a "Release X.Y.Z"
pull request open with the version bump and changelog already written for
me. Merging it publishes to crates.io, tags the release, and — new in
v0.1.2 — kicks off the per-OS binary build and uploads the archives to
that release. A manual trigger lets me rebuild those binaries for an
existing tag if a run ever fails, without cutting a new version.

The other half of "boring" is a dependabot config that files one
grouped, conventional-commit pull request a week for Rust and Actions
updates, so it folds into a clean patch bump instead of a pile of noise.
Most of what changed between v0.1.1 and v0.1.2 is exactly that:
`actions/checkout` and `action-gh-release` bumped themselves and I just
merged.

None of this is novel. It matters because a one-person tool doesn't die
from bad code — it dies when every release costs twenty minutes of
bookkeeping and one day you don't have twenty minutes. v0.1.2 drops that
cost to roughly zero, so the next release, the one with actual features
in it, is one I'll actually ship.

rambo is on [crates.io][crate] (`cargo install rambo`) and
[GitHub][repo]; grab a binary from [the releases page][releases],
including this boring one.

[rambo]: https://github.com/felipebalbi/rambo
[repo]: https://github.com/felipebalbi/rambo
[crate]: https://crates.io/crates/rambo
[releases]: https://github.com/felipebalbi/rambo/releases
[intro]: /posts/rambo-rom-collateral-damage/
[release-plz]: https://release-plz.dev
[cc]: https://www.conventionalcommits.org/
