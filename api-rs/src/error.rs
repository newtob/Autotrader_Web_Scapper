//! One error type for every handler, mapped to the fixed JSON error bodies the API documents.

use axum::{
    http::{HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};

use crate::params::ParamError;
use crate::response::{CACHE_NONE, error_body};

#[derive(Debug)]
pub enum AppError {
    BadRequest(ParamError),
    MethodNotAllowed,
    /// No usable database pool, or a query failed. The body never carries driver text.
    Unavailable,
    /// The response body could not be serialised.
    Internal,
    /// The client exceeded its rate limit.
    RateLimited {
        retry_after_secs: u64,
    },
    /// Too many requests are already in flight; the request was shed.
    Busy,
}

impl From<ParamError> for AppError {
    fn from(e: ParamError) -> Self {
        Self::BadRequest(e)
    }
}

/// A JSON response with the given `Cache-Control`.
pub fn json_response(status: StatusCode, cache: &'static str, body: String) -> Response {
    (
        status,
        [
            (
                header::CONTENT_TYPE,
                HeaderValue::from_static("application/json"),
            ),
            (header::CACHE_CONTROL, HeaderValue::from_static(cache)),
        ],
        body,
    )
        .into_response()
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let mut retry_after = None;
        let (status, message) = match self {
            Self::BadRequest(e) => (StatusCode::BAD_REQUEST, e.to_string()),
            Self::MethodNotAllowed => (StatusCode::METHOD_NOT_ALLOWED, "method not allowed".into()),
            Self::Unavailable => (StatusCode::SERVICE_UNAVAILABLE, "data unavailable".into()),
            Self::Internal => (StatusCode::INTERNAL_SERVER_ERROR, "internal error".into()),
            Self::RateLimited { retry_after_secs } => {
                retry_after = Some(retry_after_secs);
                (StatusCode::TOO_MANY_REQUESTS, "too many requests".into())
            }
            Self::Busy => {
                retry_after = Some(1);
                (StatusCode::SERVICE_UNAVAILABLE, "server busy".into())
            }
        };
        let mut response = json_response(status, CACHE_NONE, error_body(&message));
        if let Some(secs) = retry_after {
            response
                .headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from(secs));
        }
        response
    }
}
