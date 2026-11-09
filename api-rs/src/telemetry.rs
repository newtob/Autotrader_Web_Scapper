//! Logging setup. One JSON object per line; request logs carry the request's span fields
//! (`method`, `path`, `request_id`). Vercel stamps each line, so no timestamps here.

use tracing::Level;

/// `LOG_LEVEL` (error|warn|info|debug|trace) sets the level; anything else falls back to info.
pub fn init() {
    let level = std::env::var("LOG_LEVEL")
        .ok()
        .and_then(|v| v.parse::<Level>().ok())
        .unwrap_or(Level::INFO);
    tracing_subscriber::fmt()
        .json()
        .with_max_level(level)
        .with_target(false)
        .with_ansi(false)
        .without_time()
        .init();
}
