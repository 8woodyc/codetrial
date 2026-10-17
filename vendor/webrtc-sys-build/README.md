Vendored from [`webrtc-sys-build` 0.3.19](https://crates.io/crates/webrtc-sys-build),
upstream revision `2d9f01ab1e933a86a8a5c53805ee29ee58b9be1b`,
`webrtc-sys/build/`. Licensed under Apache-2.0; see LICENSE.

Only the ZIP dependency changes: 0.6 to 9.0, with just the zlib-rs
Deflate decoder since the helper only extracts, fixing RUSTSEC-2026-0367,
RUSTSEC-2026-0368 and RUSTSEC-2026-0369. The upstream Rust source is unchanged.
Remove the Cargo patch and this directory when upstream releases a build
helper using ZIP 9 or later.
