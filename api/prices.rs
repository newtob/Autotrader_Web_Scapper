//! `GET /api/prices?make=&model=&type=&days=` — read-only daily average price series.

use std::sync::OnceLock;

use prices_api::{
    db,
    params::PriceQuery,
    response::{CACHE_NONE, CACHE_OK, PriceResponse, error_body},
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
    let query = match PriceQuery::parse(req.uri().query().unwrap_or("")) {
        Ok(q) => q,
        Err(e) => return error(400, &e.to_string()),
    };
    let Some(pool) = pool() else {
        return error(503, "data unavailable");
    };
    match db::fetch_series(pool, &query).await {
        Ok(series) => json(
            200,
            CACHE_OK,
            serde_json::to_string(&PriceResponse::new(&query, &series))?,
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
