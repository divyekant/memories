//! LLM providers for extraction. Port of `llm_provider.py` (v5.19.0).
//!
//! One `Provider` serves `anthropic`, `openai`, `omlx` and `ollama`. `openai`
//! and `omlx` share one OpenAI-compatible client. Each call has a time limit
//! and a maximum of 2 retries.

use std::time::Duration;

use serde_json::{Value, json};

pub const DEFAULT_ANTHROPIC_MODEL: &str = "claude-haiku-5-5";
pub const DEFAULT_OPENAI_MODEL: &str = "gpt-4.1-nano";
pub const DEFAULT_OLLAMA_MODEL: &str = "gemma3:4b";
pub const DEFAULT_OMLX_MODEL: &str = "gemma26b-3bit";
/// Subscription OAuth tokens get 429 on every model except Haiku 4.5 (checked 2026-10-07).
pub const OAUTH_DEFAULT_MODEL: &str = "claude-haiku-4-5-20251001";

const ANTHROPIC_VERSION: &str = "2023-06-01";
const OAUTH_BETAS: &str = "oauth-2025-04-20,interleaved-thinking-2025-05-14";
/// Opus 4.7+ rejects temperature, so the Opus 4 entries are listed one by one.
const SAMPLING_MODEL_PREFIXES: [&str; 8] = [
    "claude-3",
    "claude-haiku-4",
    "claude-sonnet-4",
    "claude-opus-4-0",
    "claude-opus-4-1",
    "claude-opus-4-2025",
    "claude-opus-4-5",
    "claude-opus-4-6",
];
const ANTHROPIC_EFFORT: &str = "low";

/// Time limit for one attempt of a completion call.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(120);
/// Time limit to open a connection, as in the Python SDKs.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
pub const MAX_RETRIES: u32 = 2;
const RETRY_BASE_DELAY: Duration = Duration::from_millis(500);
const MAX_RETRY_AFTER: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Completion {
    pub text: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum ProviderError {
    #[error("{0}")]
    Config(String),
    #[error("LLM request failed: {0}")]
    Transport(String),
    #[error("LLM provider returned HTTP {status}: {body}")]
    Status { status: u16, body: String },
    #[error("LLM reply not understood: {0}")]
    Parse(String),
}

#[derive(Debug, Clone)]
enum Kind {
    Anthropic {
        base_url: String,
        credential: String,
        oauth: bool,
        workspace_id: Option<String>,
    },
    /// `openai` and `omlx`. `omlx` turns off the thinking template and reads `reasoning`.
    OpenAiCompatible {
        base_url: String,
        api_key: String,
        omlx: bool,
    },
    Ollama {
        base_url: String,
    },
}

#[derive(Debug, Clone)]
pub struct Provider {
    name: &'static str,
    model: String,
    kind: Kind,
    http: reqwest::Client,
}

fn trimmed(v: Option<String>) -> Option<String> {
    v.map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

/// Check the scheme as `urlparse` does (case does not matter), and strip the trailing slashes.
fn http_base(url: String, var: &str, hint: &str) -> Result<String, ProviderError> {
    let scheme = url
        .split_once(':')
        .map_or(String::new(), |(s, _)| s.to_ascii_lowercase());
    if (scheme == "http" || scheme == "https") && url[scheme.len()..].starts_with("://") {
        Ok(url.trim_end_matches('/').to_string())
    } else {
        Err(ProviderError::Config(format!(
            "Invalid {var} scheme: '{scheme}'{hint}"
        )))
    }
}

const SCHEME_HINT: &str = " (must be http or https)";

/// Pick the Anthropic credential from ANTHROPIC_AUTH, ANTHROPIC_API_KEY, ANTHROPIC_OAUTH_TOKEN.
fn resolve_anthropic_credential(
    env: &impl Fn(&str) -> Option<String>,
) -> Result<String, ProviderError> {
    let api_key = trimmed(env("ANTHROPIC_API_KEY"));
    let oauth = trimmed(env("ANTHROPIC_OAUTH_TOKEN"));
    let auth = env("ANTHROPIC_AUTH")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    let (var, cred) = match auth.as_str() {
        "" => {
            return api_key.or(oauth).ok_or_else(|| {
                ProviderError::Config(
                    "ANTHROPIC_API_KEY or ANTHROPIC_OAUTH_TOKEN required when EXTRACT_PROVIDER=anthropic"
                        .into(),
                )
            });
        }
        "api_key" => ("ANTHROPIC_API_KEY", api_key),
        "oauth" => ("ANTHROPIC_OAUTH_TOKEN", oauth),
        other => {
            return Err(ProviderError::Config(format!(
                "ANTHROPIC_AUTH must be 'api_key' or 'oauth', got '{other}'"
            )));
        }
    };
    cred.ok_or_else(|| ProviderError::Config(format!("{var} required when ANTHROPIC_AUTH={auth}")))
}

fn sends_temperature(model: &str) -> bool {
    SAMPLING_MODEL_PREFIXES.iter().any(|p| model.starts_with(p))
}

/// The SDK rule: `x-should-retry` first, then 408, 409, 429 and 5xx.
fn retryable(resp: &reqwest::Response) -> bool {
    match resp
        .headers()
        .get("x-should-retry")
        .and_then(|v| v.to_str().ok())
    {
        Some("true") => true,
        Some("false") => false,
        _ => matches!(resp.status().as_u16(), 408 | 409 | 429) || resp.status().is_server_error(),
    }
}

fn header_f64(resp: &reqwest::Response, name: &str) -> Option<f64> {
    resp.headers().get(name)?.to_str().ok()?.trim().parse().ok()
}

/// The SDK rule: `retry-after-ms`, then `retry-after` in seconds, if the value is
/// more than 0 and at most 60 s. Else 0.5 s, then 1 s.
// shortcut: no jitter and no HTTP-date form of retry-after; add them if a provider sends dates.
fn retry_delay(attempt: u32, resp: Option<&reqwest::Response>) -> Duration {
    resp.and_then(|r| {
        header_f64(r, "retry-after-ms")
            .map(|ms| ms / 1000.0)
            .or_else(|| header_f64(r, "retry-after"))
    })
    .filter(|s| *s > 0.0 && Duration::from_secs_f64(*s) <= MAX_RETRY_AFTER)
    .map(Duration::from_secs_f64)
    .unwrap_or(RETRY_BASE_DELAY * 2u32.pow(attempt))
}

impl Provider {
    /// Make a provider from the environment, as `get_provider` does.
    ///
    /// `provider` and `model` replace `EXTRACT_PROVIDER` and `EXTRACT_MODEL` when given.
    /// Returns `Ok(None)` when no provider is set (extraction is off).
    pub fn from_env(
        provider: Option<&str>,
        model: Option<&str>,
        env: impl Fn(&str) -> Option<String>,
    ) -> Result<Option<Self>, ProviderError> {
        let name = provider
            .map(str::to_string)
            .or_else(|| env("EXTRACT_PROVIDER"))
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        if name.is_empty() {
            return Ok(None);
        }
        let model = trimmed(model.map(str::to_string).or_else(|| env("EXTRACT_MODEL")));

        let (name, kind, default_model): (&'static str, Kind, &str) = match name.as_str() {
            "anthropic" => {
                let credential = resolve_anthropic_credential(&env)?;
                let oauth = credential.starts_with("sk-ant-oat01-");
                let base_url = http_base(
                    trimmed(env("ANTHROPIC_BASE_URL"))
                        .unwrap_or_else(|| "https://api.anthropic.com".into()),
                    "ANTHROPIC_BASE_URL",
                    SCHEME_HINT,
                )?;
                let workspace_id = trimmed(env("ANTHROPIC_WORKSPACE_ID"));
                let default = if oauth {
                    OAUTH_DEFAULT_MODEL
                } else {
                    DEFAULT_ANTHROPIC_MODEL
                };
                (
                    "anthropic",
                    Kind::Anthropic {
                        base_url,
                        credential,
                        oauth,
                        workspace_id,
                    },
                    default,
                )
            }
            "openai" => {
                let api_key = trimmed(env("OPENAI_API_KEY")).ok_or_else(|| {
                    ProviderError::Config(
                        "OPENAI_API_KEY required when EXTRACT_PROVIDER=openai".into(),
                    )
                })?;
                let base_url = http_base(
                    trimmed(env("OPENAI_BASE_URL"))
                        .unwrap_or_else(|| "https://api.openai.com/v1".into()),
                    "OPENAI_BASE_URL",
                    SCHEME_HINT,
                )?;
                (
                    "openai",
                    Kind::OpenAiCompatible {
                        base_url,
                        api_key,
                        omlx: false,
                    },
                    DEFAULT_OPENAI_MODEL,
                )
            }
            "omlx" => {
                let base_url = http_base(
                    trimmed(env("OMLX_URL")).unwrap_or_else(|| "http://localhost:11434/v1".into()),
                    "OMLX base_url",
                    "",
                )?;
                let api_key = trimmed(env("OMLX_API_KEY")).unwrap_or_else(|| "12345".into());
                (
                    "omlx",
                    Kind::OpenAiCompatible {
                        base_url,
                        api_key,
                        omlx: true,
                    },
                    DEFAULT_OMLX_MODEL,
                )
            }
            "ollama" => {
                let base_url = http_base(
                    trimmed(env("OLLAMA_URL"))
                        .unwrap_or_else(|| "http://host.docker.internal:11434".into()),
                    "OLLAMA_URL",
                    SCHEME_HINT,
                )?;
                ("ollama", Kind::Ollama { base_url }, DEFAULT_OLLAMA_MODEL)
            }
            // Not used in production (P0-3 baseline), so not ported.
            "chatgpt-subscription" => {
                return Err(ProviderError::Config(
                    "EXTRACT_PROVIDER=chatgpt-subscription is not supported by this server. \
                     Use: anthropic, openai, ollama, or omlx"
                        .into(),
                ));
            }
            other => {
                return Err(ProviderError::Config(format!(
                    "Unknown EXTRACT_PROVIDER: '{other}'. Use: anthropic, openai, ollama, or omlx"
                )));
            }
        };

        let http = reqwest::Client::builder()
            .connect_timeout(CONNECT_TIMEOUT)
            .build()
            .map_err(|e| ProviderError::Config(format!("HTTP client: {e}")))?;
        Ok(Some(Self {
            name,
            model: model.unwrap_or_else(|| default_model.to_string()),
            kind,
            http,
        }))
    }

    /// `from_env` with the process environment.
    pub fn from_process_env() -> Result<Option<Self>, ProviderError> {
        Self::from_env(None, None, |k| std::env::var(k).ok())
    }

    pub fn name(&self) -> &'static str {
        self.name
    }

    pub fn model(&self) -> &str {
        &self.model
    }

    /// All providers support the add/update/delete/no-change decision step.
    pub fn supports_audn(&self) -> bool {
        true
    }

    pub async fn complete(&self, system: &str, user: &str) -> Result<Completion, ProviderError> {
        match &self.kind {
            Kind::Anthropic {
                base_url,
                credential,
                oauth,
                workspace_id,
            } => {
                let mut body = json!({
                    "model": self.model,
                    "max_tokens": 4096, // room for adaptive thinking on newer models
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                });
                if sends_temperature(&self.model) {
                    body["temperature"] = json!(0.0);
                } else {
                    body["output_config"] = json!({"effort": ANTHROPIC_EFFORT});
                }
                // Python adds ?beta=true only when the request path is exactly /v1/messages.
                let has_path = base_url
                    .split_once("://")
                    .is_some_and(|(_, r)| r.contains('/'));
                let url = if *oauth && !has_path {
                    format!("{base_url}/v1/messages?beta=true")
                } else {
                    format!("{base_url}/v1/messages")
                };
                let reply = self
                    .send_json(|| {
                        let mut req = self
                            .http
                            .post(&url)
                            .header("anthropic-version", ANTHROPIC_VERSION);
                        if *oauth {
                            req = req
                                .bearer_auth(credential)
                                .header("anthropic-beta", OAUTH_BETAS);
                        } else {
                            req = req.header("x-api-key", credential);
                            if let Some(ws) = workspace_id {
                                // Keys not scoped to a workspace (e.g. sk-ant-usr-) get 400 without it.
                                req = req.header("anthropic-workspace-id", ws);
                            }
                        }
                        req.json(&body)
                    })
                    .await?;
                let blocks = reply["content"]
                    .as_array()
                    .ok_or_else(|| ProviderError::Parse("no content list".into()))?;
                Ok(Completion {
                    text: blocks
                        .iter()
                        .filter(|b| b["type"] == "text")
                        .filter_map(|b| b["text"].as_str())
                        .collect(),
                    input_tokens: reply["usage"]["input_tokens"].as_u64().unwrap_or(0),
                    output_tokens: reply["usage"]["output_tokens"].as_u64().unwrap_or(0),
                })
            }
            Kind::OpenAiCompatible {
                base_url,
                api_key,
                omlx,
            } => {
                let mut body = json!({
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "model": self.model,
                    "max_tokens": 1024,
                    "temperature": 0.0,
                });
                if *omlx {
                    body["chat_template_kwargs"] = json!({"enable_thinking": false});
                }
                let url = format!("{base_url}/chat/completions");
                let reply = self
                    .send_json(|| self.http.post(&url).bearer_auth(api_key).json(&body))
                    .await?;
                let msg = &reply["choices"][0]["message"];
                if msg.is_null() {
                    return Err(ProviderError::Parse("no choices[0].message".into()));
                }
                let mut text = msg["content"].as_str().filter(|s| !s.is_empty());
                if *omlx {
                    text = text.or_else(|| msg["reasoning"].as_str());
                }
                Ok(Completion {
                    text: text.unwrap_or_default().to_string(),
                    input_tokens: reply["usage"]["prompt_tokens"].as_u64().unwrap_or(0),
                    output_tokens: reply["usage"]["completion_tokens"].as_u64().unwrap_or(0),
                })
            }
            Kind::Ollama { base_url } => {
                // No format:"json": it breaks array extraction. think:false: thinking
                // models (Qwen3.5+) otherwise give empty replies.
                let body = json!({
                    "model": self.model,
                    "system": system,
                    "prompt": user,
                    "stream": false,
                    "think": false,
                    "options": {"temperature": 0.0},
                });
                let url = format!("{base_url}/api/generate");
                let reply = self.send_json(|| self.http.post(&url).json(&body)).await?;
                Ok(Completion {
                    text: reply["response"]
                        .as_str()
                        .ok_or_else(|| ProviderError::Parse("no response field".into()))?
                        .to_string(),
                    input_tokens: reply["prompt_eval_count"].as_u64().unwrap_or(0),
                    output_tokens: reply["eval_count"].as_u64().unwrap_or(0),
                })
            }
        }
    }

    /// True if the provider is reachable and working.
    pub async fn health_check(&self) -> bool {
        let result = match &self.kind {
            Kind::Ollama { base_url } => self
                .http
                .get(format!("{base_url}/api/tags"))
                .timeout(HEALTH_TIMEOUT)
                .send()
                .await
                .map_err(|e| ProviderError::Transport(e.to_string()))
                .and_then(|r| match r.status().as_u16() {
                    200 => Ok(()),
                    s => Err(ProviderError::Status {
                        status: s,
                        body: String::new(),
                    }),
                }),
            _ => self
                .complete("Reply with OK", "health check")
                .await
                .map(|_| ()),
        };
        if let Err(e) = &result {
            tracing::warn!(provider = self.name, error = %e, "LLM health check failed");
        }
        result.is_ok()
    }

    /// Send a request with the time limit and up to `MAX_RETRIES` retries, and read the JSON reply.
    async fn send_json(
        &self,
        build: impl Fn() -> reqwest::RequestBuilder,
    ) -> Result<Value, ProviderError> {
        let mut attempt = 0;
        loop {
            // A slow Ollama generation is not sent again: Python had no retry there,
            // and a retry would use the local GPU 3 times.
            let retry_transport = |e: &reqwest::Error| {
                !e.is_builder() && !(e.is_timeout() && matches!(self.kind, Kind::Ollama { .. }))
            };
            let resp = build().timeout(CALL_TIMEOUT).send().await;
            let (retry, delay, err) = match resp {
                Ok(r) if r.status().is_success() => match r.bytes().await {
                    Ok(body) => {
                        return serde_json::from_slice(&body)
                            .map_err(|e| ProviderError::Parse(e.to_string()));
                    }
                    Err(e) => (
                        retry_transport(&e),
                        retry_delay(attempt, None),
                        ProviderError::Transport(e.to_string()),
                    ),
                },
                Ok(r) => {
                    let delay = retry_delay(attempt, Some(&r));
                    let retry = retryable(&r);
                    let status = r.status().as_u16();
                    let body = r.text().await.unwrap_or_default();
                    (retry, delay, ProviderError::Status { status, body })
                }
                Err(e) => (
                    retry_transport(&e),
                    retry_delay(attempt, None),
                    ProviderError::Transport(e.to_string()),
                ),
            };
            if !retry || attempt >= MAX_RETRIES {
                return Err(err);
            }
            attempt += 1;
            tracing::warn!(provider = self.name, attempt, error = %err, "LLM call failed, retrying");
            tokio::time::sleep(delay).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay::ReplayServer;
    use std::collections::HashMap;

    fn env_of(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> + use<> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        move |k| map.get(k).cloned()
    }

    fn make(pairs: &[(&str, &str)]) -> Result<Option<Provider>, ProviderError> {
        Provider::from_env(None, None, env_of(pairs))
    }

    fn err(pairs: &[(&str, &str)]) -> String {
        make(pairs).unwrap_err().to_string()
    }

    #[test]
    fn no_provider_means_extraction_off() {
        assert!(make(&[]).unwrap().is_none());
        assert!(make(&[("EXTRACT_PROVIDER", "  ")]).unwrap().is_none());
        assert!(
            Provider::from_env(Some(""), None, env_of(&[("EXTRACT_PROVIDER", "ollama")]))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn unknown_and_unported_providers_fail() {
        assert!(
            err(&[("EXTRACT_PROVIDER", "bogus")]).contains("Unknown EXTRACT_PROVIDER: 'bogus'")
        );
        let e = err(&[
            ("EXTRACT_PROVIDER", "chatgpt-subscription"),
            ("CHATGPT_REFRESH_TOKEN", "x"),
            ("CHATGPT_CLIENT_ID", "y"),
        ]);
        assert!(e.contains("chatgpt-subscription is not supported"), "{e}");
    }

    #[test]
    fn missing_keys_fail_with_the_python_messages() {
        assert_eq!(
            err(&[("EXTRACT_PROVIDER", "anthropic")]),
            "ANTHROPIC_API_KEY or ANTHROPIC_OAUTH_TOKEN required when EXTRACT_PROVIDER=anthropic"
        );
        assert_eq!(
            err(&[("EXTRACT_PROVIDER", "openai")]),
            "OPENAI_API_KEY required when EXTRACT_PROVIDER=openai"
        );
        assert_eq!(
            err(&[
                ("EXTRACT_PROVIDER", "anthropic"),
                ("ANTHROPIC_AUTH", "bogus"),
                ("ANTHROPIC_API_KEY", "k")
            ]),
            "ANTHROPIC_AUTH must be 'api_key' or 'oauth', got 'bogus'"
        );
        assert_eq!(
            err(&[
                ("EXTRACT_PROVIDER", "anthropic"),
                ("ANTHROPIC_AUTH", "oauth"),
                ("ANTHROPIC_API_KEY", "k")
            ]),
            "ANTHROPIC_OAUTH_TOKEN required when ANTHROPIC_AUTH=oauth"
        );
        assert_eq!(
            err(&[
                ("EXTRACT_PROVIDER", "anthropic"),
                ("ANTHROPIC_AUTH", "api_key"),
                ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-x")
            ]),
            "ANTHROPIC_API_KEY required when ANTHROPIC_AUTH=api_key"
        );
    }

    #[test]
    fn anthropic_credential_and_default_model() {
        let both = [
            ("EXTRACT_PROVIDER", "anthropic"),
            ("ANTHROPIC_API_KEY", "sk-ant-api03-k"),
            ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-x"),
        ];
        assert_eq!(
            make(&both).unwrap().unwrap().model(),
            DEFAULT_ANTHROPIC_MODEL
        );
        let mut oauth = both.to_vec();
        oauth.push(("ANTHROPIC_AUTH", "OAuth"));
        assert_eq!(make(&oauth).unwrap().unwrap().model(), OAUTH_DEFAULT_MODEL);
        let only_oauth = [
            ("EXTRACT_PROVIDER", "anthropic"),
            ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-x"),
            ("EXTRACT_MODEL", " claude-haiku-5-5 "),
        ];
        assert_eq!(
            make(&only_oauth).unwrap().unwrap().model(),
            "claude-haiku-5-5"
        );
    }

    #[test]
    fn explicit_provider_and_model_override_the_environment() {
        let env = [
            ("EXTRACT_PROVIDER", "ollama"),
            ("EXTRACT_MODEL", "env-model"),
        ];
        let p = Provider::from_env(Some(" OLLAMA "), None, env_of(&env))
            .unwrap()
            .unwrap();
        assert_eq!((p.name(), p.model()), ("ollama", "env-model"));
        let p = Provider::from_env(None, Some("arg-model"), env_of(&env))
            .unwrap()
            .unwrap();
        assert_eq!(p.model(), "arg-model");
        let p = Provider::from_env(None, Some(" "), env_of(&env))
            .unwrap()
            .unwrap();
        assert_eq!(p.model(), DEFAULT_OLLAMA_MODEL);
    }

    #[test]
    fn url_scheme_must_be_http() {
        assert!(
            err(&[
                ("EXTRACT_PROVIDER", "ollama"),
                ("OLLAMA_URL", "file:///etc")
            ])
            .contains("OLLAMA_URL")
        );
        assert!(err(&[("EXTRACT_PROVIDER", "omlx"), ("OMLX_URL", "localhost:1")]).contains("OMLX"));
    }

    #[test]
    fn sampling_versus_effort_by_model() {
        for (model, temp) in [
            ("claude-haiku-4-5-20251001", true),
            ("claude-sonnet-4-6", true),
            ("claude-opus-4-20250514", true),
            ("claude-opus-4-1", true),
            ("claude-opus-4-5", true),
            ("claude-opus-4-6", true),
            ("claude-opus-4-7", false),
            ("claude-opus-4-8", false),
            ("claude-haiku-5-5", false),
        ] {
            assert_eq!(sends_temperature(model), temp, "{model}");
        }
    }

    async fn ollama_against(
        replies: Vec<(u16, Value)>,
    ) -> (ReplayServer, Result<Completion, ProviderError>) {
        let server = ReplayServer::start(replies, vec![]).await;
        let url = server.base_url();
        let p = make(&[("EXTRACT_PROVIDER", "ollama"), ("OLLAMA_URL", &url)])
            .unwrap()
            .unwrap();
        let r = p.complete("s", "u").await;
        (server, r)
    }

    #[tokio::test]
    async fn retries_a_server_error_then_succeeds() {
        let ok = json!({"response": "[]", "prompt_eval_count": 3, "eval_count": 1});
        let (server, r) =
            ollama_against(vec![(529, json!({"error": "overloaded"})), (200, ok)]).await;
        assert_eq!(r.unwrap().input_tokens, 3);
        assert_eq!(server.requests().len(), 2);
    }

    #[tokio::test]
    async fn stops_after_two_retries() {
        let busy = (429, json!({"error": "rate"}));
        let (server, r) =
            ollama_against(vec![busy.clone(), busy.clone(), busy.clone(), busy]).await;
        assert!(matches!(r, Err(ProviderError::Status { status: 429, .. })));
        assert_eq!(server.requests().len(), 3);
    }

    #[tokio::test]
    async fn does_not_retry_a_client_error() {
        let (server, r) =
            ollama_against(vec![(400, json!({"error": "bad"})), (200, json!({}))]).await;
        assert!(matches!(r, Err(ProviderError::Status { status: 400, .. })));
        assert_eq!(server.requests().len(), 1);
    }

    #[tokio::test]
    async fn connection_failure_is_an_error_and_health_is_false() {
        // Port 9 (discard) on loopback has no listener in the test container.
        let p = make(&[
            ("EXTRACT_PROVIDER", "ollama"),
            ("OLLAMA_URL", "http://127.0.0.1:9"),
        ])
        .unwrap()
        .unwrap();
        assert!(matches!(
            p.complete("s", "u").await,
            Err(ProviderError::Transport(_))
        ));
        assert!(!p.health_check().await);
    }

    #[test]
    fn retry_delay_doubles_without_a_header() {
        assert_eq!(retry_delay(0, None), Duration::from_millis(500));
        assert_eq!(retry_delay(1, None), Duration::from_secs(1));
    }

    fn response(status: u16, headers: &[(&str, &str)]) -> reqwest::Response {
        let mut b = axum::http::Response::builder().status(status);
        for (k, v) in headers {
            b = b.header(*k, *v);
        }
        reqwest::Response::from(b.body(Vec::<u8>::new()).unwrap())
    }

    #[test]
    fn retry_rules_follow_the_sdks() {
        assert!(retryable(&response(529, &[])));
        assert!(retryable(&response(409, &[])));
        assert!(!retryable(&response(400, &[])));
        assert!(!retryable(&response(503, &[("x-should-retry", "false")])));
        assert!(retryable(&response(400, &[("x-should-retry", "true")])));

        let d = |h: &[(&str, &str)]| retry_delay(0, Some(&response(429, h)));
        assert_eq!(d(&[("retry-after", "2")]), Duration::from_secs(2));
        assert_eq!(
            d(&[("retry-after-ms", "250"), ("retry-after", "9")]),
            Duration::from_millis(250)
        );
        assert_eq!(d(&[("retry-after", "3600")]), Duration::from_millis(500));
        assert_eq!(d(&[("retry-after", "0")]), Duration::from_millis(500));
        assert_eq!(
            d(&[("retry-after", "Wed, 21 Oct 2026 07:28:00 GMT")]),
            Duration::from_millis(500)
        );
    }

    #[test]
    fn scheme_check_is_case_insensitive_and_keeps_python_messages() {
        assert!(
            make(&[
                ("EXTRACT_PROVIDER", "ollama"),
                ("OLLAMA_URL", "HTTP://host:11434")
            ])
            .is_ok()
        );
        assert_eq!(
            err(&[("EXTRACT_PROVIDER", "ollama"), ("OLLAMA_URL", "ftp://h")]),
            "Invalid OLLAMA_URL scheme: 'ftp' (must be http or https)"
        );
        assert_eq!(
            err(&[("EXTRACT_PROVIDER", "omlx"), ("OMLX_URL", "localhost:1")]),
            "Invalid OMLX base_url scheme: 'localhost'"
        );
    }

    #[tokio::test]
    async fn oauth_beta_query_only_without_a_base_path() {
        let ok = json!({"content": [{"type": "text", "text": "x"}], "usage": {}});
        let server = ReplayServer::start(vec![(200, ok)], vec![]).await;
        let url = format!("{}/gateway", server.base_url());
        let p = make(&[
            ("EXTRACT_PROVIDER", "anthropic"),
            ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-oat01-x"),
            ("ANTHROPIC_BASE_URL", &url),
        ])
        .unwrap()
        .unwrap();
        assert_eq!(p.complete("s", "u").await.unwrap().text, "x");
        assert_eq!(server.requests()[0]["path"], "/gateway/v1/messages");
    }
}
