Vendored from [`tokio-tungstenite` 0.30.0](https://crates.io/crates/tokio-tungstenite),
upstream revision `4994a078037a18a9347b8cf49291a5031d63d9e0`.
Licensed under MIT; see LICENSE. The upstream README is README.upstream.md.

Only the webpki-roots dependency changes: 0.26 to 1.0.10, fixing
RUSTSEC-2026-0381. The upstream Rust source is unchanged. Version 0.26.11
re-exports the version 1 trust anchors, so this removes the compatibility
wrapper while retaining its API and the patched certificate set.
Remove the Cargo patch and this directory when upstream releases a transport
using webpki-roots 1.0.10 or later.
