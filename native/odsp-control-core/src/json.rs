// SPDX-License-Identifier: AGPL-3.0-or-later
//! The host wire carries Python's canonical ASCII JSON, not JCS. Keep numeric
//! lexemes and lone surrogate code points: neither may silently change a grant.
use num_bigint::BigInt;
use num_traits::{FromPrimitive, Signed, ToPrimitive};
use std::{cmp::Ordering, collections::BTreeMap};
pub type R<T> = Result<T, &'static str>;
pub type Text = Vec<u32>;
#[derive(Clone, Debug, PartialEq)]
pub enum J {
    Null,
    Bool(bool),
    Str(Text),
    Num(String),
    Arr(Vec<J>),
    Obj(BTreeMap<Text, J>),
}
pub fn text(s: &str) -> Text {
    s.chars().map(|c| c as u32).collect()
}

// JSON's escaped surrogate pair is ambiguous between two literal Python
// surrogate code points and one astral scalar. The inner transport masks literal
// surrogates and tildes before JSON encoding; canonical scientific bytes are
// calculated only after this reversible decoding.
fn map_text(value: &J, transform: &impl Fn(&Text) -> R<Text>) -> R<J> {
    Ok(match value {
        J::Str(v) => J::Str(transform(v)?),
        J::Arr(a) => J::Arr(a.iter().map(|v| map_text(v, transform)).collect::<R<_>>()?),
        J::Obj(o) => {
            let mut result = BTreeMap::new();
            for (key, value) in o {
                if result
                    .insert(transform(key)?, map_text(value, transform)?)
                    .is_some()
                {
                    return Err("duplicate_decoded_key");
                }
            }
            J::Obj(result)
        }
        _ => value.clone(),
    })
}
pub fn transport_encode(value: &J) -> J {
    map_text(value, &|s| {
        let mut output = Vec::new();
        for &c in s {
            if c == 126 {
                output.extend(text("~~"));
            } else if (0xd800..=0xdfff).contains(&c) {
                output.extend(text(&format!("~u{c:04x};")));
            } else {
                output.push(c);
            }
        }
        Ok(output)
    })
    .expect("encoding is injective")
}
pub fn transport_decode(value: &J) -> R<J> {
    map_text(value, &|s| {
        let mut output = Vec::new();
        let mut offset = 0;
        while offset < s.len() {
            if s[offset] != 126 {
                output.push(s[offset]);
                offset += 1;
            } else if s.get(offset + 1) == Some(&126) {
                output.push(126);
                offset += 2;
            } else {
                let part = s.get(offset..offset + 7).ok_or("text_encoding")?;
                if part[1] != 117
                    || part[6] != 59
                    || !part[2..6]
                        .iter()
                        .all(|&c| (48..=57).contains(&c) || (97..=102).contains(&c))
                {
                    return Err("text_encoding");
                }
                let hex: String = part[2..6]
                    .iter()
                    .map(|&c| char::from_u32(c).unwrap())
                    .collect();
                let code = u32::from_str_radix(&hex, 16).map_err(|_| "text_encoding")?;
                if !(0xd800..=0xdfff).contains(&code) {
                    return Err("text_encoding");
                }
                output.push(code);
                offset += 7;
            }
        }
        Ok(output)
    })
}
impl J {
    pub fn s(s: &str) -> J {
        J::Str(text(s))
    }
    pub fn string(&self) -> R<String> {
        if let J::Str(v) = self {
            v.iter()
                .map(|&n| char::from_u32(n).ok_or("unicode"))
                .collect()
        } else {
            Err("string")
        }
    }
    pub fn strv(&self) -> R<&Text> {
        if let J::Str(v) = self {
            Ok(v)
        } else {
            Err("string")
        }
    }
    pub fn object(&self) -> R<&BTreeMap<Text, J>> {
        if let J::Obj(v) = self {
            Ok(v)
        } else {
            Err("object")
        }
    }
    pub fn array(&self) -> R<&Vec<J>> {
        if let J::Arr(v) = self {
            Ok(v)
        } else {
            Err("array")
        }
    }
    pub fn get(&self, k: &str) -> R<&J> {
        self.object()?.get(&text(k)).ok_or("field")
    }
    pub fn optional(&self, k: &str) -> Option<&J> {
        self.object().ok()?.get(&text(k))
    }
    pub fn is(&self, s: &str) -> bool {
        matches!(self,J::Str(v) if *v==text(s))
    }
    pub fn truth(&self) -> R<bool> {
        if let J::Bool(v) = self {
            Ok(*v)
        } else {
            Err("boolean")
        }
    }
    pub fn integer(&self) -> R<BigInt> {
        if let J::Num(v) = self {
            if !v.contains(['.', 'e', 'E']) {
                return v.parse().map_err(|_| "integer");
            }
        }
        Err("integer")
    }
    pub fn float(&self) -> R<f64> {
        if let J::Num(v) = self {
            let n: f64 = v.parse().map_err(|_| "number")?;
            if n.is_finite() {
                return Ok(n);
            }
        }
        Err("number")
    }
    pub fn keys(&self, names: &[&str]) -> R<()> {
        let o = self.object()?;
        if o.len() == names.len() && names.iter().all(|k| o.contains_key(&text(k))) {
            Ok(())
        } else {
            Err("fields")
        }
    }
    pub fn canonical(&self) -> String {
        match self {
            J::Null => "null".into(),
            J::Bool(v) => v.to_string(),
            J::Num(v) => v.clone(),
            J::Str(v) => quote(v),
            J::Arr(v) => format!(
                "[{}]",
                v.iter().map(J::canonical).collect::<Vec<_>>().join(",")
            ),
            J::Obj(v) => format!(
                "{{{}}}",
                v.iter()
                    .map(|(k, v)| format!("{}:{}", quote(k), v.canonical()))
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        }
    }
}
pub fn object(rows: Vec<(&str, J)>) -> J {
    J::Obj(rows.into_iter().map(|(k, v)| (text(k), v)).collect())
}
fn quote(v: &[u32]) -> String {
    let mut out = String::from("\"");
    for &c in v {
        match c {
            8 => out.push_str("\\b"),
            9 => out.push_str("\\t"),
            10 => out.push_str("\\n"),
            12 => out.push_str("\\f"),
            13 => out.push_str("\\r"),
            34 => out.push_str("\\\""),
            92 => out.push_str("\\\\"),
            32..=126 => out.push(char::from_u32(c).unwrap()),
            0..=65535 => out.push_str(&format!("\\u{c:04x}")),
            _ => {
                let n = c - 65536;
                out.push_str(&format!(
                    "\\u{:04x}\\u{:04x}",
                    0xd800 + (n >> 10),
                    0xdc00 + (n & 1023)
                ))
            }
        }
    }
    out.push('"');
    out
}
struct Parser<'a> {
    raw: &'a [u8],
    at: usize,
    nodes: usize,
}
impl Parser<'_> {
    fn ws(&mut self) {
        while self.at < self.raw.len() && b" \n\r\t".contains(&self.raw[self.at]) {
            self.at += 1
        }
    }
    fn take(&mut self, c: u8) -> R<()> {
        if self.raw.get(self.at) == Some(&c) {
            self.at += 1;
            Ok(())
        } else {
            Err("syntax")
        }
    }
    fn hex(&mut self) -> R<u32> {
        if self.at + 4 > self.raw.len() {
            return Err("escape");
        };
        let s = std::str::from_utf8(&self.raw[self.at..self.at + 4]).map_err(|_| "escape")?;
        self.at += 4;
        u32::from_str_radix(s, 16).map_err(|_| "escape")
    }
    fn string(&mut self) -> R<Text> {
        self.take(b'"')?;
        let mut out = Vec::new();
        loop {
            let b = *self.raw.get(self.at).ok_or("string")?;
            self.at += 1;
            match b {
                b'"' => break,
                b'\\' => {
                    let e = *self.raw.get(self.at).ok_or("escape")?;
                    self.at += 1;
                    out.push(match e {
                        b'"' => 34,
                        b'\\' => 92,
                        b'/' => 47,
                        b'b' => 8,
                        b'f' => 12,
                        b'n' => 10,
                        b'r' => 13,
                        b't' => 9,
                        b'u' => {
                            let h = self.hex()?;
                            if (0xd800..=0xdbff).contains(&h)
                                && self.raw.get(self.at..self.at + 2) == Some(b"\\u")
                            {
                                let saved = self.at;
                                self.at += 2;
                                let l = self.hex()?;
                                if (0xdc00..=0xdfff).contains(&l) {
                                    0x10000 + ((h - 0xd800) << 10) + (l - 0xdc00)
                                } else {
                                    self.at = saved;
                                    h
                                }
                            } else {
                                h
                            }
                        }
                        _ => return Err("escape"),
                    })
                }
                0..=31 | 128..=255 => return Err("ascii_wire"),
                _ => out.push(b as u32),
            }
        }
        Ok(out)
    }
    fn value(&mut self, depth: usize) -> R<J> {
        self.nodes += 1;
        if depth > 64 || self.nodes > 1048576 {
            return Err("parser_budget");
        };
        self.ws();
        let b = *self.raw.get(self.at).ok_or("syntax")?;
        match b {
            b'"' => Ok(J::Str(self.string()?)),
            b'{' => {
                self.at += 1;
                self.ws();
                let mut o = BTreeMap::new();
                if self.raw.get(self.at) == Some(&b'}') {
                    self.at += 1;
                    return Ok(J::Obj(o));
                }
                loop {
                    self.ws();
                    let k = self.string()?;
                    self.ws();
                    self.take(b':')?;
                    let v = self.value(depth + 1)?;
                    if o.insert(k, v).is_some() {
                        return Err("duplicate_key");
                    };
                    self.ws();
                    if self.raw.get(self.at) == Some(&b'}') {
                        self.at += 1;
                        break;
                    };
                    self.take(b',')?;
                }
                Ok(J::Obj(o))
            }
            b'[' => {
                self.at += 1;
                self.ws();
                let mut v = Vec::new();
                if self.raw.get(self.at) == Some(&b']') {
                    self.at += 1;
                    return Ok(J::Arr(v));
                }
                loop {
                    v.push(self.value(depth + 1)?);
                    self.ws();
                    if self.raw.get(self.at) == Some(&b']') {
                        self.at += 1;
                        break;
                    };
                    self.take(b',')?;
                }
                Ok(J::Arr(v))
            }
            b'n' | b't' | b'f' => {
                let (s, v) = match b {
                    b'n' => (b"null".as_slice(), J::Null),
                    b't' => (b"true".as_slice(), J::Bool(true)),
                    _ => (b"false".as_slice(), J::Bool(false)),
                };
                if self.raw.get(self.at..self.at + s.len()) != Some(s) {
                    return Err("literal");
                };
                self.at += s.len();
                Ok(v)
            }
            b'-' | b'0'..=b'9' => {
                let start = self.at;
                while self.at < self.raw.len() && b"0123456789.eE+-".contains(&self.raw[self.at]) {
                    self.at += 1
                }
                let s = std::str::from_utf8(&self.raw[start..self.at]).map_err(|_| "number")?;
                if s.len() > 512 || serde_json::from_str::<serde_json::Number>(s).is_err() {
                    return Err("number");
                };
                Ok(J::Num(s.into()))
            }
            _ => Err("syntax"),
        }
    }
}
pub fn parse(raw: &[u8], maximum: usize) -> R<J> {
    if raw.is_empty() || raw.len() > maximum {
        return Err("input_budget");
    };
    let mut p = Parser {
        raw,
        at: 0,
        nodes: 0,
    };
    let v = p.value(0)?;
    p.ws();
    if p.at != raw.len() {
        return Err("trailing_data");
    };
    Ok(v)
}
pub fn canonical_parse(raw: &str, maximum: usize) -> R<J> {
    let value = parse(raw.as_bytes(), maximum)?;
    if value.canonical() != raw {
        return Err("canonical_wire");
    };
    Ok(value)
}
fn fraction(value: &J) -> R<(BigInt, i32)> {
    if let Ok(n) = value.integer() {
        return Ok((n, 0));
    }
    let f = value.float()?;
    let bits = f.to_bits();
    let exponent = ((bits >> 52) & 2047) as i32;
    let mantissa = (bits & ((1 << 52) - 1)) | if exponent == 0 { 0 } else { 1 << 52 };
    let n = BigInt::from(mantissa) * if bits >> 63 == 1 { -1 } else { 1 };
    Ok((
        n,
        if exponent == 0 {
            -1074
        } else {
            exponent - 1023 - 52
        },
    ))
}
pub fn compare(a: &J, b: &J) -> R<Ordering> {
    let (mut x, xe) = fraction(a)?;
    let (mut y, ye) = fraction(b)?;
    let low = xe.min(ye);
    x <<= (xe - low) as usize;
    y <<= (ye - low) as usize;
    Ok(x.cmp(&y))
}
pub fn number_bound(v: &J, maximum: f64) -> R<()> {
    if let Ok(n) = v.integer() {
        if n.abs() > BigInt::from_f64(maximum).ok_or("number")? {
            return Err("number_bound");
        }
    } else if v.float()?.abs() > maximum {
        return Err("number_bound");
    };
    Ok(())
}
pub fn count(v: &J, minimum: usize, maximum: usize) -> R<usize> {
    let n = v.integer()?.to_usize().ok_or("count")?;
    if n < minimum || n > maximum {
        Err("count")
    } else {
        Ok(n)
    }
}
pub fn same(a: &J, b: &J) -> bool {
    match (a, b) {
        (J::Num(_), J::Num(_)) => {
            a.integer().is_ok() == b.integer().is_ok() && compare(a, b) == Ok(Ordering::Equal)
        }
        (J::Arr(x), J::Arr(y)) => x.len() == y.len() && x.iter().zip(y).all(|(a, b)| same(a, b)),
        (J::Obj(x), J::Obj(y)) => {
            x.len() == y.len() && x.iter().all(|(k, a)| y.get(k).is_some_and(|b| same(a, b)))
        }
        _ => a == b,
    }
}
pub fn whitespace(c: u32) -> bool {
    matches!(c,9..=13|28..=32|0x85|0xa0|0x1680|0x2000..=0x200a|0x2028|0x2029|0x202f|0x205f|0x3000)
}
