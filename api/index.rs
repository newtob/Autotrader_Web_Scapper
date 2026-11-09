//! Single Vercel function: every `/api/*` request is rewritten here and routed by Axum.

use prices_api::{routes::router, state::AppState, telemetry};
use tower::ServiceBuilder;
use vercel_runtime::{Error, axum::VercelLayer, run};

#[tokio::main]
async fn main() -> Result<(), Error> {
    telemetry::init();
    let service = ServiceBuilder::new()
        .layer(VercelLayer::new())
        .service(router(AppState::from_env()));
    run(service).await
}
