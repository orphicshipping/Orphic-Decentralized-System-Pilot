//! Presentation of the existing host render records. No numerical operations.
use serde::Serialize;
use serde_json::Value;
use std::{fs::File, io::Read, path::Path};

pub const CAPTION: &str = "presentation of retained values; meshes do not compute";
const MAX_BYTES: u64 = 16 * 1024 * 1024;
const MAX_POINTS: usize = 100_000;

#[derive(Clone, Debug, Serialize)]
pub struct PathLine {
    pub name: String,
    pub points: Vec<[f64; 3]>,
}

#[derive(Clone, Debug, Serialize)]
pub struct Presentation {
    pub source: String,
    pub source_available: bool,
    pub status: String,
    pub verification: String,
    pub caption: String,
    pub cards: Vec<String>,
    pub paths: Vec<PathLine>,
    pub may_authorize: bool,
}

fn short(text: &str) -> String {
    text.chars().filter(|c| !c.is_control() || *c == '\n').take(2048).collect()
}

fn card_text(name: &str, value: &Value) -> String {
    let Some(fields) = value.as_object() else {
        return short(&format!("{name}: {}", value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string())));
    };
    let title = fields.get("title").or_else(|| fields.get("label")).and_then(Value::as_str).unwrap_or(name);
    let mut lines = vec![title.to_string()];
    if let Some(value) = fields.get("value") {
        let value = value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string());
        lines[0] = format!("{title}: {value} {}", fields.get("unit").and_then(Value::as_str).unwrap_or(""));
    }
    for (key, value) in fields {
        if ["title", "label", "value", "unit", "may_authorize"].contains(&key.as_str()) { continue; }
        if value.is_string() || value.is_number() || value.is_boolean() {
            let value = value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string());
            lines.push(format!("{}: {value}", key.replace('_', " ")));
        }
    }
    short(&lines.join("\n"))
}

pub fn status_label(status: &str, fresh: bool) -> String {
    let status = status.trim().to_uppercase();
    match status.as_str() {
        "HISTORICAL" => "retained_runtime_report_requires_fresh_verification".into(),
        "VERIFIED" if !fresh => "retained_runtime_report_requires_fresh_verification".into(),
        "" => "UNAVAILABLE".into(),
        _ => short(&status),
    }
}

impl Presentation {
    pub fn load(path: &Path) -> Result<Self, String> {
        let file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(Self {
                    source: path.display().to_string(), source_available: false,
                    status: "STALE".into(), verification: "NOT_VERIFIED".into(),
                    caption: CAPTION.into(), cards: vec!["Input file is missing".into()],
                    paths: vec![], may_authorize: false,
                });
            }
            Err(error) => return Err(error.to_string()),
        };
        let mut bytes = vec![];
        file.take(MAX_BYTES + 1).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        if bytes.len() as u64 > MAX_BYTES { return Err("Render input exceeds 16 MiB".into()); }
        let data: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        Self::from_value(&data)
    }

    pub fn from_value(data: &Value) -> Result<Self, String> {
        let object = data.as_object().ok_or("Render input must be a JSON object")?;
        let fresh = data.get("fresh_verifier_occurrence") == Some(&Value::Bool(true));
        let mut result = Self {
            source: short(data.get("source").and_then(Value::as_str).unwrap_or("retained host JSON")),
            source_available: true,
            status: status_label(data.get("status").or_else(|| data.get("upstream_status"))
                .and_then(Value::as_str).unwrap_or("UNAVAILABLE"), fresh),
            verification: if fresh { "VERIFIED" } else { "NOT_VERIFIED" }.into(),
            caption: CAPTION.into(), cards: vec![], paths: vec![], may_authorize: false,
        };
        if let Some(caption) = data.get("caption").and_then(Value::as_str) {
            result.cards.push(short(caption));
        }
        if let Some(cards) = object.get("cards") {
            let values: Vec<(String, &Value)> = match cards {
                Value::Array(items) => items.iter().enumerate().map(|(i,v)|(i.to_string(),v)).collect(),
                Value::Object(items) => items.iter().map(|(k,v)|(k.clone(),v)).collect(),
                _ => return Err("cards must be an array or object".into()),
            };
            if values.len() > 256 { return Err("Render card limit exceeded".into()); }
            for (name, value) in values {
                let mut card = value.clone();
                if let Some(fields) = card.as_object_mut() {
                    for key in ["status", "verification_status"] {
                        if let Some(text) = fields.get(key).and_then(Value::as_str) {
                            fields.insert(key.into(), Value::String(status_label(text, fresh)));
                        }
                    }
                }
                result.cards.push(card_text(&name, &card));
            }
        }
        if let Some(paths) = data.get("trajectories") {
            let paths = paths.as_object().ok_or("trajectories must be an object")?;
            if paths.len() > 64 { return Err("Render path limit exceeded".into()); }
            for (name, path) in paths {
                if let Some(points) = path.get("points") { result.add_path(name, points)?; }
            }
        }
        for name in ["path", "points"] {
            if let Some(path) = data.get(name) {
                let points = path.get("points").unwrap_or(path);
                result.add_path(name, points)?;
            }
        }
        Ok(result)
    }

    fn add_path(&mut self, name: &str, values: &Value) -> Result<(), String> {
        let values = values.as_array().ok_or("path points must be an array")?;
        let used: usize = self.paths.iter().map(|p| p.points.len()).sum();
        if used + values.len() > MAX_POINTS { return Err("Render point limit exceeded".into()); }
        let mut points = Vec::with_capacity(values.len());
        for point in values {
            let row = point.as_array().ok_or("path point must be an array")?;
            if !(2..=3).contains(&row.len()) { return Err("path point must have 2 or 3 coordinates".into()); }
            let mut out = [0.0; 3];
            for (axis, number) in row.iter().enumerate() {
                let number = number.as_f64().ok_or("path coordinate must be a finite number")?;
                // Leave headroom for camera arithmetic; no solver or unit conversion.
                if !number.is_finite() || number.abs() > 1e15 { return Err("path coordinate outside display range".into()); }
                out[axis] = number;
            }
            points.push(out);
        }
        self.paths.push(PathLine { name: short(name), points });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    #[test]
    fn historical_and_verifier_boundary() {
        for flag in [json!(false), json!("true"), json!(1), Value::Null] {
            let p=Presentation::from_value(&json!({"status":"VERIFIED","fresh_verifier_occurrence":flag})).unwrap();
            assert_ne!(p.status,"VERIFIED"); assert_eq!(p.verification,"NOT_VERIFIED");
        }
        let p=Presentation::from_value(&json!({"status":"HISTORICAL","fresh_verifier_occurrence":true,"may_authorize":true})).unwrap();
        assert_eq!(p.status,"retained_runtime_report_requires_fresh_verification");
        assert_eq!(p.verification,"VERIFIED"); assert!(!p.may_authorize);
    }
    #[test]
    fn same_paths_and_both_card_shapes() {
        for cards in [json!([{"status":"VERIFIED"}]),json!({"residual":{"status":"HISTORICAL"}})] {
            let p=Presentation::from_value(&json!({"cards":cards,"trajectories":{"renewal":{"points":[[1,2,3],[4,5,6]]}}})).unwrap();
            assert_eq!(p.paths[0].points,vec![[1.,2.,3.],[4.,5.,6.]]);
            assert!(p.cards[0].contains("retained_runtime_report_requires_fresh_verification"));
        }
    }
    #[test]
    fn malformed_coordinates_are_refused() {
        for point in [json!([1]),json!([1,true]),json!([1,2,3,4]),json!([1e100,0]),json!("point")] {
            assert!(Presentation::from_value(&json!({"points":[point]})).is_err());
        }
        assert!(Presentation::from_value(&json!([])).is_err());
        assert!(Presentation::from_value(&json!({"cards":null})).is_err());
    }
    #[test]
    fn missing_input_is_stale() {
        let p=Presentation::load(&std::env::temp_dir().join(format!("absent-ncp-render-{}",std::process::id()))).unwrap();
        assert!(!p.source_available); assert_eq!(p.status,"STALE"); assert!(!p.may_authorize);
    }
    #[test]
    fn summary_preserves_retained_binary64_coordinates() {
        let value=1.2345678901234567_f64;
        let p=Presentation::from_value(&json!({"points":[[value,0,0],[2,0,0]]})).unwrap();
        let summary=serde_json::to_value(p).unwrap();
        assert_eq!(summary["paths"][0]["points"][0][0].as_f64().unwrap(),value);
    }
}
