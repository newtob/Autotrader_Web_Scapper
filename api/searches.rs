//! `GET /api/searches` — every vehicle search that has priced data, so the UI knows what to request.

use std::sync::OnceLock;

use prices_api::{
    db,
    response::{CACHE_NONE, CACHE_OK, SearchesResponse, error_body},
};
use sqlx::PgPool;
use vercel_runtime::{Error, Request, Response, ResponseBody, run, service_fn};

static POOL: OnceLock<Result<PgPool, ()>> = OnceLock::new();

/// One lazily-created pool per warm instance. Errors are logged by kind only, never with the URL.
fn pool() -> Option<&'static PgPool> {
    POOL.get_or_init(|| {
        let url = std::env::var("DATABASE_URL_READONLY")
            .map_err(|_| eprintln!("DATABASE_URL_READONLY is not set"))?;
        db::connect(&url)
            .map_err(|_| eprintln!("DATABASE_URL_READONLY is not a valid Postgres URL"))
    })
    .as_ref()
    .ok()
}

fn json(status: u16, cache: &str, body: String) -> Result<Response<ResponseBody>, Error> {
    Ok(Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .header("Cache-Control", cache)
        .body(ResponseBody::from(body))?)
}

fn error(status: u16, message: &str) -> Result<Response<ResponseBody>, Error> {
    json(status, CACHE_NONE, error_body(message))
}

async fn handler(req: Request) -> Result<Response<ResponseBody>, Error> {
    if req.method() != "GET" {
        return error(405, "method not allowed");
    }
    let Some(pool) = pool() else {
        return error(503, "data unavailable");
    };
    match db::fetch_searches(pool).await {
        Ok(searches) => json(
            200,
            CACHE_OK,
            serde_json::to_string(&SearchesResponse {
                searches: &searches,
            })?,
        ),
        Err(e) => {
            eprintln!(
                "database query failed: {}",
                e.as_database_error()
                    .map_or("connection or protocol error", |_| "database error")
            );
            error(503, "data unavailable")
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    run(service_fn(handler)).await
}
