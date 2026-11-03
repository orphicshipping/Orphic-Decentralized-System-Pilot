// SPDX-License-Identifier: AGPL-3.0-or-later
//! Native decisions corresponding to PrincipalPolicy, excluding the facade's
//! retained artifact, execution lifecycle and handoff side effects.
use crate::json::{self, Text, J, R};
use std::{cmp::Ordering, collections::BTreeSet};
macro_rules! require {
    ($condition:expr) => {
        if !$condition {
            return Err("policy_refused");
        }
    };
}
fn id(v: &J) -> R<String> {
    let s = v.string()?;
    require!(
        !s.is_empty()
            && s.len() <= 128
            && s.as_bytes()[0].is_ascii_alphanumeric()
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.:@-".contains(&b))
    );
    Ok(s)
}
fn strings(v: &J) -> R<()> {
    let a = v.array()?;
    require!(a.len() <= 256);
    let mut seen = BTreeSet::new();
    for n in a {
        let s = n.strv()?;
        require!(!s.is_empty() && s.len() <= 512 && seen.insert(s));
    }
    Ok(())
}
fn sha_ref(v: &J) -> bool {
    v.string().is_ok_and(|s| {
        s.len() == 71
            && s.starts_with("sha256:")
            && s.as_bytes()[7..]
                .iter()
                .all(|b| b.is_ascii_digit() || (*b >= b'a' && *b <= b'f'))
    })
}
fn path_name(v: &Text) -> bool {
    let Ok(s) = J::Str(v.clone()).string() else {
        return false;
    };
    !s.is_empty()
        && s.split('.').all(|p| {
            !p.is_empty()
                && (p.as_bytes()[0].is_ascii_alphabetic() || p.starts_with('_'))
                && p.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        })
}
fn contains(list: &J, value: &J) -> R<bool> {
    Ok(list.array()?.contains(value))
}

#[derive(Clone, Copy, PartialEq)]
enum Tree {
    Control,
    Shape,
    Campaign,
    Nise,
}
fn tree(v: &J, mode: Tree, depth: usize, nodes: &mut usize, bytes: &mut usize) -> R<()> {
    *nodes += 1;
    require!(depth <= 24);
    require!(
        *nodes
            <= if mode == Tree::Shape {
                262144
            } else if mode == Tree::Control {
                1048576
            } else {
                32768
            }
    );
    match v {
        J::Null | J::Bool(_) => {}
        J::Num(_) => {
            json::number_bound(v, if mode == Tree::Shape { f64::MAX } else { 1e150 })?;
            if mode == Tree::Campaign && v.integer().is_ok() {
                require!(
                    json::compare(v, &J::Num("9007199254740991".into()))? != Ordering::Greater
                        && json::compare(v, &J::Num("-9007199254740991".into()))? != Ordering::Less
                )
            }
        }
        J::Str(s) => {
            require!(s.len() <= if mode == Tree::Nise { 262144 } else { 65536 });
            if mode != Tree::Control {
                let u = v.string()?;
                *bytes += u.len();
                require!(*bytes <= 8 * 1024 * 1024);
            }
        }
        J::Arr(a) => {
            require!(
                a.len()
                    <= if mode == Tree::Campaign || mode == Tree::Nise {
                        4096
                    } else {
                        65536
                    }
            );
            for child in a {
                tree(child, mode, depth + 1, nodes, bytes)?
            }
        }
        J::Obj(o) => {
            require!(o.len() <= if mode == Tree::Nise { 2048 } else { 1024 });
            for (k, child) in o {
                require!(k.len() <= 512);
                if mode != Tree::Campaign {
                    require!(k.iter().any(|&c| !json::whitespace(c)))
                };
                if mode != Tree::Control {
                    tree(&J::Str(k.clone()), mode, depth + 1, nodes, bytes)?
                };
                tree(child, mode, depth + 1, nodes, bytes)?
            }
        }
    }
    Ok(())
}
fn bounded(v: &J, mode: Tree) -> R<()> {
    tree(v, mode, 0, &mut 0, &mut 0)
}
const SHAPE_KEYS: &[&str] = &[
    "type",
    "enum",
    "const",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "pattern",
    "anyOf",
    "oneOf",
];
fn schema(s: &J) -> R<()> {
    let o = s.object()?;
    require!(o
        .keys()
        .all(|k| SHAPE_KEYS.iter().any(|s| *k == json::text(s))));
    if let Some(k) = s.optional("type") {
        require!(
            ["string", "object", "integer", "array", "boolean", "null", "number"]
                .iter()
                .any(|t| k.is(t))
        )
    }
    if let Some(v) = s.optional("enum") {
        require!((1..=256).contains(&v.array()?.len()))
    }
    for (low, high) in [
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minimum", "maximum"),
    ] {
        for key in [low, high] {
            if let Some(v) = s.optional(key) {
                if low == "minimum" {
                    json::number_bound(v, 1e150)?
                } else {
                    json::count(v, 0, 9007199254740992)?;
                }
            }
        }
        if let (Some(a), Some(b)) = (s.optional(low), s.optional(high)) {
            require!(json::compare(a, b)? != Ordering::Greater)
        }
    }
    if let Some(v) = s.optional("pattern") {
        require!(v.is("^sha256:[0-9a-f]{64}$") || v.is("^sha256:[a-fA-F0-9]{64}$"))
    }
    if let Some(v) = s.optional("required") {
        let mut seen = BTreeSet::new();
        for name in v.array()? {
            require!(seen.insert(name.strv()?));
        }
    }
    if let Some(v) = s.optional("additionalProperties") {
        v.truth()?;
    }
    if let Some(v) = s.optional("properties") {
        for child in v.object()?.values() {
            schema(child)?
        }
    }
    if let Some(v) = s.optional("items") {
        schema(v)?
    }
    for key in ["anyOf", "oneOf"] {
        if let Some(v) = s.optional(key) {
            require!((1..=64).contains(&v.array()?.len()));
            for child in v.array()? {
                schema(child)?
            }
        }
    }
    Ok(())
}
fn tick(work: &mut usize) -> R<()> {
    require!(*work > 0);
    *work -= 1;
    Ok(())
}
fn same(a: &J, b: &J, work: &mut usize) -> R<bool> {
    tick(work)?;
    match (a, b) {
        (J::Arr(x), J::Arr(y)) => {
            if x.len() != y.len() {
                return Ok(false);
            }
            for (a, b) in x.iter().zip(y) {
                if !same(a, b, work)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        (J::Obj(x), J::Obj(y)) => {
            if x.keys().ne(y.keys()) {
                return Ok(false);
            }
            for (k, a) in x {
                if !same(a, &y[k], work)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        _ => Ok(json::same(a, b)),
    }
}
fn check_shape(v: &J, s: &J, work: &mut usize) -> R<bool> {
    tick(work)?;
    for key in ["anyOf", "oneOf"] {
        if let Some(branches) = s.optional(key) {
            let mut matches = 0;
            for branch in branches.array()? {
                if check_shape(v, branch, work)? {
                    matches += 1
                }
            }
            if matches == 0 || (key == "oneOf" && matches != 1) {
                return Ok(false);
            }
        }
    }
    if let Some(t) = s.optional("type") {
        let matched = match t.string()?.as_str() {
            "null" => matches!(v, J::Null),
            "boolean" => matches!(v, J::Bool(_)),
            "string" => matches!(v, J::Str(_)),
            "object" => matches!(v, J::Obj(_)),
            "array" => matches!(v, J::Arr(_)),
            "integer" => v.integer().is_ok(),
            "number" => matches!(v, J::Num(_)),
            _ => false,
        };
        if !matched {
            return Ok(false);
        }
    }
    if let Some(a) = s.optional("enum") {
        let mut matched = false;
        for item in a.array()? {
            if same(v, item, work)? {
                matched = true;
                break;
            }
        }
        if !matched {
            return Ok(false);
        }
    }
    if let Some(a) = s.optional("const") {
        if !same(v, a, work)? {
            return Ok(false);
        }
    }
    match v {
        J::Str(t) => {
            for (key, minimum) in [("minLength", true), ("maxLength", false)] {
                if let Some(b) = s.optional(key) {
                    let n = json::count(b, 0, 9007199254740992)?;
                    if if minimum { t.len() < n } else { t.len() > n } {
                        return Ok(false);
                    }
                }
            }
            if let Some(p) = s.optional("pattern") {
                let strv = v.string()?;
                if strv.len() != 71
                    || !strv.starts_with("sha256:")
                    || !strv.as_bytes()[7..].iter().all(|b| {
                        b.is_ascii_digit()
                            || (*b >= b'a' && *b <= b'f')
                            || (p.is("^sha256:[a-fA-F0-9]{64}$") && *b >= b'A' && *b <= b'F')
                    })
                {
                    return Ok(false);
                }
            }
        }
        J::Num(_) => {
            let d = if v.integer().is_ok() {
                "9007199254740992"
            } else {
                "1e+150"
            };
            let high = J::Num(d.into());
            let low = J::Num(format!("-{d}"));
            if json::compare(v, s.optional("minimum").unwrap_or(&low))? == Ordering::Less
                || json::compare(v, s.optional("maximum").unwrap_or(&high))? == Ordering::Greater
            {
                return Ok(false);
            }
        }
        J::Arr(a) => {
            for (key, min) in [("minItems", true), ("maxItems", false)] {
                if let Some(b) = s.optional(key) {
                    let n = json::count(b, 0, 9007199254740992)?;
                    if if min { a.len() < n } else { a.len() > n } {
                        return Ok(false);
                    }
                }
            }
            if let Some(item) = s.optional("items") {
                for child in a {
                    if !check_shape(child, item, work)? {
                        return Ok(false);
                    }
                }
            }
        }
        J::Obj(o) => {
            if let Some(req) = s.optional("required") {
                for k in req.array()? {
                    if !o.contains_key(k.strv()?) {
                        return Ok(false);
                    }
                }
            }
            let p = s.optional("properties");
            let additional = if let Some(b) = s.optional("additionalProperties") {
                b.truth()?
            } else {
                p.is_none()
            };
            for (k, v) in o {
                if let Some(child) = p.and_then(|p| p.object().ok()?.get(k)) {
                    if !check_shape(v, child, work)? {
                        return Ok(false);
                    }
                } else if !additional {
                    return Ok(false);
                }
            }
        }
        _ => {}
    }
    Ok(true)
}
pub fn catalog(v: &J) -> R<()> {
    v.keys(&["schema", "specs", "direct", "assessment_policy"])?;
    require!(v.get("schema")?.is("odsp.policy-catalog.v1"));
    let specs = v.get("specs")?.object()?;
    require!(!specs.is_empty() && specs.len() <= 256);
    for (name, s) in specs {
        require!(name.len() <= 128);
        let mut nodes = 0;
        tree(s, Tree::Shape, 0, &mut nodes, &mut 0)?;
        require!(nodes <= 4096);
        schema(s)?
    }
    let direct = v.get("direct")?;
    direct.keys(&["domain", "workbench", "fluid", "fixed"])?;
    for row in direct.get("domain")?.array()? {
        let a = row.array()?;
        require!(a.len() == 3);
        a[0].string()?;
        a[1].string()?;
        if a[2] != J::Null {
            a[2].string()?;
        }
    }
    for k in ["workbench", "fluid", "fixed"] {
        for value in direct.get(k)?.object()?.values() {
            if k == "workbench" {
                value.string()?;
            } else {
                strings(value)?;
            }
        }
    }
    bounded(v.get("assessment_policy")?, Tree::Control)?;
    Ok(())
}
pub fn validate(snapshot: &J, cat: &J) -> R<()> {
    bounded(snapshot, Tree::Control)?;
    require!(snapshot.canonical().len() <= 262144);
    snapshot.keys(&["schema", "revision", "principals"])?;
    require!(snapshot.get("schema")?.is("ciw.principal-policy.v1"));
    id(snapshot.get("revision")?)?;
    let grants = snapshot.get("principals")?.array()?;
    require!((1..=64).contains(&grants.len()));
    let mut principals = BTreeSet::new();
    for g in grants {
        g.keys(&[
            "principal",
            "enabled",
            "operations",
            "scientific_operations",
            "resources",
            "reference_scopes",
            "handoff_recipients",
        ])?;
        require!(principals.insert(id(g.get("principal")?)?));
        g.get("enabled")?.truth()?;
        let ops = g.get("operations")?.array()?;
        require!(ops.len() <= 256);
        let mut names = BTreeSet::new();
        for op in ops {
            op.keys(&["name", "constraints"])?;
            let name = op.get("name")?.strv()?;
            require!(cat.get("specs")?.object()?.contains_key(name) && names.insert(name));
            let constraints = op.get("constraints")?.object()?;
            require!(constraints.len() <= 32);
            for (path, allowed) in constraints {
                require!(path_name(path) && (1..=64).contains(&allowed.array()?.len()));
            }
        }
        for key in [
            "scientific_operations",
            "resources",
            "reference_scopes",
            "handoff_recipients",
        ] {
            strings(g.get(key)?)?
        }
        for value in g.get("reference_scopes")?.array()? {
            require!(sha_ref(value))
        }
        for value in g.get("handoff_recipients")?.array()? {
            id(value)?;
        }
        for value in g.get("resources")?.array()? {
            let p = value.strv()?;
            require!(
                p.starts_with(&json::text("ncp://"))
                    && !p.iter().any(|&c| c <= 32 || [37, 92, 63, 35].contains(&c))
                    && !p.windows(2).any(|s| s == [46, 46])
            )
        }
    }
    for g in grants {
        for r in g.get("handoff_recipients")?.array()? {
            require!(principals.contains(&r.string()?))
        }
    }
    Ok(())
}
pub fn grant<'a>(snapshot: &'a J, principal: &J) -> R<&'a J> {
    id(principal)?;
    let g = snapshot
        .get("principals")?
        .array()?
        .iter()
        .find(|g| g.optional("principal") == Some(principal))
        .ok_or("principal_refused")?;
    require!(g.get("enabled")?.truth()?);
    Ok(g)
}
#[derive(Clone, PartialEq)]
enum Part {
    Key(String),
    Index(usize),
}
fn keys(names: &[&str]) -> Vec<Part> {
    names.iter().map(|s| Part::Key(s.to_string())).collect()
}
fn at<'a>(mut value: &'a J, path: &[Part]) -> R<&'a J> {
    for p in path {
        value = match p {
            Part::Key(k) => value.get(k)?,
            Part::Index(i) => value.array()?.get(*i).ok_or("index")?,
        }
    }
    Ok(value)
}
fn schematic(path: &[Part], key: &str, value: &J, args: &J, cat: &J) -> bool {
    let steps = [
        vec![
            Part::Key("receipt".into()),
            Part::Key("bundle".into()),
            Part::Key("steps".into()),
            Part::Index(0),
        ],
        keys(&["receipt", "bundle", "verification", "reproduction"]),
    ];
    if key == "configuration" {
        return (path == keys(&["receipt", "bundle"])
            || steps.iter().any(|p| {
                let mut t = p.clone();
                t.push(Part::Key("request".into()));
                path == t
            }))
            && cat
                .get("assessment_policy")
                .is_ok_and(|p| value.canonical() == p.canonical());
    }
    if key != "owner" || !["jspt", "plsr", "rci", "cse"].iter().any(|s| value.is(s)) {
        return false;
    }
    let mut graphs = vec![keys(&["schematic"]), keys(&["request", "schematic"])];
    for step in steps {
        for suffix in [
            &["request", "schematic"][..],
            &["result", "data", "schematic"][..],
            &["numerical_result", "data", "schematic"][..],
        ] {
            let mut p = step.clone();
            p.extend(keys(suffix));
            graphs.push(p)
        }
        for result in ["result", "numerical_result"] {
            let mut p = step.clone();
            p.extend(keys(&[result, "data", "decisions"]));
            if path.len() == p.len() + 1
                && path[..p.len()] == p
                && matches!(path.last(), Some(Part::Index(_)))
            {
                return true;
            }
        }
    }
    for p in graphs {
        if path.len() == p.len() + 3
            && path[..p.len()] == p
            && path[path.len() - 3] == Part::Key("nodes".into())
            && matches!(path[path.len() - 2], Part::Index(_))
            && path[path.len() - 1] == Part::Key("attrs".into())
            && at(args, &path[..path.len() - 1])
                .is_ok_and(|v| v.optional("kind").is_some_and(|v| v.is("certificate")))
        {
            return true;
        }
    }
    false
}
const FORBIDDEN: &[&str] = &[
    "principal",
    "owner",
    "agent_id",
    "grants",
    "policy",
    "configuration",
    "checkout",
    "python",
    "python_executable",
    "provider_revision",
    "pin",
    "repository",
    "endpoint",
    "token",
    "policy_revision",
    "policy_sha256",
];
fn forbidden(name: &str, v: &J, args: &J, cat: &J, path: &mut Vec<Part>) -> R<()> {
    match v {
        J::Obj(o) => {
            for (k, c) in o {
                let key = J::Str(k.clone()).string()?;
                if FORBIDDEN.contains(&key.as_str())
                    && !(name == "ncp_compare" && path.is_empty() && key == "policy")
                {
                    require!(
                        name.starts_with("ncp_schematic_") && schematic(path, &key, c, args, cat)
                    )
                }
                path.push(Part::Key(key));
                forbidden(name, c, args, cat, path)?;
                path.pop();
            }
        }
        J::Arr(a) => {
            for (i, c) in a.iter().enumerate() {
                path.push(Part::Index(i));
                forbidden(name, c, args, cat, path)?;
                path.pop();
            }
        }
        _ => {}
    }
    Ok(())
}
pub fn request(name: &J, args: &J, cat: &J) -> R<()> {
    let n = name.string()?;
    args.object()?;
    let envelope = json::object(vec![("name", name.clone()), ("arguments", args.clone())]);
    bounded(&envelope, Tree::Control)?;
    require!(envelope.canonical().len() <= 65536);
    if [
        "ncp_intake_",
        "ncp_proof_",
        "ncp_domain_read_",
        "ncp_toolbox_",
        "ncp_skill_",
        "ncp_live_",
        "ncp_metrology_",
    ]
    .iter()
    .any(|p| n.starts_with(p))
    {
        bounded(&envelope, Tree::Campaign)?
    } else if n.starts_with("ncp_nise_") {
        bounded(&envelope, Tree::Nise)?
    }
    forbidden(&n, args, args, cat, &mut Vec::new())?;
    let s = cat
        .get("specs")?
        .object()?
        .get(name.strv()?)
        .ok_or("unknown_operation")?;
    bounded(args, Tree::Shape)?;
    require!(check_shape(args, s, &mut 1048576)?);
    Ok(())
}
fn rule(g: &J, name: &J, args: &J) -> R<()> {
    let r = g
        .get("operations")?
        .array()?
        .iter()
        .find(|r| r.optional("name") == Some(name))
        .ok_or("missing_grant")?;
    for (path, allowed) in r.get("constraints")?.object()? {
        let p = J::Str(path.clone()).string()?;
        let mut v = args;
        for key in p.split('.') {
            v = v.get(key)?
        }
        require!(allowed
            .array()?
            .iter()
            .any(|a| a.canonical() == v.canonical()));
    }
    Ok(())
}
fn science(name: &J, args: &J, cat: &J) -> R<Vec<J>> {
    let d = cat.get("direct")?;
    if name.is("ncp_domain_run") {
        let domain = args.get("domain")?;
        let selected = args.optional("name").unwrap_or(&J::Null);
        let rows: Vec<_> = d
            .get("domain")?
            .array()?
            .iter()
            .filter(|r| {
                r.array()
                    .is_ok_and(|a| a.get(1) == Some(domain) && a.get(2) == Some(selected))
            })
            .collect();
        require!(rows.len() == 1);
        return Ok(vec![rows[0].array()?[0].clone()]);
    }
    if name.is("ncp_workbench_run") {
        return Ok(vec![d
            .get("workbench")?
            .object()?
            .get(args.get("workflow")?.strv()?)
            .ok_or("workflow")?
            .clone()]);
    }
    if name.is("ncp_fluid_field") {
        return Ok(d
            .get("fluid")?
            .object()?
            .get(args.get("solver")?.strv()?)
            .ok_or("solver")?
            .array()?
            .clone());
    }
    if name.is("ncp_lesson") && args.optional("mode").is_some_and(|v| v.is("work")) {
        return Ok(vec![J::s("statistics.v1")]);
    }
    Ok(d.get("fixed")?
        .object()?
        .get(name.strv()?)
        .map(|v| v.array().cloned())
        .transpose()?
        .unwrap_or_default())
}
fn selected_science(g: &J, name: &J, args: &J, cat: &J) -> R<()> {
    for s in science(name, args, cat)? {
        require!(contains(g.get("scientific_operations")?, &s)?)
    }
    Ok(())
}
pub fn authorize<'a>(snapshot: &'a J, principal: &J, name: &J, args: &J, cat: &J) -> R<&'a J> {
    request(name, args, cat)?;
    let g = grant(snapshot, principal)?;
    rule(g, name, args)?;
    selected_science(g, name, args, cat)?;
    if name.is("ncp_coordinate") && args.get("action")?.is("submit") {
        let inner = args.get("operation")?;
        require!(inner.is("ncp_domain_run"));
        let empty = json::object(vec![]);
        let selected = args.optional("arguments").unwrap_or(&empty);
        request(inner, selected, cat)?;
        rule(g, inner, selected)?;
        selected_science(g, inner, selected, cat)?;
    }
    Ok(g)
}
pub fn resource(uri: &J) -> R<(J, J, Option<J>)> {
    let s = uri.string()?;
    require!(uri.strv()?.len() <= 1024);
    for (family, name, key) in [
        ("plans", "ncp_plan_get", "plan_id"),
        ("runs", "ncp_run_get", "run_id"),
        ("artifacts", "ncp_artifact_read", "artifact"),
        ("retained", "ncp_retained_read", "artifact_id"),
    ] {
        if let Some(rest) = s.strip_prefix(&format!("ncp://{family}/")) {
            if !rest.is_empty()
                && rest
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
            {
                return Ok((J::s(name), json::object(vec![(key, J::s(rest))]), None));
            }
        }
    }
    let rest = s.strip_prefix("ncp://").ok_or("resource")?;
    let a: Vec<_> = rest.split('/').collect();
    require!(a.len() == 3 || a.len() == 4);
    let family = a[0];
    require!(family == "proto-corpus" || family == "evidence");
    require!(sha_ref(&J::s(&format!("sha256:{}", a[1]))));
    let (prefix, key, route) = if family == "proto-corpus" {
        ("ncp_knowledge_", "record_id", "source")
    } else {
        ("ncp_evidence_", "span_id", "span")
    };
    let args = if a.len() == 3 && a[2] == "overview" {
        json::object(vec![])
    } else {
        require!(a.len() == 4 && a[2] == route && sha_ref(&J::s(&format!("sha256:{}", a[3]))));
        json::object(vec![(key, J::s(&format!("sha256:{}", a[3])))])
    };
    Ok((
        J::s(&format!("{prefix}{}", a[2])),
        args,
        Some(J::s(&format!("sha256:{}", a[1]))),
    ))
}
pub fn authorize_resource<'a>(snapshot: &'a J, principal: &J, uri: &J, cat: &J) -> R<&'a J> {
    let (name, args, scope) = resource(uri)?;
    let g = authorize(snapshot, principal, &name, &args, cat)?;
    let u = uri.string()?;
    require!(g.get("resources")?.array()?.iter().any(|p| p
        .string()
        .is_ok_and(|p| u == p || (p.ends_with('/') && u.starts_with(&p)))));
    if let Some(scope) = scope {
        require!(contains(g.get("reference_scopes")?, &scope)?)
    }
    Ok(g)
}
pub fn context(snapshot: &J, principal: &J) -> R<J> {
    grant(snapshot, principal)?;
    let p = principal.string()?;
    Ok(json::object(vec![
        ("schema", J::s("ciw.principal-context.v1")),
        ("principal", principal.clone()),
        (
            "owner",
            J::s(&format!("principal:{}", crate::digest(p.as_bytes()))),
        ),
        ("policy_revision", snapshot.get("revision")?.clone()),
        (
            "policy_sha256",
            J::s(&format!(
                "sha256:{}",
                crate::digest(snapshot.canonical().as_bytes())
            )),
        ),
    ]))
}
