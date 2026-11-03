// SPDX-License-Identifier: AGPL-3.0-or-later
#![forbid(unsafe_code)]
mod json;
mod policy;
use json::{J, R};
use std::io::{self, Read, Write};
const MAX_INPUT: usize = 2 * 1024 * 1024;
const MAX_OUTPUT: usize = 1024 * 1024;
pub fn digest(raw: &[u8]) -> String {
    execution_commitment::sha256(raw)
        .iter()
        .map(|n| format!("{n:02x}"))
        .collect()
}
fn run(raw: &[u8]) -> R<J> {
    let input = json::canonical_parse(std::str::from_utf8(raw).map_err(|_| "utf8")?, MAX_INPUT)?;
    input.keys(&[
        "schema",
        "text_encoding",
        "catalog_json",
        "catalog_sha256",
        "policy_json",
        "operation",
        "principal",
        "name",
        "arguments_json",
        "uri",
    ])?;
    if !input.get("schema")?.is("odsp.rust-policy.v1")
        || !input.get("text_encoding")?.is("odsp.surrogate-escape.v1")
    {
        return Err("protocol_version");
    }
    let catalog_raw = input.get("catalog_json")?.string()?;
    if !input
        .get("catalog_sha256")?
        .is(&digest(catalog_raw.as_bytes()))
    {
        return Err("catalog_binding");
    };
    let cat = json::transport_decode(&json::canonical_parse(&catalog_raw, 2 * 1024 * 1024)?)?;
    policy::catalog(&cat)?;
    if input.get("operation")?.is("catalog") {
        return Ok(J::s(&digest(catalog_raw.as_bytes())));
    }
    let snapshot = json::transport_decode(&json::canonical_parse(
        &input.get("policy_json")?.string()?,
        2 * 262144,
    )?)?;
    policy::validate(&snapshot, &cat)?;
    let principal = input.get("principal")?;
    match input.get("operation")?.string()?.as_str() {
        "snapshot" => Ok(snapshot),
        "check_principal" => Ok(policy::grant(&snapshot, principal)?.clone()),
        "context" => policy::context(&snapshot, principal),
        "authorize" | "authorize_context" => {
            let args = json::transport_decode(&json::canonical_parse(
                &input.get("arguments_json")?.string()?,
                2 * 65536,
            )?)?;
            let grant = policy::authorize(&snapshot, principal, input.get("name")?, &args, &cat)?;
            if input.get("operation")?.is("authorize_context") {
                policy::context(&snapshot, principal)
            } else {
                Ok(grant.clone())
            }
        }
        "authorize_resource" => {
            Ok(policy::authorize_resource(&snapshot, principal, input.get("uri")?, &cat)?.clone())
        }
        _ => Err("operation"),
    }
}
fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.len() == 2 && args[1] == "identity-v1" {
        io::stdout()
            .write_all(include_bytes!(concat!(
                env!("OUT_DIR"),
                "/build-identity.json"
            )))
            .unwrap();
        return;
    }
    let mut raw = Vec::new();
    let answer = if args.len() != 2 || args[1] != "policy-v1" {
        Err("protocol_argument")
    } else if io::stdin()
        .take((MAX_INPUT + 1) as u64)
        .read_to_end(&mut raw)
        .is_err()
    {
        Err("input_io")
    } else {
        run(&raw)
    };
    let reply = match answer {
        Ok(value) => {
            let result = json::transport_encode(&value).canonical();
            serde_json::json!({"schema":"odsp.rust-policy.reply.v1","status":"ok","input_sha256":digest(&raw),"result_sha256":digest(result.as_bytes()),"result_json":result,"proof_established":false})
        }
        Err(reason) => {
            serde_json::json!({"schema":"odsp.rust-policy.reply.v1","status":"refused","reason":reason,"proof_established":false})
        }
    };
    let mut encoded = serde_json::to_vec(&reply).unwrap();
    encoded.push(b'\n');
    if encoded.len() <= MAX_OUTPUT {
        io::stdout().write_all(&encoded).unwrap()
    } else {
        std::process::exit(2)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn duplicate_keys_refuse() {
        assert!(json::parse(br#"{"a":1,"\u0061":2}"#, 100).is_err());
    }
    #[test]
    fn unicode_tokens_preserved() {
        for s in [r#""\ud800""#, r#""\ud83c\udf0d""#, r#""\ud800x\udc00""#] {
            assert_eq!(json::canonical_parse(s, 100).unwrap().canonical(), s);
        }
    }
    #[test]
    fn transport_preserves_python_surrogates_and_scalar_keys() {
        let literal_pair = J::Str(vec![0xd83c, 0xdf0d]);
        let scalar = J::s("🌍");
        assert_ne!(literal_pair, scalar);
        // The old plain JSON representation is identical, which is why the
        // versioned inner transport must preserve the original code points.
        assert_eq!(literal_pair.canonical(), scalar.canonical());
        assert_ne!(
            json::transport_encode(&literal_pair).canonical(),
            json::transport_encode(&scalar).canonical()
        );
        let mut keys = std::collections::BTreeMap::new();
        keys.insert(vec![0xd83c, 0xdf0d], J::s("literal"));
        keys.insert(vec![0x1f30d], J::s("scalar"));
        keys.insert(json::text("~ud800;"), J::s("~~"));
        for original in [
            literal_pair,
            scalar,
            J::Obj(keys),
            J::s("~ud800;~"),
            J::Str(vec![0xd800]),
        ] {
            let wire = json::transport_encode(&original).canonical();
            let parsed = json::canonical_parse(&wire, 4096).unwrap();
            assert_eq!(json::transport_decode(&parsed).unwrap(), original);
        }
    }
    #[test]
    fn malformed_text_encoding_refuses() {
        for bad in ["~", "~u1234;", "~uD800;", "~ud800", "~xd800;", "~ud800:"] {
            assert!(json::transport_decode(&J::s(bad)).is_err(), "{bad}");
        }
        // Plain marker-looking text can only be accepted after its literal
        // tilde has been escaped; decoded text is never recursively decoded.
        assert_eq!(
            json::transport_decode(&J::s("~~ud800;")).unwrap(),
            J::s("~ud800;")
        );
    }
    #[test]
    fn typed_constraints_and_semantic_schema_equality() {
        let a = json::parse(b"1", 100).unwrap();
        let b = json::parse(b"1.0", 100).unwrap();
        assert!(!json::same(&a, &b));
        assert_ne!(a.canonical(), b.canonical());
        assert!(json::same(
            &json::parse(b"-0.0", 100).unwrap(),
            &json::parse(b"0.0", 100).unwrap()
        ));
    }
    #[test]
    fn integer_float_boundary_exact() {
        let a = json::parse(b"100000000000000000000", 100).unwrap();
        let b = json::parse(b"1e+20", 100).unwrap();
        assert_eq!(json::compare(&a, &b), Ok(std::cmp::Ordering::Equal));
        let c = json::parse(b"100000000000000000001", 100).unwrap();
        assert_eq!(json::compare(&c, &b), Ok(std::cmp::Ordering::Greater));
    }
    #[test]
    fn resource_routes_are_closed() {
        for s in [
            "ncp://runs/id/extra",
            "ncp://runs/../id",
            "ncp://runs/a?b",
            "ncp://evidence/123/overview",
        ] {
            assert!(policy::resource(&J::s(s)).is_err())
        }
    }
}
