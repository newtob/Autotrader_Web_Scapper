//! Runs against a THROWAWAY Postgres only: set `TEST_DATABASE_URL` (the test creates and wipes
//! a `daily_stats` table). Skipped when unset so plain `cargo test` stays offline.

use prices_api::{db, params::PriceQuery};
use sqlx::postgres::PgPoolOptions;

async fn seeded_pool() -> Option<sqlx::PgPool> {
    let url = std::env::var("TEST_DATABASE_URL").ok()?;
    let admin = PgPoolOptions::new()
        .max_connections(1)
        .connect(&url)
        .await
        .unwrap();
    sqlx::raw_sql(
        "DROP TABLE IF EXISTS daily_stats;
         CREATE TABLE daily_stats (
           id serial PRIMARY KEY, stat_date date NOT NULL, make text NOT NULL,
           model text NOT NULL DEFAULT '', model_type text NOT NULL DEFAULT '',
           car_count integer NOT NULL, avg_price_gbp integer, avg_days_live numeric(6,1),
           computed_at timestamptz NOT NULL DEFAULT now());
         INSERT INTO daily_stats (stat_date, make, model, model_type, car_count, avg_price_gbp) VALUES
           ('2026-11-01','Porsche','Taycan','4S',5,43000),
           ('2026-11-02','Porsche','Taycan','4S',5,43500),
           ('2026-11-03','Porsche','Taycan','4S',0,NULL),
           ('2026-11-05','Porsche','Taycan','4S',5,44000),
           ('2026-11-05','Porsche','Taycan','',5,60000),
           ('2026-11-05','Porsche','Macan','',5,50000);",
    )
    .execute(&admin)
    .await
    .unwrap();
    Some(db::connect(&url).unwrap())
}

fn q(s: &str) -> PriceQuery {
    PriceQuery::parse(s).unwrap()
}

#[tokio::test]
async fn reads_window_anchored_on_latest_date() {
    let Some(pool) = seeded_pool().await else {
        return;
    };

    // 3 days ending 2026-11-05 => 03, 04, 05; 03 is NULL and 04 absent => only 05.
    let s = db::fetch_series(&pool, &q("make=Porsche&model=Taycan&type=4S&days=3"))
        .await
        .unwrap();
    assert_eq!(
        s.iter().map(|p| p.avg_price_gbp).collect::<Vec<_>>(),
        vec![44000]
    );

    // 5 days => 01..05, NULL day skipped, ascending.
    let s = db::fetch_series(&pool, &q("make=Porsche&model=Taycan&type=4S&days=5"))
        .await
        .unwrap();
    let dates: Vec<_> = s.iter().map(|p| p.date.to_string()).collect();
    assert_eq!(dates, ["2026-11-01", "2026-11-02", "2026-11-05"]);
}

#[tokio::test]
async fn matching_is_case_insensitive_and_type_scoped() {
    let Some(pool) = seeded_pool().await else {
        return;
    };

    let s = db::fetch_series(&pool, &q("make=porsche&model=TAYCAN&type=4s&days=30"))
        .await
        .unwrap();
    assert_eq!(s.len(), 3);

    let s = db::fetch_series(&pool, &q("make=Porsche&model=Taycan&days=30"))
        .await
        .unwrap();
    assert_eq!(
        s.iter().map(|p| p.avg_price_gbp).collect::<Vec<_>>(),
        vec![60000]
    );
}

#[tokio::test]
async fn unknown_search_is_empty() {
    let Some(pool) = seeded_pool().await else {
        return;
    };
    let s = db::fetch_series(&pool, &q("make=Nope&model=Nada&days=30"))
        .await
        .unwrap();
    assert!(s.is_empty());
}

#[tokio::test]
async fn the_connection_cannot_write() {
    let Some(pool) = seeded_pool().await else {
        return;
    };
    let mut tx = pool.begin().await.unwrap();
    sqlx::query("SET TRANSACTION READ ONLY")
        .execute(&mut *tx)
        .await
        .unwrap();
    let err = sqlx::query("DELETE FROM daily_stats")
        .execute(&mut *tx)
        .await
        .unwrap_err();
    assert!(err.to_string().contains("read-only"), "{err}");
}
