Vendored from [`livekit-net` 0.1.4](https://crates.io/crates/livekit-net),
upstream revision `fa46e8427050b6ee0c6e40670ff161ee3a2bbf5a`, `livekit-net/`.
Licensed under Apache-2.0; see LICENSE.

The tokio-tungstenite dependency changes from 0.29 to 0.30, including its
upstream tests. The unused direct bytes dependency is removed. The companion
tokio-tungstenite patch moves its bundled certificate roots
to webpki-roots 1, fixing RUSTSEC-2026-0381 without changing either transport
implementation.
Remove the Cargo patch and this directory when upstream releases a network
transport using tokio-tungstenite 0.30 or later.
