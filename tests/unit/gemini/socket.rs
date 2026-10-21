//! The `tests` module of `src/gemini/socket.rs`, which declares this file by
//! path. A TLS server here cannot carry a certificate the bundled roots trust,
//! so the tunnel is checked under a plain WebSocket and the proxy choice on the
//! `wss://` URL apart from it.

use super::*;
use futures_util::{SinkExt, StreamExt};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{accept_async, client_async};

fn proxies(proxy: &str) -> Matcher {
    Matcher::builder()
        .https(proxy)
        .no("excluded.example")
        .build()
}

fn intercept(proxy: &str) -> Intercept {
    proxy_for(&"wss://live.example/ws".parse().unwrap(), &proxies(proxy)).unwrap()
}

/// A proxy that records the request head, answers with `reply`, and on a
/// 200 serves the WebSocket itself, echoing one message.
async fn proxy_fixture(reply: &'static str) -> (String, tokio::task::JoinHandle<Vec<String>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (socket, _) = listener.accept().await.unwrap();
        let mut reader = BufReader::new(socket);
        let mut head = Vec::new();
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).await.unwrap();
            if line == "\r\n" {
                break;
            }
            head.push(line.trim_end().to_string());
        }
        let mut socket = reader.into_inner();
        socket.write_all(reply.as_bytes()).await.unwrap();
        if reply.starts_with("HTTP/1.1 200") {
            let mut socket = accept_async(socket).await.unwrap();
            let message = socket.next().await.unwrap().unwrap();
            socket.send(message).await.unwrap();
        }
        head
    });
    (format!("http://user:secret@{address}"), server)
}

#[test]
fn only_secure_sockets_outside_no_proxy_are_proxied() {
    let proxies = proxies("http://127.0.0.1:3128");
    let chosen = |url: &str| proxy_for(&url.parse().unwrap(), &proxies);
    let proxy = chosen("wss://generativelanguage.googleapis.com/ws/live?key=k").unwrap();
    assert_eq!(proxy.uri().authority().unwrap().as_str(), "127.0.0.1:3128");
    assert!(chosen("wss://excluded.example/ws").is_none());
    assert!(chosen("ws://127.0.0.1:9000/ws").is_none());
}

#[tokio::test]
async fn the_tunnel_carries_the_socket_after_an_authorized_connect() {
    let (proxy, server) = proxy_fixture("HTTP/1.1 200 Connection established\r\n\r\n").await;
    let target = "wss://live.example/ws".parse().unwrap();
    let stream = tunnel(&intercept(&proxy), &target).await.unwrap();
    let (mut socket, _) = client_async("ws://live.example/ws", stream).await.unwrap();
    socket.send(Message::Text("ping".into())).await.unwrap();
    assert_eq!(
        socket.next().await.unwrap().unwrap(),
        Message::Text("ping".into())
    );
    let head = server.await.unwrap();
    assert_eq!(head[0], "CONNECT live.example:443 HTTP/1.1");
    assert!(head.contains(&"Host: live.example:443".to_string()));
    // user:secret
    assert!(head.contains(&"Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=".to_string()));
}

#[tokio::test]
async fn a_refused_tunnel_says_why() {
    for (reply, reason) in [
        (
            "HTTP/1.1 407 Proxy Authentication Required\r\n\r\n",
            "proxy authorization required",
        ),
        ("HTTP/1.1 403 Forbidden\r\n\r\n", "unsuccessful"),
    ] {
        let (proxy, server) = proxy_fixture(reply).await;
        let target = "wss://live.example/ws".parse().unwrap();
        let error = tunnel(&intercept(&proxy), &target).await.unwrap_err();
        assert!(error.to_string().contains(reason), "{error}");
        server.await.unwrap();
    }
}

#[tokio::test]
async fn an_unsupported_proxy_is_refused_before_connecting() {
    let target = "wss://live.example/ws".parse().unwrap();
    let error = tunnel(&intercept("https://127.0.0.1:1"), &target)
        .await
        .unwrap_err();
    assert!(error.to_string().contains("not https://"), "{error}");
}
