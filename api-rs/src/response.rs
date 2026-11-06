//! JSON bodies and cache policy for `GET /api/prices`.

use chrono::NaiveDate;
use serde::Serialize;

use crate::params::PriceQuery;

pub const CACHE_OK: &str = "public, s-maxage=3600, stale-while-revalidate=86400";
pub const CACHE_NONE: &str = "no-store";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Point {
    pub date: NaiveDate,
    pub avg_price_gbp: i32,
}

#[derive(Debug, Serialize)]
pub struct PriceResponse<'a> {
    pub make: &'a str,
    pub model: &'a str,
    #[serde(rename = "type")]
    pub model_type: &'a str,
    pub days: u32,
    pub series: &'a [Point],
}

impl<'a> PriceResponse<'a> {
    pub fn new(query: &'a PriceQuery, series: &'a [Point]) -> Self {
        Self {
            make: &query.make,
            model: &query.model,
            model_type: &query.model_type,
            days: query.days,
            series,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SearchSummary {
    pub make: String,
    pub model: String,
    #[serde(rename = "type")]
    pub model_type: String,
    pub latest_date: NaiveDate,
}

#[derive(Debug, Serialize)]
pub struct SearchesResponse<'a> {
    pub searches: &'a [SearchSummary],
}

/// Fixed error body; never carries driver or connection text.
pub fn error_body(message: &str) -> String {
    serde_json::json!({ "error": message }).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serialises_documented_shape() {
        let q = PriceQuery::parse("make=Porsche&model=Taycan&type=4S&days=30").unwrap();
        let series = [Point {
            date: NaiveDate::from_ymd_opt(2026, 11, 4).unwrap(),
            avg_price_gbp: 44820,
        }];
        let json = serde_json::to_value(PriceResponse::new(&q, &series)).unwrap();
        assert_eq!(
            json,
            serde_json::json!({
                "make": "Porsche", "model": "Taycan", "type": "4S", "days": 30,
                "series": [{ "date": "2026-11-04", "avg_price_gbp": 44820 }]
            })
        );
    }

    #[test]
    fn empty_series_is_an_empty_array() {
        let q = PriceQuery::parse("make=A&model=B&days=7").unwrap();
        let json = serde_json::to_value(PriceResponse::new(&q, &[])).unwrap();
        assert_eq!(json["series"], serde_json::json!([]));
        assert_eq!(json["type"], "");
    }

    #[test]
    fn searches_serialise_documented_shape() {
        let searches = [SearchSummary {
            make: "Porsche".into(),
            model: "Taycan".into(),
            model_type: String::new(),
            latest_date: NaiveDate::from_ymd_opt(2026, 11, 5).unwrap(),
        }];
        let json = serde_json::to_value(SearchesResponse {
            searches: &searches,
        })
        .unwrap();
        assert_eq!(
            json,
            serde_json::json!({ "searches": [
                { "make": "Porsche", "model": "Taycan", "type": "", "latest_date": "2026-11-05" }
            ]})
        );
    }

    #[test]
    fn error_body_is_fixed_json() {
        assert_eq!(
            error_body("data unavailable"),
            r#"{"error":"data unavailable"}"#
        );
    }
}
