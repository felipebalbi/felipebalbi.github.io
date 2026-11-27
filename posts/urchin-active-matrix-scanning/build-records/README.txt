Prepared firmware build records

These records describe the four locally prepared erase-enabled test image
sets. All use Urchin `580db2ce97625ae9bad5073990d15618a6fb16e8` and published RMK
`0d9c302094dfc2aacc62263d09b19f7013e18611`, built with Rust 1.100.0.

Each directory contains:

- `BUILD.txt`: selected features, revisions, compiler and command.
- `keyboard-erase.toml`: the configuration used by the macro, including
  `[storage] clear_storage = true`.
- `SHA256SUMS`: the original prepared-set checksums, including firmware
  artifacts and build/config/instruction files.

The original four sets' binaries and `FLASH.txt` are not bundled here. Therefore the
checksum lists cannot be checked in full within this blog bundle; they are
fingerprints for matching the original build output, not a claim that the
exact flashed image was verified for each hardware capture.

The original commands used `/home/balbi/workspace/rmk-urchin` paths and a
temporary configuration path; those paths must be adapted on another host.
`clear_storage` clears all RMK storage on every boot, including bonds and
saved settings. These are not persistent-pairing production images.

The fifth set, `erase-pacing-only`, also starts from those revisions, but
uses a frozen local source override that restores the original settling
timer while retaining pacing. Its `pacing-only.patch`, modified matrix
source, manifests, lockfile and flashing instructions are included. Its
configuration is named `keyboard.toml`. Its checksum list also references
the original SOURCE.tar.gz and firmware binaries, which are not bundled.
