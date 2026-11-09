//! The Axum router: `GET /api/prices` and `GET /api/searches`.

use std::time::Duration;

use axum::{
    BoxError, Router,
    error_handling::HandleErrorLayer,
    extract::{RawQuery, State},
    http::{Request, StatusCode},
    response::Response,
    routing::get,
};
use tower::{ServiceBuilder, limit::GlobalConcurrencyLimitLayer, load_shed::error::Overloaded};
use tower_http::{
    LatencyUnit,
    request_id::{MakeRequestUuid, PropagateRequestIdLayer, SetRequestIdLayer},
    timeout::TimeoutLayer,
    trace::{DefaultOnResponse, TraceLayer},
};
use tracing::Level;

use crate::{
    db,
    error::{AppError, json_response},
    limits::{Limits, with_rate_limit},
    params::PriceQuery,
    response::{CACHE_OK, PriceResponse, SearchesResponse},
    state::AppState,
};

/// Under Vercel's 10 s `maxDuration`, so a stuck request answers instead of being killed.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(8);

/// Header carrying the request ID: set when the caller sent none, echoed on the response, and
/// attached to every log line for the request.
const REQUEST_ID: &str = "x-request-id";

pub fn router(state: AppState) -> Router {
    router_with(state, Limits::default())
}

/// Layers, outermost first: request ID, request log, rate limit (429), load shedding (503) over a
/// concurrency cap, then the timeout. Rate limiting sits outside the cap, so a client that is
/// already throttled never takes an in-flight slot.
pub fn router_with(state: AppState, limits: Limits) -> Router {
    let routes = Router::new()
        .route("/api/prices", get(prices).fallback(method_not_allowed))
        .route("/api/searches", get(searches).fallback(method_not_allowed))
        .with_state(state)
        .layer(
            ServiceBuilder::new()
                .layer(HandleErrorLayer::new(shed))
                .load_shed()
                .layer(GlobalConcurrencyLimitLayer::new(limits.max_in_flight))
                .layer(TimeoutLayer::with_status_code(
                    StatusCode::SERVICE_UNAVAILABLE,
                    REQUEST_TIMEOUT,
                )),
        );

    with_rate_limit(routes, limits).layer(
        ServiceBuilder::new()
            .layer(SetRequestIdLayer::x_request_id(MakeRequestUuid))
            .layer(
                TraceLayer::new_for_http()
                    .make_span_with(|req: &Request<_>| {
                        let request_id = req
                            .headers()
                            .get(REQUEST_ID)
                            .and_then(|v| v.to_str().ok())
                            .unwrap_or_default();
                        tracing::info_span!(
                            "request",
                            method = %req.method(),
                            path = %req.uri().path(),
                            request_id,
                        )
                    })
                    .on_response(
                        DefaultOnResponse::new()
                            .level(Level::INFO)
                            .latency_unit(LatencyUnit::Millis),
                    ),
            )
            .layer(PropagateRequestIdLayer::x_request_id()),
    )
}

/// Maps the load-shed error to a 503; anything else from this stack is a bug.
async fn shed(err: BoxError) -> AppError {
    if err.is::<Overloaded>() {
        AppError::Busy
    } else {
        tracing::error!("unexpected middleware error: {err}");
        AppError::Internal
    }
}

async fn method_not_allowed() -> AppError {
    AppError::MethodNotAllowed
}

/// Log a failed query by kind only, never the driver text, then answer 503.
fn query_failed(e: sqlx::Error) -> AppError {
    tracing::error!("database query failed: {}", db::error_kind(&e));
    AppError::Unavailable
}

fn ok_json<T: serde::Serialize>(body: &T) -> Result<Response, AppError> {
    let body = serde_json::to_string(body).map_err(|_| AppError::Internal)?;
    Ok(json_response(StatusCode::OK, CACHE_OK, body))
}

/// Parsed from the raw query string, not `Query<T>`, to keep the documented rules (first key
/// wins, trimming, length and control-character limits) in one place: `PriceQuery::parse`.
async fn prices(
    State(state): State<AppState>,
    RawQuery(raw): RawQuery,
) -> Result<Response, AppError> {
    let query = PriceQuery::parse(raw.as_deref().unwrap_or(""))?;
    let series = db::fetch_series(state.pool()?, &query)
        .await
        .map_err(query_failed)?;
    ok_json(&PriceResponse::new(&query, &series))
}

async fn searches(State(state): State<AppState>) -> Result<Response, AppError> {
    let searches = db::fetch_searches(state.pool()?)
        .await
        .map_err(query_failed)?;
    ok_json(&SearchesResponse {
        searches: &searches,
    })
}
