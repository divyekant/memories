use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use memories_engine::Engine;
use memories_engine::writes::{DEFAULT_IDENTICAL_THRESHOLD, Writer};
use std::sync::atomic::Ordering;

use memories_embed::{EmbedConfig, Embedder, prepare_cache_dir};
use memories_extract::provider::Provider;
use memories_server::{
    AppState, Config,
    audit::AuditLog,
    extract::{Extract, ExtractConfig, Pipeline},
    keys::KeyStore,
    migrate,
    pipeline::{ExtractPipeline, PipelineConfig},
};
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
    // A second server on the same DATA_DIR would put the running jobs of the
    // first server back in the queue. Keep the lock until the process ends.
    let lock_path = cfg.data_dir.join("memories-server.lock");
    let _lock = match std::fs::File::create(&lock_path).map(|f| (f.try_lock(), f)) {
        Ok((Ok(()), f)) => f,
        Ok((Err(e), _)) => {
            tracing::error!(
                "cannot start: another memories-server uses DATA_DIR {} ({e})",
                cfg.data_dir.display()
            );
            return std::process::ExitCode::FAILURE;
        }
        Err(e) => {
            tracing::error!("cannot start: cannot make {}: {e}", lock_path.display());
            return std::process::ExitCode::FAILURE;
        }
    };
    // As Python: a provider that cannot start is a warning, and extraction is off.
    let provider = match Provider::from_process_env() {
        Ok(Some(p)) => {
            tracing::info!(
                "Extraction enabled: provider={}, model={}",
                p.name(),
                p.model()
            );
            Some(Arc::new(p))
        }
        Ok(None) => {
            tracing::info!("Extraction disabled (EXTRACT_PROVIDER not set)");
            None
        }
        Err(e) => {
            tracing::warn!("Extraction setup failed: {e}");
            None
        }
    };
    let pipeline = provider.clone().map(|provider| {
        Arc::new(ExtractPipeline {
            provider,
            cfg: PipelineConfig::from_env(),
        }) as Arc<dyn Pipeline>
    });
    let extract = match Extract::open(&cfg.data_dir, ExtractConfig::from_env(), provider, pipeline)
    {
        Ok(e) => e,
        Err(e) => {
            tracing::error!("cannot start: cannot open the extract queue: {e}");
            return std::process::ExitCode::FAILURE;
        }
    };
    let migrate_first = match first_start_check(&cfg) {
        Ok(m) => m,
        Err(msg) => {
            tracing::error!("cannot start: {msg}");
            return std::process::ExitCode::FAILURE;
        }
    };
    let runtime = tokio::runtime::Runtime::new().expect("tokio runtime");
    // After a first-start migration, the engine opens when the migration ends.
    let writer = if migrate_first {
        None
    } else {
        match runtime.block_on(open_writer(&cfg)) {
            Ok(e) => e,
            Err(msg) => {
                tracing::error!("cannot start: {msg}");
                return std::process::ExitCode::FAILURE;
            }
        }
    };
    let engine = writer.as_ref().map(|w| w.engine.clone());
    let state = AppState::with_extract(&cfg, keys, audit, extract, engine);
    if let Some(w) = writer {
        let _ = state.writer.set(w);
    }
    runtime.block_on(serve(port, state, migrate_first.then_some(cfg)));
    std::process::ExitCode::SUCCESS
}

/// True if the server must migrate before it serves. An error stops the start.
fn first_start_check(cfg: &Config) -> Result<bool, String> {
    if !migrate::metadata_path(&cfg.data_dir).exists() {
        return Ok(false);
    }
    // With no Qdrant server there is no memories_v2 to write or read; the
    // data routes stay off (see the Engine in AppState), so do not migrate.
    if std::env::var("QDRANT_URL")
        .unwrap_or_default()
        .trim()
        .is_empty()
    {
        tracing::warn!("metadata.json exists, but QDRANT_URL is empty: no migration");
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

/// The engine and its write flow. `DOCTRINE_IDENTICAL_THRESHOLD` must be a
/// number (Python reads it with `float()` on each doctrine add).
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
        let task = tokio::spawn(async move {
            tracing::info!("first start: migrating metadata.json to memories_v2");
            let opts = migrate::Options {
                sample: 1000,
                ..Default::default()
            };
            match run_migration(&cfg, &opts, false).await {
                Ok(report) => {
                    tracing::info!(%report, "migration done");
                    match open_writer(&cfg).await {
                        Ok(Some(w)) => {
                            let _ = st.engine.set(w.engine.clone());
                            let _ = st.writer.set(w);
                        }
                        Ok(None) => {}
                        Err(msg) => {
                            tracing::error!("cannot open the engine, the server stops: {msg}");
                            std::process::exit(1);
                        }
                    }
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
        // A panic in the migration must stop the server, not leave it at 503.
        tokio::spawn(async move {
            if let Err(e) = task.await {
                tracing::error!("migration task failed, the server stops: {e}");
                std::process::exit(1);
            }
        });
    }
    let flusher = tokio::spawn(flush_key_usage_forever(state.clone()));
    let reinforcer = tokio::spawn(flush_reinforcements_forever(state.clone()));
    // A running job stays `running` in state.db at a stop; the next start puts it back.
    let workers = memories_server::extract::spawn_workers(&state);
    // As Python: the scheduler needs MAINTENANCE_ENABLED and an extract provider.
    let maint = match memories_server::maintenance::SchedulerConfig::from_env() {
        Ok(m) => m,
        Err(e) => {
            tracing::error!("{e}");
            std::process::exit(1);
        }
    };
    let scheduler = match (&state.extract.provider, maint.enabled) {
        (Some(p), true) => Some(tokio::spawn(memories_server::maintenance::scheduler(
            state.clone(),
            maint,
            p.clone(),
        ))),
        _ => None,
    };
    axum::serve(
        listener,
        memories_server::app(state.clone()).into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("server error");
    flusher.abort();
    reinforcer.abort();
    workers.iter().for_each(|w| w.abort());
    if let Some(s) = scheduler {
        // Let a running job stop at a safe point (between clusters), so that a
        // merge does not stop between its add and its delete.
        state
            .stopping
            .store(true, std::sync::atomic::Ordering::Relaxed);
        let held = tokio::time::timeout(std::time::Duration::from_secs(120), state.jobs.lock())
            .await
            .ok();
        s.abort();
        drop(held);
    }
    flush_key_usage(&state).await;
    flush_reinforcements(&state).await;
    // Python writes each usage row before it replies; write the queued rows before the exit.
    if let Some(u) = &state.usage {
        u.flush().await;
    }
}

async fn flush_reinforcements_forever(state: Arc<AppState>) {
    let mut tick = tokio::time::interval(REINFORCE_FLUSH);
    loop {
        tick.tick().await;
        flush_reinforcements(&state).await;
    }
}

async fn flush_reinforcements(state: &Arc<AppState>) {
    // The writer lock keeps a flush out of a write that reads, then writes a payload.
    let result = match (state.writer.get(), state.engine.get()) {
        (Some(w), _) => w.flush_reinforcements().await.map_err(|e| e.to_string()),
        (None, Some(engine)) => engine
            .flush_reinforcements()
            .await
            .map(|_| ())
            .map_err(|e| e.to_string()),
        (None, None) => Ok(()),
    };
    if let Err(e) = result {
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
