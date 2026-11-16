use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use std::sync::atomic::Ordering;

use memories_embed::{EmbedConfig, Embedder, prepare_cache_dir};
use memories_server::{AppState, Config, audit::AuditLog, keys::KeyStore, migrate};
use memories_store::{QdrantSettings, QdrantStore, StateDb};
use memories_types::config;
use serde_json::Value;
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
    /// Copy the records of metadata.json into memories_v2, then verify. The
    /// command does not change metadata.json or the old collection.
    Migrate {
        /// Shadow mode: also replace changed records and delete removed
        /// records in memories_v2.
        #[arg(long)]
        shadow: bool,
        /// The count of vectors that the verify step compares.
        #[arg(long, default_value_t = 1000)]
        sample: usize,
    },
    /// Compare memories_v2 with metadata.json. Exit code 1 on a difference.
    Verify {
        #[arg(long, default_value_t = 1000)]
        sample: usize,
    },
}

/// The interval for writing `last_used_at` and `usage_count` of the API keys.
const KEY_USAGE_FLUSH: Duration = Duration::from_secs(5);

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
    let cfg = match Config::from_env() {
        Ok(c) => c,
        Err(msg) => {
            tracing::error!("cannot start: {msg}");
            return std::process::ExitCode::FAILURE;
        }
    };
    let port = match cli.command {
        Command::Serve { port } => port,
        Command::Migrate { shadow, sample } => {
            let opts = migrate::Options {
                shadow,
                sample,
                ..Default::default()
            };
            return run_command(&cfg, opts, false);
        }
        Command::Verify { sample } => {
            let opts = migrate::Options {
                sample,
                ..Default::default()
            };
            return run_command(&cfg, opts, true);
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
    let migrate_first = match first_start_check(&cfg) {
        Ok(m) => m,
        Err(msg) => {
            tracing::error!("cannot start: {msg}");
            return std::process::ExitCode::FAILURE;
        }
    };
    let state = AppState::new(&cfg, keys, audit);
    tokio::runtime::Runtime::new()
        .expect("tokio runtime")
        .block_on(serve(port, state, migrate_first.then_some(cfg)));
    std::process::ExitCode::SUCCESS
}

/// True if the server must migrate before it serves. An error stops the start.
fn first_start_check(cfg: &Config) -> Result<bool, String> {
    if !migrate::metadata_path(&cfg.data_dir).exists() {
        return Ok(false);
    }
    let state = open_state(cfg)?;
    Ok(migrate::start_action(&cfg.data_dir, &state)? == migrate::StartAction::Migrate)
}

fn open_state(cfg: &Config) -> Result<StateDb, String> {
    let path = cfg.data_dir.join("state.db");
    StateDb::open(&path).map_err(|e| format!("cannot open {}: {e}", path.display()))
}

async fn load_embedder() -> Result<Embedder, String> {
    let var = |n| std::env::var(n).unwrap_or_default();
    let cache = prepare_cache_dir(&var("MODEL_CACHE_DIR"), &var("PRELOADED_MODEL_CACHE_DIR"))
        .map_err(|e| e.to_string())?;
    let config = EmbedConfig::from_env(cache).map_err(|e| e.to_string())?;
    Embedder::load(config).await.map_err(|e| e.to_string())
}

/// Run `migrate` (or `verify`) with the stores and the embedder of the
/// environment. Returns the report.
async fn run_migration(
    cfg: &Config,
    opts: &migrate::Options,
    verify_only: bool,
) -> Result<Value, String> {
    let settings = QdrantSettings::from_env().map_err(|e| e.to_string())?;
    let legacy = migrate::Legacy::read(&cfg.data_dir)?;
    let old_name = legacy
        .old_collection
        .unwrap_or_else(|| settings.collection.clone());
    let new = QdrantStore::new(&settings, &settings.v2_collection()).map_err(|e| e.to_string())?;
    let old = QdrantStore::new(&settings, &old_name).map_err(|e| e.to_string())?;
    let state = open_state(cfg)?;
    let embed = load_embedder().await?;
    let stores = migrate::Stores {
        new: &new,
        old: &old,
        state: &state,
    };
    if verify_only {
        migrate::verify(&cfg.data_dir, &stores, &embed, opts).await
    } else {
        migrate::migrate(&cfg.data_dir, &stores, &embed, opts).await
    }
}

fn run_command(cfg: &Config, opts: migrate::Options, verify_only: bool) -> std::process::ExitCode {
    let result = tokio::runtime::Runtime::new()
        .expect("tokio runtime")
        .block_on(run_migration(cfg, &opts, verify_only));
    match result {
        Ok(report) => {
            println!("{}", serde_json::to_string_pretty(&report).expect("json"));
            if report.get("ok") == Some(&Value::Bool(false)) {
                std::process::ExitCode::FAILURE
            } else {
                std::process::ExitCode::SUCCESS
            }
        }
        Err(msg) => {
            tracing::error!("{msg}");
            std::process::ExitCode::FAILURE
        }
    }
}

async fn serve(port: u16, state: Arc<AppState>, migrate_first: Option<Config>) {
    let listener = TcpListener::bind(("0.0.0.0", port))
        .await
        .unwrap_or_else(|e| panic!("cannot bind port {port}: {e}"));
    tracing::info!(
        port,
        version = memories_server::VERSION,
        "memories-server started"
    );
    if let Some(cfg) = migrate_first {
        state.migrating.store(true, Ordering::Release);
        let st = state.clone();
        tokio::spawn(async move {
            tracing::info!("first start: migrating metadata.json to memories_v2");
            let opts = migrate::Options {
                sample: 1000,
                ..Default::default()
            };
            match run_migration(&cfg, &opts, false).await {
                Ok(report) => {
                    tracing::info!(%report, "migration done");
                    st.migrating.store(false, Ordering::Release);
                }
                Err(msg) => {
                    tracing::error!(
                        "migration failed, the server stops. The old data did not change: {msg}"
                    );
                    std::process::exit(1);
                }
            }
        });
    }
    let flusher = tokio::spawn(flush_key_usage_forever(state.clone()));
    axum::serve(
        listener,
        memories_server::app(state.clone()).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("server error");
    flusher.abort();
    flush_key_usage(&state).await;
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
