+++
title = "Embedding metadata in ELF: paavo-meta's macros and the linker fragment"
date = 2026-07-22T08:00:00
description = "Three macros, one linker fragment, a cfg_attr trick that lets the same crate compile on the host and on Cortex-M, and a parser that treats missing sections as fine but malformed ones as crimes."
[taxonomies]
tags = ["embedded", "rust", "paavo", "elf", "linker", "macros", "no-std"]
+++

Paavo needs three pieces of per-test metadata to do its job:

1. Which board this test targets (`mcxa266`, `rt685-evk`, etc.) so
   the scheduler can pick the right hardware.
2. The hard-max wall-clock budget for this test, so the watchdog
   knows when to give up.
3. The inactivity-timeout override for this test, when 120 seconds
   isn't the right answer.

All three pieces have to travel with the test ELF. If they live in
a separate config file, somebody will edit one and forget the
other, and the system will silently flash an mcxa266 test onto an
rt685 board. That's the kind of bug that wastes an afternoon
because every layer above it is doing the right thing with wrong
inputs.

The right place for this metadata is inside the test binary
itself. Specifically, inside a few extra ELF sections that get
written at compile time, preserved by the linker, and read back
out by the host-side runner before flashing. The Embassy folks
solved this for their [teleprobe] runner with three macros —
`target!()` and `timeout!()` — and the structure they came up
with is excellent. Paavo's version is the same structure with one
new macro added (`inactivity_timeout!()`), a self-contained
implementation that doesn't depend on upstream `teleprobe-meta`,
and a `cfg_attr` trick that lets the same crate compile both for
the embedded target and for the host workspace.

This post is about the implementation.

[teleprobe]: https://github.com/embassy-rs/teleprobe

## What test source looks like

Here is a representative Embassy test, abbreviated:

```rust
#![no_std]
#![no_main]

use {defmt_rtt as _, panic_probe as _};

paavo_meta::target!(b"frdm-mcx-a266");
paavo_meta::timeout!(60);
paavo_meta::inactivity_timeout!(30);

#[embassy_executor::task]
async fn run(spawner: Spawner) {
    // ... actual test logic ...
    defmt::info!("Test OK");
    cortex_m::asm::bkpt();
}

#[cortex_m_rt::entry]
fn main() -> ! {
    let spawner = embassy_executor::Spawner::for_current_executor();
    spawner.spawn(run(spawner)).unwrap();
    loop {}
}
```

The three macros are top-level, called once each, before `main`.
Each one expands to a `#[no_mangle]` static that the compiler
emits into a specific ELF section. Paavo's host-side runner reads
those sections out of the linked binary, validates them, and uses
them to drive the dispatcher.

The runtime overhead is zero. The macros don't run code at
startup; they just plant data in the ELF. The chip never reads
them. The compiler can't dead-code-eliminate them because they're
`#[no_mangle]` and referenced by name from the linker fragment.

## What each macro expands to

The three macro bodies are nearly identical. Here's `target!()`:

```rust
#[macro_export]
macro_rules! target {
    ($value:expr) => {
        #[used]
        #[cfg_attr(target_os = "none", link_section = ".paavo.target")]
        #[cfg_attr(not(target_os = "none"), link_section = ".rodata.paavo_meta_target")]
        #[no_mangle]
        pub static PAAVO_TARGET: [u8; { $value.len() }] = *$value;
    };
}
```

It expands to a single `#[no_mangle]` static byte array, with three
attributes that matter:

- **`#[used]`** tells the compiler not to drop the static even
  though nothing in the crate references it. Without this, the
  optimizer would notice that `PAAVO_TARGET` has no users and
  remove it before the linker ever saw it.
- **`#[no_mangle]`** keeps the symbol name stable so the linker
  fragment's `KEEP(*(.paavo.target))` can reach it by section
  name (more on that below). It also means two invocations of
  `target!()` in the same crate produce a duplicate-symbol error
  at link time — which is correct; a test should declare exactly
  one target.
- **`#[cfg_attr(...)]`** — the two arms here are the load-bearing
  trick. We'll come back to it.

The other two macros are the same shape with different section
names and a u32 value:

```rust
#[macro_export]
macro_rules! timeout {
    ($seconds:expr) => {
        #[used]
        #[cfg_attr(target_os = "none", link_section = ".paavo.timeout")]
        #[cfg_attr(not(target_os = "none"), link_section = ".rodata.paavo_meta_timeout")]
        #[no_mangle]
        pub static PAAVO_TIMEOUT: [u8; 4] = ($seconds as u32).to_le_bytes();
    };
}
```

Two details about that body worth pointing out.

First, the value is `[u8; 4]`, not `u32`. The host-side parser
reads raw bytes from the ELF section and decodes them with
`u32::from_le_bytes`. Storing the bytes directly removes any
question about how the compiler chose to lay out a `u32` — no
padding, no alignment slack, no ambiguity. The section is exactly
four bytes; if it isn't, the parser knows something's wrong.

Second, `.to_le_bytes()` is explicit. Cortex-M is little-endian
in practice, so `.to_le_bytes()` on the target produces the same
bytes as `.to_ne_bytes()` would, but the macro is going to expand
in the *test crate*, which is `#![no_std]` and could in principle
be cross-compiled to a big-endian target someday. Pinning to
little-endian in the macro body means the on-ELF wire format is
the same regardless of what target the crate compiles for. The
host parser doesn't have to care.

## The `cfg_attr` trick

Here's the load-bearing part. Why are there two `#[cfg_attr]`
attributes per static, one for `target_os = "none"` and one for
everything else?

The intended deployment is straightforward: the test crate is
cross-compiled to `thumbv8m.main-none-eabihf` (or similar), and
the macros emit data into the `.paavo.*` sections, and the linker
fragment preserves those sections through to the final ELF.
That's the `target_os = "none"` arm.

The trouble is that Paavo's *workspace* also wants to compile
`paavo-meta` — to build the doctests, to run the macro-expansion
smoke test, to make sure the crate works at all. On the host
(`target_os = "linux"`, `"macos"`, `"windows"`), `cargo test`
runs against the host toolchain, with the host linker. And here
is the inconvenient fact: **most host linkers will reject a
custom `.paavo.*` section name they don't recognize**, or accept
it but emit warnings that fail under `-D warnings`. Apple's `ld`
in particular is strict about Mach-O segment naming and won't
silently allow arbitrary names.

The workaround is to give each static a different section on the
host. `.rodata.paavo_meta_target` is just a sub-name under the
standard `.rodata` section, which every linker knows about and
happily places into the read-only data segment. The host build
puts the static somewhere innocuous; nobody looks at it; the
crate compiles.

The cross-compiled embedded build (`target_os = "none"`) gets
the real `.paavo.target` section, which the linker fragment
preserves explicitly. The runtime on the chip never reads it.
Paavo's host-side runner reads it from the linked ELF before
flashing.

Same source, two valid linker outputs, depending on target.

## The linker fragment

The `cfg_attr` trick gets the data into the ELF on the embedded
build, but it doesn't keep it there. By default, `ld.lld` will
happily drop any section that isn't reachable from a memory
region or named in the link script. The data has to be
explicitly preserved.

That's what `paavo.x` is for. It's 13 lines:

```
/* paavo-meta linker fragment. Preserves the .paavo.* ELF sections
 * emitted by target!(), timeout!(), and inactivity_timeout!() so that
 * paavo-probe can read them out of the linked binary. */
SECTIONS
{
    .paavo (INFO) :
    {
        KEEP(*(.paavo.target))
        KEEP(*(.paavo.timeout))
        KEEP(*(.paavo.inactivity_timeout))
    }
}
INSERT AFTER .text;
```

Three things to call out:

**`KEEP(...)`** is the bit that actually preserves the sections.
Without it, the linker's garbage-collection pass (`--gc-sections`,
which the embedded build always uses to strip dead code) would
notice that `.paavo.target` has no users and drop it. `KEEP`
tells the GC pass "I know this looks dead; keep it anyway."

**`(INFO)`** is the section type. It means "this section carries
information that isn't loaded into memory at runtime." The chip
never sees these sections; they exist only in the ELF file on
disk. That's what we want — the metadata is for the host runner,
not for the chip.

**`INSERT AFTER .text`** tells the linker *where* in the output
layout to place this section group. After `.text` is fine; we
don't actually care about the position, because nothing references
these sections by address. The `INSERT AFTER` clause is what makes
this file a *fragment*: it doesn't override the main link script
(usually `link.x` from `cortex-m-rt`), it just adds onto it. You
include it from the test crate's build by adding `-Tpaavo.x` to
RUSTFLAGS, after `-Tlink.x`.

The build.rs in `paavo-meta` copies `paavo.x` into `OUT_DIR` and
emits `cargo:rustc-link-search={OUT_DIR}` so downstream crates can
just write `-Tpaavo.x` in their `.cargo/config.toml` and have it
resolve. Standard pattern, borrowed directly from `cortex-m-rt`.

## The parser

Once the test ELF is linked, the host-side runner needs to read
the metadata back out. That's `paavo-probe`'s job. The parser is
about 100 lines, and the interesting part isn't the parsing — it's
the **philosophy of what to do when something's wrong**.

The signature is straightforward:

```rust
pub struct MetaSections {
    pub target: Option<String>,
    pub timeout_s: Option<u32>,
    pub inactivity_timeout_s: Option<u32>,
}

pub fn parse_meta_sections(elf: &[u8]) -> Result<MetaSections>;
```

It uses the [`object`] crate to open the ELF and look up sections
by name. For each of the three section names, the parser does
one of three things:

1. **Section is absent.** The corresponding field on
   `MetaSections` stays `None`. This is a valid outcome.
   `target!()` is required in practice — `paavod` rejects a job
   if `MetaSections.target` is `None` — but the *parser* doesn't
   know that, because the parser's job is to read what's there,
   not enforce policy.
2. **Section is present and well-formed.** The field gets `Some(_)`
   with the decoded value.
3. **Section is present but malformed.** The parser returns an
   `Err` with a specific diagnostic. This is the part worth
   talking about.

[`object`]: https://crates.io/crates/object

## Missing is fine; malformed is a crime

The `.paavo.target` section is supposed to be a NUL-terminated
UTF-8 byte string. There are four ways it can be wrong, and the
parser distinguishes all four:

```rust
fn parse_cstring(bytes: &[u8]) -> Result<String> {
    if bytes.is_empty() {
        return Err(ProbeError::EmptyTarget);
    }
    let Some(nul_at) = bytes.iter().position(|&b| b == 0) else {
        return Err(ProbeError::MalformedTarget {
            reason: "missing trailing NUL".into(),
        });
    };
    if nul_at + 1 != bytes.len() {
        return Err(ProbeError::MalformedTarget {
            reason: format!(
                "interior NUL at byte {} with {} trailing bytes after",
                nul_at,
                bytes.len() - nul_at - 1,
            ),
        });
    }
    str::from_utf8(&bytes[..nul_at])
        .map(String::from)
        .map_err(|e| ProbeError::MalformedTarget {
            reason: format!("invalid UTF-8 at byte {}", e.valid_up_to()),
        })
}
```

Empty section → `EmptyTarget`. No NUL anywhere → `MalformedTarget {
reason: "missing trailing NUL" }`. NUL in the middle with extra
bytes after → `MalformedTarget` with the offset and count.
Invalid UTF-8 → `MalformedTarget` with the byte where the decode
failed.

Why bother distinguishing? Because each one points at a different
bug:

- *Empty* means somebody wrote `target!(b"")`. Caller error.
- *Missing trailing NUL* means the macro is broken — every
  documented invocation pattern produces a NUL-terminated string.
  If this fires, somebody changed the macro and didn't notice.
- *Interior NUL with trailing bytes* means somebody passed a
  byte literal with an embedded `\0`. Probably accidental;
  probably surprising the caller.
- *Invalid UTF-8* means somebody passed raw bytes that aren't a
  valid string. Surprising and should be loud.

The runner surfaces the specific reason in the job's error log.
The next time someone sees `Failed { InfraErr { stage:
"elf_parse", message: "interior NUL at byte 5 with 3 trailing
bytes after" } }`, they know exactly where to look. The
alternative — a generic "couldn't parse `.paavo.target`" — would
send them spelunking.

The same philosophy applies to the timeout sections. They're
supposed to be exactly four bytes. If they're three or five,
that's a `BadIntegerSection { section: ".paavo.timeout", got:
3 }`. The parser uses explicit byte indexing for the decode:

```rust
fn parse_u32_le(bytes: &[u8], section_name: &'static str) -> Result<u32> {
    if bytes.len() != 4 {
        return Err(ProbeError::BadIntegerSection {
            section: section_name,
            got: bytes.len(),
        });
    }
    Ok(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}
```

No `try_into().unwrap()`. No truncating cast. The length check
above the `from_le_bytes` call is what makes the explicit
indexing safe, and writing it that way means a future reader can
see at a glance that there's no panic path. Defensive code in a
parser that lives next to a probe driver is worth its weight in
hours-not-spent-debugging.

## Testing without an ARM toolchain

The temptation when writing tests for a parser like this is to
keep a pre-built ARM ELF in `tests/fixtures/` and parse it. That
works but it's fragile: somebody's got to rebuild the fixture
when the test changes, the CI image needs `arm-none-eabi-gcc`,
and every fixture is one more binary artifact in the repo.

The better pattern, borrowed from the `object` crate's own tests,
is to **synthesize ELFs in-process** using `object`'s write API:

```rust
fn synth_elf(sections: &[(&str, &[u8])]) -> Vec<u8> {
    use object::write::*;
    let mut obj = Object::new(
        BinaryFormat::Elf,
        Architecture::Arm,
        Endianness::Little,
    );
    // ... add a minimal .text section ...
    for (name, bytes) in sections {
        let sec_id = obj.add_section(
            Vec::new(),
            name.as_bytes().to_vec(),
            SectionKind::ReadOnlyData,
        );
        let offset = obj.append_section_data(sec_id, bytes, 1);
        obj.add_symbol(Symbol {
            name: format!("sym_{name}").into_bytes(),
            value: offset,
            size: bytes.len() as u64,
            kind: SymbolKind::Data,
            scope: SymbolScope::Linkage,
            weak: false,
            section: SymbolSection::Section(sec_id),
            flags: SymbolFlags::None,
        });
    }
    obj.write().unwrap()
}
```

The test then constructs a tiny ELF with whatever sections it
wants — well-formed, missing, empty, NUL-less, oversized — and
runs `parse_meta_sections` against the raw bytes. No filesystem,
no cross-compiler, no fixtures. The whole test suite runs in
under a hundred milliseconds.

This pattern alone is worth the cost of pulling in the `object`
crate as a dev-dependency. It also means new test cases are
cheap: when a future malformed-section bug is discovered, the
fix is to add a new `synth_elf(&[(".paavo.target", b"...weird
bytes...")])` test that reproduces it. No rebuild step in CI; no
fixture file to commit.

## What you get out of all this

The cumulative effect of three macros, a 13-line linker fragment,
and a 100-line parser is that every test ELF carries its own
deployment metadata. Paavo can look at any binary that came in
over the wire and answer three questions without consulting any
external configuration:

- Is this test for a board I have?
- How long should I let it run before forcing a stop?
- How long should I let it go quiet before assuming it's hung?

If the answers are missing, the test is rejected at dispatch with
a specific error. If they're malformed, the runner says exactly
what's wrong and where. If they're present and valid, the
dispatcher uses them and the scheduler and watchdog do the right
thing.

This is one of those areas where a small amount of careful work
up front (a couple hundred lines of code, a few `cfg_attr`s, one
linker `KEEP`) produces a system that's nearly impossible to
misconfigure later. The metadata can't drift from the test
because it *is* the test. That's worth the effort.

## What's next in this series

The next and last post in the series is about the build cache:
how a blake3 hash of an uploaded tar becomes the key into a
content-addressed ELF cache, how the LRU eviction works, why the
lookup is self-healing when the on-disk file goes missing, and
what the actual cache hit rates look like once Paavo has been
running real jobs for a while. That post is gated on those real
numbers existing, so it'll land later than this one — once the
daemon binary is wired up and there's a fleet of jobs to measure
against.
