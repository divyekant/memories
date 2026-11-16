use std::net::SocketAddr;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use clap::{Parser, Subcommand};
use memories_server::{AppState, Config, keys::KeyStore};
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
    let state = AppState::new(&cfg, keys);
    tokio::runtime::Runtime::new()
        .expect("tokio runtime")
        .block_on(serve(port, state));
    std::process::ExitCode::SUCCESS
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
