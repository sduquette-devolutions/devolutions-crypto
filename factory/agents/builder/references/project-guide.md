# Project guide: devolutions-crypto

## Layout

| Path | Content |
|------|---------|
| `src/` | Core Rust library (`devolutions_crypto` crate) |
| `src/lib.rs` | Public API surface and module declarations |
| `src/<feature>/` | One module per feature: `ciphertext`, `key`, `password_hash`, `secret_sharing`, `signature`, `signing_key`, `online_ciphertext`, `key_derivation`, `derive_encrypt` |
| `src/header.rs`, `src/enums.rs` | Binary header format and the version/subtype enums |
| `src/error.rs` | Crate-wide `Error` type |
| `tests/` | Integration tests (`conformity.rs` holds fixed vectors that guarantee backward compatibility) |
| `cli/`, `ffi/`, `python/`, `uniffi/`, `wrappers/` | Bindings for other languages |
| `fuzz/` | cargo-fuzz targets |

## Build and test

- Build: `cargo build` from the repository root.
- Test: `cargo test` from the repository root.
- Lint (CI enforces it): `cargo fmt --all -- --check` and
  `cargo clippy --workspace --all-targets -- -D warnings`.
- Edition 2024.

## Conventions

- Every serialized object starts with a `Header` (signature, data type,
  subtype, version). Adding a new version means adding a new enum variant and a
  new module file (e.g. `ciphertext_v3.rs`); never change how an existing
  version serializes or parses.
- Backward compatibility is mandatory: data produced by older versions must
  keep decrypting/verifying. `tests/conformity.rs` must never be edited to
  accept different output for an existing version.
- Public functions return `Result<T, devolutions_crypto::Error>`. Do not
  `unwrap()`/`panic!` on attacker-controlled input.
- Secrets should be zeroized (`zeroize` crate) when dropped.
- Compare MACs/secrets with constant-time comparisons (`subtle`).
- Unit tests live in a `#[cfg(test)] mod tests` at the bottom of the module
  file; cross-module or API-level tests go in `tests/`.
- If you change the public API, update `CHANGELOG.md` under an "Unreleased"
  section.
