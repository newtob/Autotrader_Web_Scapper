//! Query-string parsing and validation for `GET /api/prices`.

use std::fmt;

pub const MAX_DAYS: u32 = 1095;
const MAX_TEXT_LEN: usize = 64;

/// A validated price query. `model_type` is `""` when no trim was given (as stored in `daily_stats`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PriceQuery {
    pub make: String,
    pub model: String,
    pub model_type: String,
    pub days: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParamError {
    Missing(&'static str),
    Invalid(&'static str),
}

impl fmt::Display for ParamError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Missing(name) => write!(f, "missing required parameter `{name}`"),
            Self::Invalid(name) => write!(f, "invalid value for `{name}`"),
        }
    }
}

impl std::error::Error for ParamError {}

fn text(value: Option<String>, name: &'static str, required: bool) -> Result<String, ParamError> {
    let value = value.unwrap_or_default();
    let value = value.trim();
    if value.is_empty() {
        return if required {
            Err(ParamError::Missing(name))
        } else {
            Ok(String::new())
        };
    }
    if value.chars().count() > MAX_TEXT_LEN || value.chars().any(char::is_control) {
        return Err(ParamError::Invalid(name));
    }
    Ok(value.to_owned())
}

impl PriceQuery {
    /// Parse a raw query string (without the leading `?`). The first occurrence of a key wins.
    pub fn parse(query: &str) -> Result<Self, ParamError> {
        let (mut make, mut model, mut model_type, mut days) = (None, None, None, None);
        for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
            let slot = match key.as_ref() {
                "make" => &mut make,
                "model" => &mut model,
                "type" => &mut model_type,
                "days" => &mut days,
                _ => continue,
            };
            slot.get_or_insert(value.into_owned());
        }

        let days = days.ok_or(ParamError::Missing("days"))?;
        let days: u32 = days
            .trim()
            .parse()
            .map_err(|_| ParamError::Invalid("days"))?;
        if !(1..=MAX_DAYS).contains(&days) {
            return Err(ParamError::Invalid("days"));
        }

        Ok(Self {
            make: text(make, "make", true)?,
            model: text(model, "model", true)?,
            model_type: text(model_type, "type", false)?,
            days,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_full_query() {
        let q = PriceQuery::parse("make=Porsche&model=Taycan&type=4S&days=30").unwrap();
        assert_eq!(
            q,
            PriceQuery {
                make: "Porsche".into(),
                model: "Taycan".into(),
                model_type: "4S".into(),
                days: 30
            }
        );
    }

    #[test]
    fn type_is_optional() {
        assert_eq!(
            PriceQuery::parse("make=A&model=B&days=7")
                .unwrap()
                .model_type,
            ""
        );
        assert_eq!(
            PriceQuery::parse("make=A&model=B&type=&days=7")
                .unwrap()
                .model_type,
            ""
        );
    }

    #[test]
    fn decodes_and_trims() {
        let q = PriceQuery::parse("make=Land%20Rover&model=+Range+Rover+&days=7").unwrap();
        assert_eq!(q.make, "Land Rover");
        assert_eq!(q.model, "Range Rover");
    }

    #[test]
    fn missing_required() {
        assert_eq!(
            PriceQuery::parse("model=B&days=7"),
            Err(ParamError::Missing("make"))
        );
        assert_eq!(
            PriceQuery::parse("make=A&days=7"),
            Err(ParamError::Missing("model"))
        );
        assert_eq!(
            PriceQuery::parse("make=%20&model=B&days=7"),
            Err(ParamError::Missing("make"))
        );
        assert_eq!(
            PriceQuery::parse("make=A&model=B"),
            Err(ParamError::Missing("days"))
        );
    }

    #[test]
    fn days_bounds() {
        for bad in ["0", "1096", "-1", "abc", "1.5", ""] {
            assert_eq!(
                PriceQuery::parse(&format!("make=A&model=B&days={bad}")),
                Err(ParamError::Invalid("days")),
                "days={bad}"
            );
        }
        assert_eq!(PriceQuery::parse("make=A&model=B&days=1").unwrap().days, 1);
        assert_eq!(
            PriceQuery::parse("make=A&model=B&days=1095").unwrap().days,
            1095
        );
    }

    #[test]
    fn rejects_long_and_control_chars() {
        let long = "x".repeat(65);
        assert_eq!(
            PriceQuery::parse(&format!("make={long}&model=B&days=7")),
            Err(ParamError::Invalid("make"))
        );
        assert_eq!(
            PriceQuery::parse("make=A%00&model=B&days=7"),
            Err(ParamError::Invalid("make"))
        );
        assert_eq!(
            PriceQuery::parse("make=A&model=B&type=%0A4S&days=7")
                .unwrap()
                .model_type,
            "4S"
        );
    }

    #[test]
    fn first_occurrence_wins() {
        assert_eq!(
            PriceQuery::parse("make=A&make=Z&model=B&days=7")
                .unwrap()
                .make,
            "A"
        );
    }
}
