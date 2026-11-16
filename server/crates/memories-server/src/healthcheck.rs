//! Health check for the image. The compose files of current installs run
//! `python -c "...urlopen('http://localhost:8000/health'...)"`, so the image
//! links `python` to this binary and the binary runs this check.

use std::io::{self, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

const DEFAULT_URL: &str = "http://localhost:8000/health";
const TIMEOUT: Duration = Duration::from_secs(3);

pub struct Response {
    pub status: u16,
    pub content_type: String,
    pub body: String,
}

/// Returns the first `http://` URL in the arguments, up to a quote or a space.
pub fn url_from_args(args: &[String]) -> String {
    args.iter()
        .find_map(|a| {
            let start = a.find("http://")?;
            let rest = &a[start..];
            let end = rest
                .find(|c: char| c == '\'' || c == '"' || c.is_whitespace())
                .unwrap_or(rest.len());
            Some(rest[..end].to_string())
        })
        .unwrap_or_else(|| DEFAULT_URL.to_string())
}

/// Exit code: 0 if the URL answers with a status from 200 to 399, else 1.
/// Exit code 127 for each call that is not `python -c "...urlopen(...)..."`.
pub fn run(args: &[String]) -> i32 {
    let is_check = args.get(1).is_some_and(|a| a == "-c")
        && args.get(2).is_some_and(|code| code.contains("urlopen("));
    if !is_check {
        eprintln!("memories image: python is a health check shim only, not a Python interpreter");
        return 127;
    }
    match get(&url_from_args(args)) {
        Ok(r) if (200..400).contains(&r.status) => 0,
        _ => 1,
    }
}

/// HTTP/1.0 GET with a 3 s limit for each step. Supports `http://` only.
pub fn get(url: &str) -> io::Result<Response> {
    let invalid = || io::Error::new(io::ErrorKind::InvalidInput, url.to_string());
    let rest = url.strip_prefix("http://").ok_or_else(invalid)?;
    let (authority, path) = rest.split_at(rest.find('/').unwrap_or(rest.len()));
    let path = if path.is_empty() { "/" } else { path };
    let addr_text = if authority.contains(':') {
        authority.to_string()
    } else {
        format!("{authority}:80")
    };

    let mut last_err = invalid();
    let mut stream = None;
    for addr in addr_text.to_socket_addrs()? {
        match TcpStream::connect_timeout(&addr, TIMEOUT) {
            Ok(s) => {
                stream = Some(s);
                break;
            }
            Err(e) => last_err = e,
        }
    }
    let mut stream = stream.ok_or(last_err)?;
    stream.set_read_timeout(Some(TIMEOUT))?;
    stream.set_write_timeout(Some(TIMEOUT))?;
    write!(
        stream,
        "GET {path} HTTP/1.0\r\nHost: {authority}\r\nConnection: close\r\n\r\n"
    )?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw)?;

    let (head, body) = raw.split_once("\r\n\r\n").ok_or_else(invalid)?;
    let mut lines = head.lines();
    let status = lines
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .ok_or_else(invalid)?;
    let content_type = lines
        .filter_map(|l| l.split_once(':'))
        .find(|(k, _)| k.eq_ignore_ascii_case("content-type"))
        .map(|(_, v)| v.trim().to_string())
        .unwrap_or_default();
    Ok(Response {
        status,
        content_type,
        body: body.to_string(),
    })
}
