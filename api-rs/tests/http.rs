//! Router-level tests: requests go straight into the Axum `Router`, no sockets.
//! The 200-path test needs `TEST_DATABASE_URL` and returns early without it, like `db_integration`.
//!
//! Every test except the last runs without a database (`AppState::new(None)`), so a request that
//! passes validation ends in 503. That is how the "accepted" cases are observed.
//!
//! | Test                                               | What it checks                                                          |
//! | -------------------------------------------------- | ----------------------------------------------------------------------- |
//! | `missing_make_is_400_with_no_store`                | a missing `make` is a 400 with the fixed error body and `no-store`      |
//! | `invalid_days_is_400`                              | `days=0` is rejected as an invalid `days`                               |
//! | `days_is_required`                                 | a request without `days` is a 400 naming `days`                         |
//! | `days_above_max_is_400`                            | `days=1096` (one over the 1095 cap) is rejected                         |
//! | `days_must_be_an_integer`                          | a non-numeric `days` is rejected                                        |
//! | `overlong_make_is_400`                             | a `make` over 64 characters is rejected                                 |
//! | `control_character_in_model_is_400`                | a percent-encoded control character in `model` is rejected              |
//! | `first_duplicate_key_wins`                         | with `make` given twice, only the first value counts                    |
//! | `encoded_values_pass_validation`                   | `+` and `%20` decode to spaces and the request passes validation        |
//! | `non_get_is_405_json_on_both_routes`               | POST on either route is a JSON 405 with `no-store`                      |
//! | `head_is_served_by_the_get_handler`                | HEAD reaches the GET handler rather than returning 405                  |
//! | `valid_request_without_a_pool_is_503`              | a valid request with no database pool is a 503 `data unavailable`       |
//! | `errors_are_json`                                  | 400, 405 and 503 all carry `Content-Type: application/json`             |
//! | `unknown_route_is_404`                             | a path that is not a route is a 404                                     |
//! | `trailing_slash_is_404`                            | `/api/prices/` is not an alias for `/api/prices`                        |
//! | `request_id_is_generated_when_absent`              | a request with no `x-request-id` gets one on the response               |
//! | `request_id_from_caller_is_echoed`                 | a caller's `x-request-id` comes back unchanged                          |
//! | `rate_limit_returns_429_after_the_burst`           | past the burst a client gets a JSON 429 with `Retry-After`              |
//! | `rate_limit_is_per_client`                         | one client being throttled does not throttle another                    |
//! | `default_limits_do_not_throttle_normal_use`        | 60 quick requests from one client are all served under the defaults     |
//! | `requests_are_shed_when_no_capacity`               | with no in-flight capacity a request gets a JSON 503 `server busy`      |
//! | `database_backed_routes_return_200_with_cache_headers` | both routes return 200, the public cache header and the JSON shape  |

use axum::{
    body::Body,
    http::{Method, Request, StatusCode, header},
};
use http_body_util::BodyExt;
use prices_api::{
    db,
    limits::Limits,
    routes::{router, router_with},
    state::AppState,
};
use std::time::Duration;
use tower::ServiceExt;

/// GET with extra request headers, returning the whole response.
async fn get_with(
    app: &axum::Router,
    uri: &str,
    headers: &[(&str, &str)],
) -> axum::response::Response {
    let mut req = Request::builder().method(Method::GET).uri(uri);
    for (name, value) in headers {
        req = req.header(*name, *value);
    }
    app.clone()
        .oneshot(req.body(Body::empty()).unwrap())
        .await
        .unwrap()
}

/// Limits that never refill during a test, so the burst is exact.
fn tight(burst: u32, max_in_flight: usize) -> Limits {
    Limits {
        burst,
        replenish_every: Duration::from_secs(3600),
        max_in_flight,
    }
}

async fn send(app: axum::Router, method: Method, uri: &str) -> (StatusCode, String, String) {
    let res = app
        .oneshot(
            Request::builder()
                .method(method)
                .uri(uri)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = res.status();
    let cache = res
        .headers()
        .get(header::CACHE_CONTROL)
        .map(|v| v.to_str().unwrap().to_owned())
        .unwrap_or_default();
    let body = res.into_body().collect().await.unwrap().to_bytes();
    (status, cache, String::from_utf8(body.to_vec()).unwrap())
}

fn no_db() -> axum::Router {
    router(AppState::new(None))
}

#[tokio::test]
async fn missing_make_is_400_with_no_store() {
    let (status, cache, body) =
        send(no_db(), Method::GET, "/api/prices?model=Taycan&days=30").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(cache, "no-store");
    assert_eq!(body, r#"{"error":"missing required parameter `make`"}"#);
}

#[tokio::test]
async fn invalid_days_is_400() {
    let (status, _, body) = send(
        no_db(),
        Method::GET,
        "/api/prices?make=Porsche&model=Taycan&days=0",
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"invalid value for `days`"}"#);
}

#[tokio::test]
async fn days_is_required() {
    let (status, _, body) = send(no_db(), Method::GET, "/api/prices?make=A&model=B").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"missing required parameter `days`"}"#);
}

#[tokio::test]
async fn days_above_max_is_400() {
    let (status, _, body) =
        send(no_db(), Method::GET, "/api/prices?make=A&model=B&days=1096").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"invalid value for `days`"}"#);
}

#[tokio::test]
async fn days_must_be_an_integer() {
    let (status, _, body) = send(no_db(), Method::GET, "/api/prices?make=A&model=B&days=abc").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"invalid value for `days`"}"#);
}

#[tokio::test]
async fn overlong_make_is_400() {
    let uri = format!("/api/prices?make={}&model=B&days=30", "a".repeat(65));
    let (status, _, body) = send(no_db(), Method::GET, &uri).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"invalid value for `make`"}"#);
}

#[tokio::test]
async fn control_character_in_model_is_400() {
    let (status, _, body) = send(
        no_db(),
        Method::GET,
        "/api/prices?make=A&model=Tay%07can&days=30",
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"invalid value for `model`"}"#);
}

#[tokio::test]
async fn first_duplicate_key_wins() {
    // The first `make` is blank, so it is missing even though a later one has a value.
    let (status, _, body) = send(
        no_db(),
        Method::GET,
        "/api/prices?make=%20&make=Porsche&model=Taycan&days=30",
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(body, r#"{"error":"missing required parameter `make`"}"#);
}

#[tokio::test]
async fn encoded_values_pass_validation() {
    // Validation passed if the request reached the pool check (503) rather than failing with 400.
    let (status, _, _) = send(
        no_db(),
        Method::GET,
        "/api/prices?make=Hyundai&model=Ioniq+5&type=Premium%20Plus&days=30",
    )
    .await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn non_get_is_405_json_on_both_routes() {
    for uri in ["/api/prices", "/api/searches"] {
        let (status, cache, body) = send(no_db(), Method::POST, uri).await;
        assert_eq!(status, StatusCode::METHOD_NOT_ALLOWED, "{uri}");
        assert_eq!(cache, "no-store");
        assert_eq!(body, r#"{"error":"method not allowed"}"#);
    }
}

#[tokio::test]
async fn head_is_served_by_the_get_handler() {
    let (status, _, _) = send(no_db(), Method::HEAD, "/api/searches").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn valid_request_without_a_pool_is_503() {
    for uri in [
        "/api/prices?make=Porsche&model=Taycan&days=30",
        "/api/searches",
    ] {
        let (status, cache, body) = send(no_db(), Method::GET, uri).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{uri}");
        assert_eq!(cache, "no-store");
        assert_eq!(body, r#"{"error":"data unavailable"}"#);
    }
}

#[tokio::test]
async fn errors_are_json() {
    for (method, uri) in [
        (Method::GET, "/api/prices"),
        (Method::POST, "/api/searches"),
        (Method::GET, "/api/searches"),
    ] {
        let res = no_db()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(uri)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(
            res.headers().get(header::CONTENT_TYPE).unwrap(),
            "application/json",
            "{uri}"
        );
    }
}

#[tokio::test]
async fn unknown_route_is_404() {
    let (status, _, _) = send(no_db(), Method::GET, "/api/nope").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn trailing_slash_is_404() {
    let (status, _, _) = send(no_db(), Method::GET, "/api/prices/?make=A&model=B&days=30").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn request_id_is_generated_when_absent() {
    let res = get_with(&no_db(), "/api/searches", &[]).await;
    let id = res.headers().get("x-request-id").unwrap().to_str().unwrap();
    assert!(!id.is_empty());
}

#[tokio::test]
async fn request_id_from_caller_is_echoed() {
    let res = get_with(&no_db(), "/api/searches", &[("x-request-id", "abc-123")]).await;
    assert_eq!(res.headers().get("x-request-id").unwrap(), "abc-123");
}

#[tokio::test]
async fn rate_limit_returns_429_after_the_burst() {
    let app = router_with(AppState::new(None), tight(2, 64));
    let ip = [("x-real-ip", "203.0.113.7")];
    for _ in 0..2 {
        let res = get_with(&app, "/api/searches", &ip).await;
        assert_eq!(res.status(), StatusCode::SERVICE_UNAVAILABLE); // served (no pool), not throttled
    }
    let res = get_with(&app, "/api/searches", &ip).await;
    assert_eq!(res.status(), StatusCode::TOO_MANY_REQUESTS);
    assert_eq!(
        res.headers().get(header::CACHE_CONTROL).unwrap(),
        "no-store"
    );
    assert_eq!(
        res.headers().get(header::CONTENT_TYPE).unwrap(),
        "application/json"
    );
    assert!(res.headers().contains_key(header::RETRY_AFTER));
    assert!(res.headers().contains_key("x-request-id"));
    let body = res.into_body().collect().await.unwrap().to_bytes();
    assert_eq!(&body[..], br#"{"error":"too many requests"}"#);
}

#[tokio::test]
async fn rate_limit_is_per_client() {
    let app = router_with(AppState::new(None), tight(1, 64));
    let a = [("x-real-ip", "203.0.113.1")];
    let b = [("x-forwarded-for", "203.0.113.2, 10.0.0.1")];
    assert_eq!(
        get_with(&app, "/api/searches", &a).await.status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    assert_eq!(
        get_with(&app, "/api/searches", &a).await.status(),
        StatusCode::TOO_MANY_REQUESTS
    );
    assert_eq!(
        get_with(&app, "/api/searches", &b).await.status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
}

#[tokio::test]
async fn default_limits_do_not_throttle_normal_use() {
    let app = no_db();
    for _ in 0..60 {
        let res = get_with(&app, "/api/searches", &[("x-real-ip", "203.0.113.9")]).await;
        assert_ne!(res.status(), StatusCode::TOO_MANY_REQUESTS);
    }
}

#[tokio::test]
async fn requests_are_shed_when_no_capacity() {
    let app = router_with(AppState::new(None), tight(100, 0));
    let res = get_with(&app, "/api/searches", &[]).await;
    assert_eq!(res.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(res.headers().get(header::RETRY_AFTER).unwrap(), "1");
    let body = res.into_body().collect().await.unwrap().to_bytes();
    assert_eq!(&body[..], br#"{"error":"server busy"}"#);
}

#[tokio::test]
async fn database_backed_routes_return_200_with_cache_headers() {
    let Ok(url) = std::env::var("TEST_DATABASE_URL") else {
        return;
    };
    let pool = db::connect(&url).unwrap();
    sqlx::query(
        "CREATE TABLE IF NOT EXISTS daily_stats (make text, model text, model_type text, \
         stat_date date, avg_price_gbp integer)",
    )
    .execute(&pool)
    .await
    .unwrap();
    let state = AppState::new(Some(pool));
    state.warm_up(); // must not disturb the requests below
    let app = router(state);

    let (status, cache, body) = send(
        app.clone(),
        Method::GET,
        "/api/prices?make=nobody&model=nothing&days=7",
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "public, s-maxage=3600, stale-while-revalidate=86400");
    let json: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(json["series"], serde_json::json!([]));
    assert_eq!(json["days"], 7);

    let (status, cache, body) = send(app, Method::GET, "/api/searches").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(cache, "public, s-maxage=3600, stale-while-revalidate=86400");
    assert!(serde_json::from_str::<serde_json::Value>(&body).unwrap()["searches"].is_array());
}
