//! Read-only price API: validates a query, reads `daily_stats`, shapes the JSON response.

pub mod db;
pub mod error;
pub mod limits;
pub mod params;
pub mod response;
pub mod routes;
pub mod state;
pub mod telemetry;
