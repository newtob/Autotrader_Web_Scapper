//! Read-only access to `daily_stats`. The only SQL in this crate is the one `SELECT` below.

use std::time::Duration;

use sqlx::postgres::{PgConnectOptions, PgPool, PgPoolOptions};

use crate::params::PriceQuery;
use crate::response::Point;

/// Last `days` calendar days ending at the search's latest priced `stat_date`, oldest first.
const SERIES_SQL: &str = "\
SELECT stat_date, avg_price_gbp FROM daily_stats \
WHERE lower(make) = lower($1) AND lower(model) = lower($2) AND lower(model_type) = lower($3) \
  AND avg_price_gbp IS NOT NULL \
  AND stat_date > (SELECT max(stat_date) FROM daily_stats \
                   WHERE lower(make) = lower($1) AND lower(model) = lower($2) \
                     AND lower(model_type) = lower($3) AND avg_price_gbp IS NOT NULL) - $4::int \
ORDER BY stat_date";

/// Build a lazy single-connection pool. Never logs or returns the URL.
///
/// The statement cache is disabled because the Supabase transaction pooler (port 6543) does not
/// keep named prepared statements between queries.
pub fn connect(database_url: &str) -> Result<PgPool, sqlx::Error> {
    let options: PgConnectOptions = database_url.parse()?;
    Ok(PgPoolOptions::new()
        .max_connections(1)
        .acquire_timeout(Duration::from_secs(3))
        .connect_lazy_with(options.statement_cache_capacity(0)))
}

pub async fn fetch_series(pool: &PgPool, query: &PriceQuery) -> Result<Vec<Point>, sqlx::Error> {
    let mut tx = pool.begin().await?;
    // Second line of defence after the read-only role: this transaction cannot write.
    sqlx::query("SET TRANSACTION READ ONLY")
        .persistent(false)
        .execute(&mut *tx)
        .await?;
    let rows: Vec<(chrono::NaiveDate, i32)> = sqlx::query_as(SERIES_SQL)
        .persistent(false)
        .bind(&query.make)
        .bind(&query.model)
        .bind(&query.model_type)
        .bind(i32::try_from(query.days).unwrap_or(i32::MAX))
        .fetch_all(&mut *tx)
        .await?;
    tx.rollback().await?;
    Ok(rows
        .into_iter()
        .map(|(date, avg_price_gbp)| Point {
            date,
            avg_price_gbp,
        })
        .collect())
}
