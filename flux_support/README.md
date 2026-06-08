Helper Flux functions

## The Flux compiler

This repository pins the [Flux](https://github.com/flux-rs/flux) verifier as a
git submodule at `flux/` (tracking upstream `flux-rs/flux`, not a personal
fork). The `flux-rs` / `flux-core` crates referenced by the workspace
`Cargo.toml` files resolve through relative path dependencies into that
submodule (e.g. `../flux/lib/flux-rs`), so there are no machine-specific
absolute paths to edit.

### Setup

After cloning Tock, initialize the submodule:

```sh
git submodule update --init
```

To build and install the pinned Flux compiler so you can run `cargo flux`
locally:

```sh
cd flux
cargo x install   # builds + installs flux / cargo-flux onto PATH
```

Flux ships its own `rust-toolchain.toml`; running `cargo x install` from inside
`flux/` uses it automatically. The invariant-checking CI
(`.github/workflows/flux-invariant2.yml`) follows the same flow against the
pinned submodule commit.

### Updating Flux

Advance the pinned commit with:

```sh
cd flux && git fetch origin && git checkout origin/main
cd .. && git add flux && git commit -m "flux: bump submodule"
```
