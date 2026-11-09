//! Abuse limits: a per-client rate limit and a cap on requests in flight.
//!
//! Both are deliberately generous, so normal browsing never sees them; they only stop a client
//! hammering the function. They are per warm instance, not global, and the CDN cache already
//! absorbs repeat requests. Hard, platform-wide limits belong in Vercel's firewall.

use std::{net::IpAddr, sync::Arc, time::Duration};

use axum::{Router, http::Request, response::IntoResponse};
use tower_governor::{
    GovernorError, GovernorLayer,
    governor::{GovernorConfig, GovernorConfigBuilder},
    key_extractor::KeyExtractor,
};

use crate::error::AppError;

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    /// Requests one client may send back to back before being throttled.
    pub burst: u32,
    /// How often one request of a client's allowance is restored.
    pub replenish_every: Duration,
    /// Requests being handled at once, across all clients, before new ones are shed with 503.
    pub max_in_flight: usize,
}

impl Default for Limits {
    /// 10 requests a second sustained with a burst of 120 per client, and 64 in flight. The UI
    /// makes one `/api/searches` call and one `/api/prices` call per vehicle card.
    fn default() -> Self {
        Self {
            burst: 120,
            replenish_every: Duration::from_millis(100),
            max_in_flight: 64,
        }
    }
}

/// Identifies the client by the IP Vercel puts in `x-real-ip` / `x-forwarded-for`. Vercel
/// overwrites both, so a client cannot choose its own key; run elsewhere, put a trusted proxy in
/// front or replace this. Requests with no usable header share one bucket, so the limit still
/// applies to them.
#[derive(Debug, Clone, Copy)]
pub struct ClientIp;

impl KeyExtractor for ClientIp {
    type Key = Option<IpAddr>;

    fn extract<T>(&self, req: &Request<T>) -> Result<Self::Key, GovernorError> {
        let headers = req.headers();
        let ip = ["x-real-ip", "x-forwarded-for"].iter().find_map(|name| {
            let value = headers.get(*name)?.to_str().ok()?;
            value.split(',').next()?.trim().parse().ok()
        });
        Ok(ip)
    }
}

/// Wrap `router` in the per-client rate limit; excess requests get a JSON 429 with `Retry-After`.
/// Panics if `burst` or `replenish_every` is zero.
///
/// Spawns a task that drops idle clients from the limiter, so it must run inside a Tokio runtime.
/// (Applied to the router rather than returned as a layer because `governor`'s middleware type is
/// not re-exported.)
pub fn with_rate_limit(router: Router, limits: Limits) -> Router {
    let mut builder = GovernorConfigBuilder::default();
    builder
        .period(limits.replenish_every)
        .burst_size(limits.burst);
    let config: Arc<GovernorConfig<_, _>> = Arc::new(
        builder
            .key_extractor(ClientIp)
            .finish()
            .expect("rate limit burst and period must be non-zero"),
    );

    let limiter = config.limiter().clone();
    tokio::spawn(async move {
        let mut tick = tokio::time::interval(Duration::from_secs(60));
        loop {
            tick.tick().await;
            limiter.retain_recent();
        }
    });

    router.layer(GovernorLayer::new(config).error_handler(|e| {
        match e {
            GovernorError::TooManyRequests { wait_time, .. } => AppError::RateLimited {
                retry_after_secs: wait_time,
            },
            _ => AppError::Internal,
        }
        .into_response()
    }))
}
