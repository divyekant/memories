use std::path::Path;

use clap::{Parser, Subcommand};
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

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let argv0 = args.first().map(Path::new).and_then(|p| p.file_name());
    if argv0.is_some_and(|n| n.to_string_lossy().starts_with("python")) {
        return (memories_server::healthcheck::run(&args) as u8).into();
    }

    tracing_subscriber::fmt()
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
    tokio::runtime::Runtime::new()
        .expect("tokio runtime")
        .block_on(serve(port));
    std::process::ExitCode::SUCCESS
}

async fn serve(port: u16) {
    let listener = TcpListener::bind(("0.0.0.0", port))
        .await
        .unwrap_or_else(|e| panic!("cannot bind port {port}: {e}"));
    tracing::info!(
        port,
        version = memories_server::VERSION,
        "memories-server started"
    );
    axum::serve(listener, memories_server::app())
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("server error");
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
