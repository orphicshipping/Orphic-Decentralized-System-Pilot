//! Native binary64 implementation of ODSP's seven declared numerical operations.
//! No policy, physical measurement, proof or execution-admission authority lives here.
// SPDX-License-Identifier: AGPL-3.0-or-later
#![deny(unsafe_code)]
#[allow(unsafe_code)]
pub mod lapack;

use nalgebra::DMatrix;
use serde::Deserialize;
use serde_json::{Value, json};

pub const MAX_INPUT: usize = 512 * 1024;
pub const MAX_OUTPUT: usize = 2 * 1024 * 1024;
pub type Result<T> = std::result::Result<T, &'static str>;
type Matrix = Vec<Vec<f64>>;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Envelope { schema: String, request: Request }

#[derive(Deserialize)]
#[serde(tag = "operation", content = "input", deny_unknown_fields)]
enum Request {
    #[serde(rename = "uncertainty.propagate.v1")] Propagate(Uncertainty),
    #[serde(rename = "uncertainty.budget.v1")] Budget(Uncertainty),
    #[serde(rename = "sensitivity.local.v1")] Sensitivity(Sensitivity),
    #[serde(rename = "calibration.fit.v1")] Fit(Fit),
    #[serde(rename = "calibration.residuals.v1")] Residuals(Residuals),
    #[serde(rename = "observability.rank.v1")] Observability(Observability),
    #[serde(rename = "model.discrepancy.v1")] Discrepancy(Discrepancy),
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Uncertainty { jacobian: Matrix, covariance: Matrix }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Sensitivity { jacobian: Matrix, input_scales: Vec<f64>, output_scales: Vec<f64> }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fit { x: Vec<f64>, y: Vec<f64>, covariance: Matrix, rcond: f64 }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Residuals { x: Vec<f64>, y: Vec<f64>, coefficients: [f64; 2], coefficient_covariance: Matrix,
                   observation_covariance: Matrix }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Observability { transition: Matrix, observation: Matrix, state_scales: Vec<f64>,
                       output_scales: Vec<f64>, horizon: usize, rcond: f64 }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Discrepancy { observed: Vec<f64>, predicted: Vec<f64>, joint_covariance: Matrix }

fn finite(x: f64) -> Result<f64> { if x.is_finite() { Ok(x) } else { Err("nonfinite_computation") } }
fn vector(v: &[f64], n: usize) -> Result<()> {
    if v.len() != n || v.iter().any(|x| !x.is_finite()) { return Err("vector_shape_or_domain"); }
    Ok(())
}
fn matrix(v: &Matrix, r: usize, c: usize) -> Result<()> {
    if v.len() != r || r == 0 || c == 0 || v.iter().any(|row| row.len() != c) { return Err("matrix_shape"); }
    for row in v { vector(row, c)?; } Ok(())
}
fn dimension(n: usize, max: usize) -> Result<()> {
    if n == 0 || n > max { Err("dimension_budget") } else { Ok(()) }
}
fn positive(v: &[f64]) -> Result<()> {
    if v.iter().any(|x| !x.is_finite() || *x <= 0.) { Err("positive_scale_required") } else { Ok(()) }
}
fn rcond(x: f64) -> Result<()> {
    if !x.is_finite() || !(1e-15..=1e-2).contains(&x) { Err("rcond_range") } else { Ok(()) }
}

// Exact dyadic representations make inexact subnormal multiplication/division
// fail as NumPy's explicit elementwise underflow trap does. No covariance repair.
fn dyad(x: f64) -> (u128, i32) {
    let b = x.abs().to_bits(); let e = ((b >> 52) & 2047) as i32;
    let m = b & ((1u64 << 52)-1);
    if e == 0 { (m as u128, -1074) } else { ((m | (1u64 << 52)) as u128, e-1075) }
}
fn same_dyad(mut a: u128, mut ae: i32, mut b: u128, mut be: i32) -> bool {
    if a == 0 || b == 0 { return a == b; }
    let az = a.trailing_zeros(); let bz = b.trailing_zeros();
    a >>= az; b >>= bz; ae += az as i32; be += bz as i32;
    a == b && ae == be
}
fn mul(a: f64, b: f64) -> Result<f64> {
    let value = finite(a*b)?;
    if a != 0. && b != 0. && value.abs() < f64::MIN_POSITIVE {
        let (am,ae)=dyad(a); let (bm,be)=dyad(b); let (vm,ve)=dyad(value);
        if !same_dyad(am*bm,ae+be,vm,ve) { return Err("binary64_underflow"); }
    }
    Ok(value)
}
fn div(a: f64, b: f64) -> Result<f64> {
    let value = finite(a/b)?;
    if a != 0. && value.abs() < f64::MIN_POSITIVE {
        let (am,ae)=dyad(a); let (bm,be)=dyad(b); let (vm,ve)=dyad(value);
        if !same_dyad(vm*bm,ve+be,am,ae) { return Err("binary64_underflow"); }
    }
    Ok(value)
}

// Error-free expansion summation with a final ties-to-even correction. This
// preserves signed covariance cross terms instead of changing reduction order.
pub fn accurate_sum(values: impl IntoIterator<Item=f64>) -> Result<f64> {
    let mut partials: Vec<f64> = Vec::new();
    for mut x in values {
        finite(x)?; let mut used = 0;
        for i in 0..partials.len() {
            let mut y = partials[i];
            if x.abs() < y.abs() { std::mem::swap(&mut x, &mut y); }
            let high = finite(x+y)?; let low = y-(high-x);
            if low != 0. { partials[used] = low; used += 1; }
            x = high;
        }
        partials.truncate(used);
        if x != 0. { partials.push(x); }
    }
    let mut high = partials.pop().unwrap_or(0.);
    while let Some(y) = partials.pop() {
        let x = high; high = finite(x+y)?; let low = y-(high-x);
        if low != 0. {
            if partials.last().is_some_and(|z| (low < 0. && *z < 0.) || (low > 0. && *z > 0.)) {
                let correction = low*2.; let adjusted = high+correction;
                if correction == adjusted-high { high = finite(adjusted)?; }
            }
            break;
        }
    }
    Ok(high)
}
pub fn product(values: &[f64]) -> Result<f64> {
    if values.contains(&0.) { return Ok(0.); }
    let (mut mantissa, mut exponent) = (1., 0);
    for &value in values { finite(value)?; let (m,e)=libm::frexp(value); mantissa *= m; exponent += e; }
    let result = finite(libm::scalbn(mantissa, exponent))?;
    if result == 0. { Err("nonzero_contribution_underflow") } else { Ok(result) }
}
fn norm(v: &[f64]) -> Result<f64> {
    let scale = v.iter().map(|v| v.abs()).fold(0., f64::max);
    if scale == 0. { return Ok(0.); }
    let sum = accurate_sum(v.iter().map(|v| { let r=v/scale; r*r }))?;
    finite(scale*sum.sqrt())
}
fn propagation(j: &Matrix, c: &Matrix) -> Result<Matrix> {
    let mut out = vec![vec![0.;j.len()];j.len()];
    for a in 0..j.len() { for b in 0..j.len() {
        let mut terms=Vec::with_capacity(c.len()*c.len());
        for i in 0..c.len() { for k in 0..c.len() { terms.push(product(&[j[a][i],c[i][k],j[b][k]])?); } }
        out[a][b]=accurate_sum(terms)?;
    }} Ok(out)
}
fn standard(c: &Matrix) -> Result<Vec<f64>> {
    (0..c.len()).map(|i| if c[i][i] < 0. { Err("negative_variance") } else { finite(c[i][i].sqrt()) }).collect()
}
fn rows(m: &DMatrix<f64>) -> Result<Matrix> {
    (0..m.nrows()).map(|r| (0..m.ncols()).map(|c| finite(m[(r,c)])).collect()).collect()
}
fn cholesky(c: &Matrix, lapack: &lapack::Lapack) -> Result<(Vec<f64>, DMatrix<f64>)> {
    let n=c.len(); let scale=standard(c)?; positive(&scale)?;
    let mut corr=DMatrix::zeros(n,n);
    for i in 0..n { for k in 0..n {
        if c[i][k] != c[k][i] { return Err("exact_symmetric_covariance_required"); }
        corr[(i,k)]=div(div(c[i][k],scale[i])?,scale[k])?;
    }}
    let chol=lapack.cholesky(corr)?;
    rows(&chol)?; Ok((scale,chol))
}
fn solve_lower(chol: &DMatrix<f64>, rhs: &DMatrix<f64>, lapack: &lapack::Lapack) -> Result<DMatrix<f64>> {
    let solved=lapack.solve(chol.clone(),rhs.clone())?;
    rows(&solved)?; Ok(solved)
}
fn summary(predicted: Vec<f64>, residuals: Vec<f64>, covariance: Matrix, lapack: &lapack::Lapack) -> Result<Value> {
    let std=standard(&covariance)?;
    let mut chi=None;
    let decomposition=cholesky(&covariance,lapack);
    match decomposition.as_ref() {
        Err(&"positive_definite_covariance_required") | Err(&"positive_scale_required") |
        Err(&"exact_symmetric_covariance_required") => {},
        Err(reason) => return Err(reason),
        Ok(_) => {},
    }
    if let Ok((scales,chol))=decomposition {
        let r: Vec<f64>=residuals.iter().zip(scales).map(|(v,s)| div(*v,s)).collect::<Result<_>>()?;
        let w=solve_lower(&chol,&DMatrix::from_column_slice(r.len(),1,&r),lapack)?;
        chi=Some(lapack.dot(w.as_slice(),w.as_slice())?);
    }
    let z: Vec<Option<f64>>=residuals.iter().zip(&std).map(|(v,s)| if *s==0. {Ok(None)} else {finite(*v / *s).map(Some)}).collect::<Result<_>>()?;
    let bias=finite(accurate_sum(residuals.iter().copied())?/residuals.len() as f64)?;
    let rmse=finite(norm(&residuals)?/(residuals.len() as f64).sqrt())?;
    Ok(json!({"predicted":predicted,"residuals":residuals,"covariance":covariance,
        "standard_uncertainty":std,"standardized_residuals":z,"bias":bias,"rmse":rmse,
        "chi_square":chi,"chi_square_status": if chi.is_some() {"computed_known_covariance_no_acceptance_threshold"} else {"singular_or_nonpositive_covariance"}}))
}

fn calculate(request: Request, lapack: Option<&lapack::Lapack>) -> Result<Value> {
    match request {
        Request::Propagate(v) => uncertainty(v, false),
        Request::Budget(v) => uncertainty(v, true),
        Request::Sensitivity(v) => {
            let n=v.input_scales.len(); let m=v.output_scales.len();
            dimension(n,32)?; dimension(m,32)?; matrix(&v.jacobian,m,n)?;
            positive(&v.input_scales)?; positive(&v.output_scales)?;
            let scaled: Matrix=(0..m).map(|r| (0..n).map(|c| div(mul(v.jacobian[r][c],v.input_scales[c])?,v.output_scales[r])).collect()).collect::<Result<_>>()?;
            let norms=scaled.iter().map(|r| norm(r)).collect::<Result<Vec<_>>>()?;
            Ok(json!({"jacobian":v.jacobian,"scaled_jacobian":scaled,"row_norms":norms,"method":"supplied_jacobian"}))
        },
        Request::Fit(v) => fit(v, lapack.ok_or("pinned_lapack_required")?),
        Request::Residuals(v) => {
            let n=v.x.len(); dimension(n,256)?; vector(&v.x,n)?; vector(&v.y,n)?; vector(&v.coefficients,2)?;
            matrix(&v.coefficient_covariance,2,2)?; matrix(&v.observation_covariance,n,n)?;
            let design: Matrix=v.x.iter().map(|x| vec![1.,*x]).collect();
            let h=DMatrix::from_fn(n,2,|r,c|design[r][c]);
            let predicted=lapack.ok_or("pinned_lapack_required")?.matmul(&h,
                &DMatrix::from_column_slice(2,1,&v.coefficients))?.column(0).as_slice().to_vec();
            let residuals: Vec<f64>=v.y.iter().zip(&predicted).map(|(y,p)| finite(y-p)).collect::<Result<_>>()?;
            let mut cov=propagation(&design,&v.coefficient_covariance)?;
            for i in 0..n {for k in 0..n {cov[i][k]=finite(cov[i][k]+v.observation_covariance[i][k])?;}}
            summary(predicted,residuals,cov,lapack.ok_or("pinned_lapack_required")?)
        },
        Request::Observability(v) => observability(v, lapack.ok_or("pinned_lapack_required")?),
        Request::Discrepancy(v) => {
            let n=v.observed.len(); dimension(n,128)?; vector(&v.observed,n)?; vector(&v.predicted,n)?; matrix(&v.joint_covariance,2*n,2*n)?;
            let mut c=vec![vec![0.;n];n];
            for i in 0..n {for k in 0..n {
                let a=finite(v.joint_covariance[i][k]+v.joint_covariance[i+n][k+n])?;
                let b=finite(a-v.joint_covariance[i][k+n])?;
                c[i][k]=finite(b-v.joint_covariance[i+n][k])?;
            }}
            let residuals=v.observed.iter().zip(&v.predicted).map(|(o,p)|finite(o-p)).collect::<Result<Vec<_>>>()?;
            summary(v.predicted,residuals,c,lapack.ok_or("pinned_lapack_required")?)
        }
    }
}

fn uncertainty(v: Uncertainty, budget: bool) -> Result<Value> {
    let m=v.jacobian.len(); let n=v.covariance.len(); dimension(m,32)?; dimension(n,32)?;
    matrix(&v.jacobian,m,n)?; matrix(&v.covariance,n,n)?;
    let cov=propagation(&v.jacobian,&v.covariance)?; let std=standard(&cov)?;
    if !budget {return Ok(json!({"covariance":cov,"standard_uncertainty":std}));}
    let diagonal: Matrix=v.jacobian.iter().map(|r| (0..n).map(|i| product(&[r[i],r[i],v.covariance[i][i]])).collect()).collect::<Result<_>>()?;
    let mut cross=vec![];
    for i in 0..n {for k in i+1..n {
        let terms=v.jacobian.iter().map(|r| accurate_sum([
            product(&[r[i],r[k],v.covariance[i][k]])?,product(&[r[i],r[k],v.covariance[k][i]])?])).collect::<Result<Vec<_>>>()?;
        cross.push(json!({"pair":[i,k],"contributions":terms}));
    }}
    Ok(json!({"covariance":cov,"standard_uncertainty":std,"diagonal_terms":diagonal,"cross_terms":cross}))
}
fn fit(v: Fit, lapack: &lapack::Lapack) -> Result<Value> {
    let n=v.x.len(); dimension(n,256)?;
    if n<3 {return Err("three_samples_required");} vector(&v.x,n)?; vector(&v.y,n)?; matrix(&v.covariance,n,n)?; rcond(v.rcond)?;
    let (scale,chol)=cholesky(&v.covariance,lapack)?;
    let center=accurate_sum(v.x.iter().map(|x| div(*x,n as f64)).collect::<Result<Vec<_>>>()?)?;
    let deviations=v.x.iter().map(|x|finite(x-center)).collect::<Result<Vec<_>>>()?;
    let xscale=deviations.iter().map(|d|d.abs()).fold(0.,f64::max);
    if xscale==0. {return Err("constant_x");}
    let mut design=DMatrix::zeros(n,2); let mut normalized=DMatrix::zeros(n,2); let mut target=DMatrix::zeros(n,1);
    for i in 0..n {
        design[(i,0)]=1.; design[(i,1)]=div(deviations[i],xscale)?;
        normalized[(i,0)]=div(1.,scale[i])?; normalized[(i,1)]=div(design[(i,1)],scale[i])?;
        target[(i,0)]=div(v.y[i],scale[i])?;
    }
    let whitened=solve_lower(&chol,&normalized,lapack)?; let target=solve_lower(&chol,&target,lapack)?;
    let (singular,u,vt)=lapack.svd(whitened, true)?;
    vector(singular.as_slice(),2)?;
    if singular[1] <= mul(v.rcond,singular[0])? {return Err("rank_deficient_at_rcond");}
    let projected=lapack.matmul(&u.transpose(),&target)?;
    let mut normalized_coefficients=projected;
    for i in 0..2 {normalized_coefficients[(i,0)]=div(normalized_coefficients[(i,0)],singular[i])?;}
    let coefficients=lapack.matmul(&vt.transpose(),&normalized_coefficients)?;
    rows(&coefficients)?;
    let transform=DMatrix::from_row_slice(2,2,&[1.,div(-center,xscale)?,0.,div(1.,xscale)?]);
    let physical=lapack.matmul(&transform,&coefficients)?; rows(&physical)?;
    let mut inverse=vt.transpose();
    for r in 0..2 {for c in 0..2 {inverse[(r,c)]=div(inverse[(r,c)],singular[c])?;}}
    let inverse=lapack.matmul(&transform,&inverse)?; rows(&inverse)?;
    let coeff_cov=rows(&lapack.gram(&inverse)?)?;
    let predicted=lapack.matmul(&design,&coefficients)?; rows(&predicted)?;
    let residual=v.y.iter().enumerate().map(|(i,y)|finite(*y-predicted[(i,0)])).collect::<Result<Vec<_>>>()?;
    let residual_scaled=residual.iter().zip(scale).map(|(r,s)|div(*r,s)).collect::<Result<Vec<_>>>()?;
    let wr=solve_lower(&chol,&DMatrix::from_column_slice(n,1,&residual_scaled),lapack)?;
    let chi=lapack.dot(wr.as_slice(),wr.as_slice())?; let condition=finite(singular[0]/singular[1])?;
    Ok(json!({"coefficients":physical.column(0).as_slice(),"coefficient_covariance":coeff_cov,
        "predicted":predicted.column(0).as_slice(),"residuals":residual,"degrees_of_freedom":n-2,
        "chi_square":chi,"condition_number":condition,"covariance_scale":"known_absolute_not_residual_rescaled"}))
}
fn observability(v: Observability, lapack: &lapack::Lapack) -> Result<Value> {
    let n=v.state_scales.len(); let m=v.output_scales.len(); dimension(n,32)?; dimension(m,32)?;
    if !(1..=32).contains(&v.horizon) {return Err("horizon_budget");}
    matrix(&v.transition,n,n)?; matrix(&v.observation,m,n)?; positive(&v.state_scales)?; positive(&v.output_scales)?; rcond(v.rcond)?;
    let mut a=DMatrix::zeros(n,n); let mut block=DMatrix::zeros(m,n);
    for r in 0..n {for c in 0..n {a[(r,c)]=div(mul(v.transition[r][c],v.state_scales[c])?,v.state_scales[r])?;}}
    for r in 0..m {for c in 0..n {block[(r,c)]=div(mul(v.observation[r][c],v.state_scales[c])?,v.output_scales[r])?;}}
    let mut o=DMatrix::zeros(m*v.horizon,n);
    for h in 0..v.horizon {
        for r in 0..m {for c in 0..n {o[(h*m+r,c)]=block[(r,c)];}}
        if h+1<v.horizon {block=lapack.matmul(&block,&a)?; rows(&block)?;}
    }
    let out=rows(&o)?;
    let (s,_,_)=lapack.svd(o, false)?; vector(s.as_slice(),n.min(m*v.horizon))?;
    let threshold=mul(v.rcond,s[0])?; let rank=s.iter().filter(|s|**s>threshold).count();
    let condition=if rank==n {Some(finite(s[0]/s[s.len()-1])?)} else {None};
    Ok(json!({"observability_matrix":out,"singular_values":s.as_slice(),"rank":rank,"threshold":threshold,
        "full_rank_over_horizon":rank==n,"condition_number":condition,"method":"scaled_discrete_finite_horizon_svd"}))
}

pub fn run(raw: &[u8]) -> Result<Value> { run_with_lapack(raw, None) }

pub fn run_with_lapack(raw: &[u8], lapack: Option<&lapack::Lapack>) -> Result<Value> {
    if raw.is_empty() || raw.len()>MAX_INPUT {return Err("input_budget");}
    let envelope: Envelope=serde_json::from_slice(raw).map_err(|_|"strict_request_schema")?;
    if envelope.schema!="odsp.rust-numerical.v1" {return Err("schema_version");}
    calculate(envelope.request, lapack)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn sum_keeps_cancelled_low_term() {assert_eq!(accurate_sum([1e100,1.,-1e100]).unwrap(),1.);}
    #[test] fn product_keeps_exponent_range() {
        assert!((product(&[1e200,1e-300,1e200]).unwrap()/1e100-1.).abs()<1e-14);
        assert!(product(&[1e-200,1.,1e-200]).is_err());
    }
    #[test] fn subnormal_trap_distinguishes_exact_result() {
        let tiny=f64::from_bits(2);assert_eq!(mul(tiny,0.5).unwrap().to_bits(),1);
        assert!(mul(f64::from_bits(1),0.5).is_err());
    }
    #[test] fn duplicate_and_unknown_fields_refuse() {
        for raw in [br#"{"schema":"odsp.rust-numerical.v1","schema":"x","request":{}}"#.as_slice(),
                    br#"{"schema":"odsp.rust-numerical.v1","request":{"operation":"sensitivity.local.v1","input":{"jacobian":[[1]],"input_scales":[1],"output_scales":[1],"exec":true}}}"#] {
            assert!(run(raw).is_err());
        }
    }
}
