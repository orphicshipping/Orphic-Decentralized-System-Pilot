//! Checked exact affine kernel; no device IO, authentication or physical claim.
extern crate alloc;
use alloc::vec::Vec;
pub fn evaluate(raw: &[u8]) -> Result<Vec<u8>, u32> {
    if raw.len()!=68 || &raw[..4]!=b"DBI1" { return Err(2); }
    let count=i64::from_le_bytes(raw[36..44].try_into().map_err(|_|2u32)?);
    let gain=i64::from_le_bytes(raw[44..52].try_into().map_err(|_|2u32)?);
    let offset=i64::from_le_bytes(raw[52..60].try_into().map_err(|_|2u32)?);
    let den=u64::from_le_bytes(raw[60..68].try_into().map_err(|_|2u32)?);
    if !(-(1i64<<40)..=(1i64<<40)).contains(&count) || !(-(1i64<<20)..=(1i64<<20)).contains(&gain)
        || !(-(1i64<<60)..=(1i64<<60)).contains(&offset) || !(1..=(1u64<<20)).contains(&den) { return Err(3); }
    let y=(count as i128).checked_mul(gain as i128).and_then(|v|v.checked_add(offset as i128)).ok_or(4u32)?;
    let mut out=b"DBO1".to_vec();out.extend_from_slice(&raw[4..36]);
    out.extend_from_slice(&y.to_le_bytes());out.extend_from_slice(&den.to_le_bytes());Ok(out)
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn vector() {
        let mut raw=b"DBI1".to_vec();raw.extend_from_slice(&[7;32]);
        for n in [24982i64,1000,0,1] {raw.extend_from_slice(&n.to_le_bytes());}
        let out=evaluate(&raw).unwrap();assert_eq!(out.len(),60);
        assert_eq!(i128::from_le_bytes(out[36..52].try_into().unwrap()),24982000);
        raw.push(0);assert_eq!(evaluate(&raw),Err(2));
    }
}
