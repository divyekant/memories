use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use memories_engine::Engine;
use memories_engine::writes::{DEFAULT_IDENTICAL_THRESHOLD, Writer};
use memories_server::{AppState, Config, audit::AuditLog, keys::KeyStore};
use memories_store::QdrantSettings;
use memories_types::config;
use tokio::net::TcpListener;

#[derive(Parser)]
#[command(name = "memories-server", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Start the HTTP server.
    Serve {
        #[arg(long, env = "PORT", default_value_t = 8000)]
        port: u16,
    },
}

/// The interval for writing `last_used_at` and `usage_count` of the API keys.
const KEY_USAGE_FLUSH: Duration = Duration::from_secs(5);
/// The interval for writing the `last_reinforced_at` values of searches.
const REINFORCE_FLUSH: Duration = Duration::from_secs(2);

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let argv0 = args.first().map(Path::new).and_then(|p| p.file_name());
    if argv0.is_some_and(|n| n.to_string_lossy().starts_with("python")) {
        return (memories_server::healthcheck::run(&args) as u8).into();
    }

    tracing_subscriber::fmt()
        .with_ansi(std::io::IsTerminal::is_terminal(&std::io::stdout()))
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    // With no command, run `serve`, so that a plain `docker run` starts the server.
    let cli = if args.len() == 1 {
        Cli::parse_from([args[0].as_str(), "serve"])
    } else {
        Cli::parse()
    };
    let Command::Serve { port } = cli.command;

    let cfg = match Config::from_env() {
        Ok(c) => c,
        Err(msg) => {
            tracing::error!("cannot start: {msg}");
            return std::process::ExitCode::FAILURE;
        }
    };
    for name in config::set_but_not_used(|n| std::env::var(n).ok()) {
        tracing::info!("{name} is set, but this server version does not use it");
    }
    if let Err(e) = std::fs::create_dir_all(&cfg.data_dir) {
        tracing::error!(
            "cannot start: cannot make DATA_DIR {}: {e}",
            cfg.data_dir.display()
        );
        return std::process::ExitCode::FAILURE;
    }
    let keys_path = cfg.data_dir.join("keys.db");
    let keys = match KeyStore::open(&keys_path) {
        Ok(k) => k,
        Err(e) => {
            tracing::error!("cannot start: cannot open {}: {e}", keys_path.display());
            return std::process::ExitCode::FAILURE;
        }
    };
    let audit = if cfg.audit_log {
        let path = cfg.data_dir.join("audit.db");
        match AuditLog::open(&path) {
            Ok(a) => {
                tracing::info!("Audit logging enabled");
                Some(a)
            }
            Err(e) => {
                tracing::error!("cannot start: cannot open {}: {e}", path.display());
                return std::process::ExitCode::FAILURE;
            }
        }
    } else {
        None
    };
    let runtime = tokio::runtime::Runtime::new().expect("tokio runtime");
    let writer = match runtime.block_on(open_writer(&cfg)) {
        Ok(w) => w,
        Err(msg) => {
            tracing::error!("cannot start: {msg}");
            return std::process::ExitCode::FAILURE;
        }
    };
    let state = AppState::with_writer(&cfg, keys, audit, writer);
    runtime.block_on(serve(port, state));
    std::process::ExitCode::SUCCESS
}

/// The engine, when `QDRANT_URL` is set. With no `QDRANT_URL`, the server
/// starts without it and the routes that need it answer 503.
async fn open_engine(cfg: &Config) -> Result<Option<Arc<Engine>>, String> {
    if std::env::var("QDRANT_URL").map_or(true, |u| u.trim().is_empty()) {
        tracing::warn!("QDRANT_URL is not set: the memory routes answer 503");
        return Ok(None);
    }
    let settings = QdrantSettings::from_env().map_err(|e| e.to_string())?;
    let graph = memories_engine::engine::graph_params(|n| std::env::var(n).ok())
        .map_err(|e| e.to_string())?;
    let cache = memories_embed::prepare_cache_dir(
        &config::string("MODEL_CACHE_DIR", ""),
        &config::string("PRELOADED_MODEL_CACHE_DIR", ""),
    )
    .map_err(|e| format!("model cache: {e}"))?;
    let embed = memories_embed::EmbedConfig::from_env(cache).map_err(|e| e.to_string())?;
    let engine = Engine::open(&settings, &cfg.data_dir, embed, graph)
        .await
        .map_err(|e| e.to_string())?;
    tracing::info!(
        collection = engine.store.collection(),
        records = engine.count(),
        "engine ready"
    );
    Ok(Some(Arc::new(engine)))
}

/// The write flow over the engine. `DOCTRINE_IDENTICAL_THRESHOLD` is a number
/// (Python reads it with `float()` on each doctrine add).
async fn open_writer(cfg: &Config) -> Result<Option<Arc<Writer>>, String> {
    let Some(engine) = open_engine(cfg).await? else {
        return Ok(None);
    };
    let raw = std::env::var("DOCTRINE_IDENTICAL_THRESHOLD").ok();
    let threshold = match raw.as_deref().map(str::trim) {
        None => DEFAULT_IDENTICAL_THRESHOLD,
        Some(v) => v.parse::<f64>().map_err(|_| {
            format!("DOCTRINE_IDENTICAL_THRESHOLD={v:?} is not a valid number. Set a number or remove it.")
        })?,
    };
    let writer = Writer::open(engine, cfg.data_dir.clone(), threshold)
        .await
        .map_err(|e| e.to_string())?;
    Ok(Some(Arc::new(writer)))
}

async fn serve(port: u16, state: Arc<AppState>) {
    let listener = TcpListener::bind(("0.0.0.0", port))
        .await
        .unwrap_or_else(|e| panic!("cannot bind port {port}: {e}"));
    tracing::info!(
        port,
        version = memories_server::VERSION,
        "memories-server started"
    );
    let flusher = tokio::spawn(flush_key_usage_forever(state.clone()));
    let reinforcer = tokio::spawn(flush_reinforcements_forever(state.clone()));
    axum::serve(
        listener,
        memories_server::app(state.clone()).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("server error");
    flusher.abort();
    reinforcer.abort();
    flush_key_usage(&state).await;
    flush_reinforcements(&state).await;
}

async fn flush_reinforcements_forever(state: Arc<AppState>) {
    let mut tick = tokio::time::interval(REINFORCE_FLUSH);
    loop {
        tick.tick().await;
        flush_reinforcements(&state).await;
    }
}

async fn flush_reinforcements(state: &Arc<AppState>) {
    if let Some(engine) = &state.engine
        && let Err(e) = engine.flush_reinforcements().await
    {
        tracing::warn!(error = %e, "reinforcement write failed; will try again");
    }
}

async fn flush_key_usage_forever(state: Arc<AppState>) {
    let mut tick = tokio::time::interval(KEY_USAGE_FLUSH);
    loop {
        tick.tick().await;
        flush_key_usage(&state).await;
    }
}

async fn flush_key_usage(state: &Arc<AppState>) {
    let st = state.clone();
    match tokio::task::spawn_blocking(move || st.keys.flush_usage()).await {
        Ok(Ok(_)) => {}
        Ok(Err(e)) => tracing::warn!(error = %e, "keys.db: usage write failed; will try again"),
        Err(e) => tracing::warn!(error = %e, "keys.db: usage write task failed"),
    }
}

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    let term = async {
        if let Ok(mut s) = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            s.recv().await;
        }
    };
    tokio::select! { _ = ctrl_c => {}, _ = term => {} }
    tracing::info!("shutdown signal received");
}
