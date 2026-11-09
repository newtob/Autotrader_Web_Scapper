//! Shared handler state: the read-only Postgres pool.

use sqlx::PgPool;

use crate::{db, error::AppError};

#[derive(Clone)]
pub struct AppState {
    pool: Option<PgPool>,
}

impl AppState {
    pub fn new(pool: Option<PgPool>) -> Self {
        Self { pool }
    }

    /// Build the lazy pool from `DATABASE_URL_READONLY`. A missing or invalid URL leaves the pool
    /// empty so every data request answers 503. Errors are logged by kind only, never with the URL.
    pub fn from_env() -> Self {
        let pool = match std::env::var("DATABASE_URL_READONLY") {
            Err(_) => {
                tracing::error!("DATABASE_URL_READONLY is not set");
                None
            }
            Ok(url) => db::connect(&url)
                .inspect_err(|_| {
                    tracing::error!("DATABASE_URL_READONLY is not a valid Postgres URL")
                })
                .ok(),
        };
        Self::new(pool)
    }

    /// Open the first connection in the background, so its TCP, TLS and pooler handshakes overlap
    /// startup instead of delaying the first request. A failure is only logged (by kind); the first
    /// request simply tries again. Needs a Tokio runtime; does nothing without a pool.
    pub fn warm_up(&self) {
        let Some(pool) = self.pool.clone() else {
            return;
        };
        tokio::spawn(async move {
            if let Err(e) = pool.acquire().await {
                tracing::warn!("pool warm-up failed: {}", db::error_kind(&e));
            }
        });
    }

    pub fn pool(&self) -> Result<&PgPool, AppError> {
        self.pool.as_ref().ok_or(AppError::Unavailable)
    }
}
