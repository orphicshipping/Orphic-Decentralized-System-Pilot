//! AGPL-3.0-or-later. Stateless prefix classification, never authorization.
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    io::{self, Read, Write},
};
const INPUT: usize = 8 * 1024 * 1024;
const OUTPUT: usize = 4096;
const REQUESTS: usize = 4096;
const WIRE: u64 = 1024 * 1024 - 1024;
const SCHEMA: &str = "odsp.request-boundary.v1";
type R<T> = Result<T, &'static str>;
fn object<'a>(v: &'a Value, fields: &[&str]) -> R<&'a serde_json::Map<String, Value>> {
    let o = v.as_object().ok_or("contract_object")?;
    if o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)) {
        return Err("contract_fields");
    }
    Ok(o)
}
// After complete serde syntax validation, reject duplicate decoded keys instead
// of accepting Value's last-key behavior. This scan never canonicalizes data.
fn unique(s: &[u8], p: &mut usize, d: usize) -> R<()> {
    if d > 28 {
        return Err("contract_depth");
    }
    fn ws(s: &[u8], p: &mut usize) {
        while *p < s.len() && s[*p].is_ascii_whitespace() {
            *p += 1;
        }
    }
    fn string(s: &[u8], p: &mut usize) -> R<String> {
        let start = *p;
        *p += 1;
        let mut esc = false;
        while *p < s.len() {
            let b = s[*p];
            *p += 1;
            if !esc && b == b'"' {
                return serde_json::from_slice(&s[start..*p]).map_err(|_| "string");
            }
            if !esc && b == b'\\' {
                esc = true;
            } else {
                esc = false;
            }
        }
        Err("string")
    }
    ws(s, p);
    match s.get(*p).ok_or("syntax")? {
        b'{' => {
            *p += 1;
            ws(s, p);
            let mut seen = HashSet::new();
            if s.get(*p) == Some(&b'}') {
                *p += 1;
                return Ok(());
            }
            loop {
                ws(s, p);
                let k = string(s, p)?;
                if !seen.insert(k) {
                    return Err("duplicate_key");
                }
                ws(s, p);
                *p += 1;
                unique(s, p, d + 1)?;
                ws(s, p);
                let b = *s.get(*p).ok_or("syntax")?;
                *p += 1;
                if b == b'}' {
                    break;
                }
            }
        }
        b'[' => {
            *p += 1;
            ws(s, p);
            if s.get(*p) == Some(&b']') {
                *p += 1;
                return Ok(());
            }
            loop {
                unique(s, p, d + 1)?;
                ws(s, p);
                let b = *s.get(*p).ok_or("syntax")?;
                *p += 1;
                if b == b']' {
                    break;
                }
            }
        }
        b'"' => {
            string(s, p)?;
        }
        _ => {
            while *p < s.len() && !b",]} \t\r\n".contains(&s[*p]) {
                *p += 1;
            }
        }
    }
    Ok(())
}
fn id_key(v: &Value) -> Option<(u8, String)> {
    match v {
        Value::String(s) if (1..=128).contains(&s.chars().count()) => Some((0, s.clone())),
        Value::Number(_) => v
            .as_i64()
            .filter(|n| (-9007199254740991..=9007199254740991).contains(n))
            .map(|n| (1, n.to_string())),
        _ => None,
    }
}
fn result(decision: &str, code: Option<i64>, id: Value, consume: bool) -> Value {
    json!({"decision":decision,"code":code,"response_id":id,"consume_id":consume,"execution_authorized":false})
}
fn classify(doc: &Value) -> R<Value> {
    if doc.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Err("version");
    }
    match doc
        .get("operation")
        .and_then(Value::as_str)
        .ok_or("operation")?
    {
        "wire_output" => {
            object(doc, &["schema", "operation", "encoded_bytes"])?;
            let bytes = doc["encoded_bytes"].as_u64().ok_or("encoded_bytes")?;
            Ok(result(
                if bytes <= WIRE {
                    "output_candidate"
                } else {
                    "reject"
                },
                None,
                Value::Null,
                false,
            ))
        }
        "request_prefix" => {
            object(doc, &["schema", "operation", "message", "seen"])?;
            let entries = doc["seen"].as_array().ok_or("seen")?;
            if entries.len() > REQUESTS {
                return Err("snapshot_budget");
            }
            let mut seen = HashSet::new();
            for entry in entries {
                object(entry, &["kind", "value"])?;
                let key = id_key(&entry["value"]).ok_or("snapshot_id")?;
                let kind = entry["kind"].as_str().ok_or("snapshot_kind")?;
                if (key.0 == 0 && kind != "str") || (key.0 == 1 && kind != "int") {
                    return Err("snapshot_kind");
                }
                if !seen.insert(key) {
                    return Err("snapshot_duplicate");
                }
            }
            let Some(m) = doc["message"].as_object() else {
                return Ok(result("reject", Some(-32600), Value::Null, false));
            };
            let notification = !m.contains_key("id");
            if m.keys()
                .any(|k| !["jsonrpc", "id", "method", "params"].contains(&k.as_str()))
                || m.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
                || m.get("method")
                    .and_then(Value::as_str)
                    .is_none_or(|s| s.is_empty())
            {
                return Ok(result("reject", Some(-32600), Value::Null, false));
            }
            let id = m.get("id").cloned().unwrap_or(Value::Null);
            let key = if notification {
                None
            } else {
                match id_key(&id) {
                    Some(k) => Some(k),
                    None => return Ok(result("reject", Some(-32600), Value::Null, false)),
                }
            };
            if m.get("params").is_some_and(|v| !v.is_object()) {
                return Ok(if notification {
                    result("notification", None, Value::Null, false)
                } else {
                    result("reject", Some(-32602), id, false)
                });
            }
            if notification {
                return Ok(result("notification", None, Value::Null, false));
            }
            if seen.contains(&key.unwrap()) {
                return Ok(result("reject", Some(-32600), id, false));
            }
            if seen.len() >= REQUESTS {
                return Ok(result("reject", Some(-32000), id, false));
            }
            Ok(result("candidate", None, id, true))
        }
        _ => Err("operation"),
    }
}
fn run(raw: &[u8]) -> R<Value> {
    if raw.is_empty() || raw.len() > INPUT {
        return Err("input_budget");
    }
    let doc: Value = serde_json::from_slice(raw).map_err(|_| "json_or_unsupported_scalar")?;
    unique(raw, &mut 0, 0)?;
    classify(&doc)
}
fn main() {
    let mut raw = Vec::new();
    let arguments: Vec<_> = std::env::args().collect();
    let answer = if arguments.len() != 2 || arguments[1] != "boundary-v1" {
        Err("protocol_argument")
    } else if io::stdin()
        .take((INPUT + 1) as u64)
        .read_to_end(&mut raw)
        .is_err()
    {
        Err("input_io")
    } else {
        run(&raw)
    };
    let reply = match answer {
        Ok(v) => json!({"schema":"odsp.request-boundary.reply.v1","status":"ok","result":v}),
        Err(e) => {
            json!({"schema":"odsp.request-boundary.reply.v1","status":"unsupported","reason":e,"fallback":"python_required","execution_authorized":false})
        }
    };
    let mut bytes = serde_json::to_vec(&reply).unwrap();
    bytes.push(b'\n');
    if bytes.len() <= OUTPUT {
        let _ = io::stdout().lock().write_all(&bytes);
    } else {
        let _=io::stdout().lock().write_all(b"{\"schema\":\"odsp.request-boundary.reply.v1\",\"status\":\"unsupported\",\"reason\":\"output_budget\",\"fallback\":\"python_required\",\"execution_authorized\":false}\n");
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn integer_types() {
        assert!(id_key(&json!(true)).is_none());
        assert!(id_key(&serde_json::from_str::<Value>("1.0").unwrap()).is_none());
        assert!(id_key(&json!(9007199254740992i64)).is_none());
    }
    #[test]
    fn duplicates() {
        assert_eq!(
            run(br#"{"schema":"a","schema":"b"}"#).unwrap_err(),
            "duplicate_key"
        );
    }
    #[test]
    fn notification() {
        let v = json!({"schema":SCHEMA,"operation":"request_prefix","message":{"jsonrpc":"2.0","method":"tools/call"},"seen":[]});
        assert_eq!(classify(&v).unwrap()["decision"], "notification");
    }
    #[test]
    fn wire() {
        for (n, decision) in [(WIRE, "output_candidate"), (WIRE + 1, "reject")] {
            assert_eq!(
                classify(&json!({"schema":SCHEMA,"operation":"wire_output","encoded_bytes":n}))
                    .unwrap()["decision"],
                decision
            );
        }
    }
}
