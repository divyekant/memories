use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};

use memories_server::{AppState, Config, audit::AuditLog, keys::KeyStore};

pub fn temp_dir() -> PathBuf {
    static N: AtomicU32 = AtomicU32::new(0);
    let dir = std::env::temp_dir().join(format!(
        "memories-server-test-{}-{}",
        std::process::id(),
        N.fetch_add(1, Ordering::Relaxed)
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

pub fn config(api_key: &str, rate_limit: i64) -> Config {
    Config {
        data_dir: temp_dir(),
        api_key: api_key.into(),
        auth_rate_limit: rate_limit,
        metrics_latency_samples: 200,
        forwarded_allow_ips: "127.0.0.1".into(),
        audit_log: false,
        max_backups: 10,
        usage_tracking: false,
        metrics_trend_samples: 120,
        ui_dir: "/nonexistent-ui".into(),
    }
}

pub fn state(cfg: &Config) -> Arc<AppState> {
    let audit = cfg
        .audit_log
        .then(|| AuditLog::open(&cfg.data_dir.join("audit.db")).unwrap());
    AppState::new(
        cfg,
        KeyStore::open(&cfg.data_dir.join("keys.db")).unwrap(),
        audit,
    )
}
