// SPIKE #1 — proxy fidelity.
// A minimal HTTP-aware reverse proxy: listens on :11435, forwards every request
// to Ollama on :11434, and STREAMS the response body back chunk-by-chunk.
// Goal: prove that an HTTP-aware proxy (the shape agentosd needs, so it can read
// requests to inject priority/queue) does NOT break streaming SSE or tool-calls.
//
// If a request streams identically through here as it does direct, the riskiest
// assumption under the enforcing-gateway design holds.

use axum::{
    body::Body,
    extract::{Request, State},
    response::Response,
    Router,
};
use std::net::SocketAddr;

#[derive(Clone)]
struct AppState {
    client: reqwest::Client,
    upstream: String,
}

#[tokio::main]
async fn main() {
    let state = AppState {
        client: reqwest::Client::new(),
        upstream: "http://127.0.0.1:11434".to_string(),
    };

    let app = Router::new().fallback(proxy).with_state(state);
    let addr = SocketAddr::from(([127, 0, 0, 1], 11435));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    println!("proxy-fidelity: listening on http://{addr} -> {}", "127.0.0.1:11434");
    axum::serve(listener, app).await.unwrap();
}

async fn proxy(State(st): State<AppState>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let path_q = parts
        .uri
        .path_and_query()
        .map(|p| p.as_str())
        .unwrap_or("/");
    let url = format!("{}{}", st.upstream, path_q);

    // Buffer the request body (chat requests are small; the client sends a
    // complete JSON and then receives a stream — so this is faithful).
    let body_bytes = match axum::body::to_bytes(body, usize::MAX).await {
        Ok(b) => b,
        Err(e) => return err_response(400, format!("read request body: {e}")),
    };

    let mut rb = st.client.request(parts.method, &url).body(body_bytes);
    for (k, v) in parts.headers.iter() {
        if k == axum::http::header::HOST {
            continue;
        }
        rb = rb.header(k, v);
    }

    let upstream = match rb.send().await {
        Ok(r) => r,
        Err(e) => return err_response(502, format!("upstream error: {e}")),
    };

    let status = upstream.status();
    let headers = upstream.headers().clone();
    let stream = upstream.bytes_stream();

    let mut out = Response::builder().status(status);
    for (k, v) in headers.iter() {
        // Drop hop-by-hop / framing headers — let the server re-frame the
        // re-streamed body so chunked SSE isn't double-encoded.
        match k.as_str() {
            "content-length" | "transfer-encoding" | "connection" => continue,
            _ => out = out.header(k, v),
        }
    }
    out.body(Body::from_stream(stream)).unwrap()
}

fn err_response(code: u16, msg: String) -> Response {
    Response::builder()
        .status(code)
        .body(Body::from(msg))
        .unwrap()
}
