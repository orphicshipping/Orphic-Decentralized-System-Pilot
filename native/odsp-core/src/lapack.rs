//! Narrow, bounded FFI to a host-pinned scipy OpenBLAS ILP64 library.
//! No library search, Python invocation, alternate solver or fallback.
// SPDX-License-Identifier: AGPL-3.0-or-later
use std::{ffi::{c_char, CStr}, path::Path};
use libloading::Library;
use nalgebra::DMatrix;
use crate::Result;

type InfoString = unsafe extern "C" fn() -> *const c_char;
type NumThreads = unsafe extern "C" fn() -> i32;
type Gesdd = unsafe extern "C" fn(i32, c_char, i64, i64, *mut f64, i64,
    *mut f64, *mut f64, i64, *mut f64, i64) -> i64;

type Potrf = unsafe extern "C" fn(i32, c_char, i64, *mut f64, i64) -> i64;
type Gesv = unsafe extern "C" fn(i32, i64, i64, *mut f64, i64, *mut i64, *mut f64, i64) -> i64;
type Gemm = unsafe extern "C" fn(i32,i32,i32,i64,i64,i64,f64,*const f64,i64,*const f64,i64,f64,*mut f64,i64);
type Gemv = unsafe extern "C" fn(i32,i32,i64,i64,f64,*const f64,i64,*const f64,i64,f64,*mut f64,i64);
type Dot = unsafe extern "C" fn(i64,*const f64,i64,*const f64,i64) -> f64;
type Syrk = unsafe extern "C" fn(i32,i32,i32,i64,i64,f64,*const f64,i64,f64,*mut f64,i64);
type FloatStatus = unsafe extern "C" fn(i32) -> i32;

pub struct Lapack {
    // Keep the library loaded for the entire lifetime of its function pointer.
    _library: Library,
    gesdd: Gesdd,
    potrf: Potrf,
    gesv: Gesv,
    gemm: Gemm,
    gemv: Gemv,
    dot: Dot,
    syrk: Syrk,
    clear_status: FloatStatus,
    read_status: FloatStatus,
    configuration: serde_json::Value,
}
impl Lapack {
    pub fn load(path: &Path) -> Result<Self> {
        // The fenv mask and ILP64 ABI below are qualified only on this target.
        if !cfg!(all(target_os="linux",target_arch="x86_64")) {return Err("unqualified_native_target");}
        if !path.is_absolute() { return Err("absolute_lapack_path_required"); }
        // SAFETY: The trusted host pins and snapshots this executable library and
        // all bundled dependencies before launch. This is the documented ILP64
        // LAPACKE ABI (i32 layout, i64 dimensions/info, binary64 real buffers).
        // A wrongly selected library is a host-binding error, never model input.
        let library=unsafe { Library::new(path) }.map_err(|_|"pinned_lapack_load")?;
        let gesdd=unsafe { *library.get::<Gesdd>(b"scipy_LAPACKE_dgesdd64_\0")
            .map_err(|_|"pinned_lapack_abi")? };
        // SAFETY: Same selected ABI and library lifetime as DGESDD above.
        let potrf=unsafe { *library.get::<Potrf>(b"scipy_LAPACKE_dpotrf64_\0").map_err(|_|"pinned_lapack_abi")? };
        let gesv=unsafe { *library.get::<Gesv>(b"scipy_LAPACKE_dgesv64_\0").map_err(|_|"pinned_lapack_abi")? };
        let gemm=unsafe { *library.get::<Gemm>(b"scipy_cblas_dgemm64_\0").map_err(|_|"pinned_lapack_abi")? };
        let gemv=unsafe { *library.get::<Gemv>(b"scipy_cblas_dgemv64_\0").map_err(|_|"pinned_lapack_abi")? };
        let dot=unsafe { *library.get::<Dot>(b"scipy_cblas_ddot64_\0").map_err(|_|"pinned_lapack_abi")? };
        let syrk=unsafe { *library.get::<Syrk>(b"scipy_cblas_dsyrk64_\0").map_err(|_|"pinned_lapack_abi")? };
        // SAFETY: These C99 functions are resolved from the selected library's
        // libm dependency. They access this thread's floating-point status only.
        let clear_status=unsafe { *library.get::<FloatStatus>(b"feclearexcept\0").map_err(|_|"native_fenv_abi")? };
        let read_status=unsafe { *library.get::<FloatStatus>(b"fetestexcept\0").map_err(|_|"native_fenv_abi")? };
        // SAFETY: OpenBLAS returns static NUL-terminated strings and a C int.
        let (config,core,threads)=unsafe {
            let config=library.get::<InfoString>(b"scipy_openblas_get_config64_\0").map_err(|_|"pinned_lapack_abi")?;
            let core=library.get::<InfoString>(b"scipy_openblas_get_corename64_\0").map_err(|_|"pinned_lapack_abi")?;
            let threads=library.get::<NumThreads>(b"scipy_openblas_get_num_threads64_\0").map_err(|_|"pinned_lapack_abi")?;
            let (config,core)=(config(),core());
            if config.is_null() || core.is_null() {return Err("pinned_lapack_abi");}
            (CStr::from_ptr(config).to_str().map_err(|_|"pinned_lapack_abi")?.to_owned(),
             CStr::from_ptr(core).to_str().map_err(|_|"pinned_lapack_abi")?.to_owned(),threads())
        };
        if !config.contains("USE64BITINT") || config.len()>1024 || core.is_empty() || core.len()>128 || threads!=1 {
            return Err("pinned_lapack_configuration");
        }
        let configuration=serde_json::json!({"config":config,"core":core,"threads":threads});
        Ok(Self { _library:library, gesdd, potrf, gesv, gemm, gemv, dot, syrk,
            clear_status, read_status, configuration })
    }
    pub fn configuration(&self) -> serde_json::Value { self.configuration.clone() }
    fn clear_arithmetic_status(&self) -> Result<()> {
        // SAFETY: x86_64 Linux FE_ALL_EXCEPT is 0x3d. No traps are enabled.
        if unsafe {(self.clear_status)(0x3d)} != 0 {return Err("native_fenv_clear");}
        Ok(())
    }
    fn check_arithmetic_status(&self) -> Result<()> {
        // SAFETY: Inspect invalid/divide/overflow/underflow; ignore inexact.
        // LAPACK calls intentionally do not use this check, matching NumPy's
        // linalg wrappers. NumPy matmul does propagate these arithmetic faults.
        if unsafe {(self.read_status)(0x1d)} != 0 {return Err("blas_arithmetic_domain");}
        Ok(())
    }
    pub fn dot(&self, a: &[f64], b: &[f64]) -> Result<f64> {
        if a.is_empty() || a.len()>1024 || a.len()!=b.len()
            || a.iter().chain(b.iter()).any(|v|!v.is_finite()) {return Err("lapack_input_bounds");}
        self.clear_arithmetic_status()?;
        // SAFETY: Equal finite contiguous slices of at most 1024 doubles.
        let value=unsafe {(self.dot)(a.len() as i64,a.as_ptr(),1,b.as_ptr(),1)};
        self.check_arithmetic_status()?;
        if !value.is_finite() {return Err("lapack_nonfinite");}
        Ok(value)
    }
    pub fn gram(&self, a: &DMatrix<f64>) -> Result<DMatrix<f64>> {
        let (n,k)=(a.nrows(),a.ncols());
        if n==0 || n>256 || k==0 || k>256 || a.iter().any(|v|!v.is_finite()) {return Err("lapack_input_bounds");}
        let ar:Vec<_>=(0..n).flat_map(|r|(0..k).map(move|c|a[(r,c)])).collect();
        let mut out=vec![0.;n*n];
        self.clear_arithmetic_status()?;
        // SAFETY: Owned row-major n*k input and n*n output; upper triangle is
        // populated by DSYRK and mirrored just as NumPy's A @ A.T branch does.
        unsafe {(self.syrk)(101,121,111,n as i64,k as i64,1.,ar.as_ptr(),k as i64,0.,out.as_mut_ptr(),n as i64)};
        self.check_arithmetic_status()?;
        for r in 0..n {for c in r+1..n {out[c*n+r]=out[r*n+c];}}
        if out.iter().any(|v|!v.is_finite()) {return Err("lapack_nonfinite");}
        Ok(DMatrix::from_row_slice(n,n,&out))
    }
    pub fn svd(&self, mut a: DMatrix<f64>, vectors: bool) -> Result<(Vec<f64>,DMatrix<f64>,DMatrix<f64>)> {
        let (m,n)=(a.nrows(),a.ncols());
        if m==0 || m>1024 || n==0 || n>32 || a.iter().any(|v|!v.is_finite()) {
            return Err("lapack_input_bounds");
        }
        let k=m.min(n);
        let mut s=vec![0.;k];
        let mut u=DMatrix::zeros(if vectors {m} else {1},if vectors {k} else {1});
        let mut vt=DMatrix::zeros(if vectors {k} else {1},if vectors {n} else {1});
        // SAFETY: Owned column-major buffers have exact LAPACKE sizes, disjoint
        // mutable storage, nonzero leading dimensions and no pointer escapes.
        // k<=32; native process wall/address-space/CPU limits bound the routine.
        let info=unsafe {(self.gesdd)(102,if vectors {b'S'} else {b'N'} as c_char,
            m as i64,n as i64,a.as_mut_ptr(),m as i64,s.as_mut_ptr(),u.as_mut_ptr(),
            if vectors {m as i64} else {1},vt.as_mut_ptr(),if vectors {k as i64} else {1})};
        if info != 0 {return Err("lapack_svd_failure");}
        if s.iter().chain(u.iter()).chain(vt.iter()).any(|v|!v.is_finite()) {return Err("lapack_nonfinite");}
        Ok((s,u,vt))
    }
    pub fn cholesky(&self, mut a: DMatrix<f64>) -> Result<DMatrix<f64>> {
        let n=a.nrows();
        if n==0 || n>256 || a.ncols()!=n || a.iter().any(|v|!v.is_finite()) {return Err("lapack_input_bounds");}
        // SAFETY: Disjoint owned column-major n*n buffer, lda=n, n<=256.
        let info=unsafe {(self.potrf)(102,b'L' as c_char,n as i64,a.as_mut_ptr(),n as i64)};
        if info>0 {return Err("positive_definite_covariance_required");}
        if info<0 {return Err("lapack_cholesky_argument");}
        for r in 0..n {for c in r+1..n {a[(r,c)]=0.;}}
        if a.iter().any(|v|!v.is_finite()) {return Err("lapack_nonfinite");}
        Ok(a)
    }
    pub fn solve(&self, mut a: DMatrix<f64>, mut b: DMatrix<f64>) -> Result<DMatrix<f64>> {
        let (n,nrhs)=(a.nrows(),b.ncols());
        if n==0 || n>256 || a.ncols()!=n || b.nrows()!=n || nrhs==0 || nrhs>32
            || a.iter().chain(b.iter()).any(|v|!v.is_finite()) {return Err("lapack_input_bounds");}
        let mut pivots=vec![0i64;n];
        // SAFETY: DGESV owns n*n A, n*nrhs B, n pivot slots; lda=ldb=n.
        let info=unsafe {(self.gesv)(102,n as i64,nrhs as i64,a.as_mut_ptr(),n as i64,
            pivots.as_mut_ptr(),b.as_mut_ptr(),n as i64)};
        if info!=0 {return Err("singular_solve");}
        if b.iter().any(|v|!v.is_finite()) {return Err("lapack_nonfinite");}
        Ok(b)
    }
    pub fn matmul(&self, a: &DMatrix<f64>, b: &DMatrix<f64>) -> Result<DMatrix<f64>> {
        let (m,k,n)=(a.nrows(),a.ncols(),b.ncols());
        if m==0 || m>1024 || k==0 || k>256 || n==0 || n>256 || b.nrows()!=k
            || a.iter().chain(b.iter()).any(|v|!v.is_finite()) {return Err("lapack_input_bounds");}
        let ar:Vec<_>=(0..m).flat_map(|r|(0..k).map(move|c|a[(r,c)])).collect();
        let br:Vec<_>=(0..k).flat_map(|r|(0..n).map(move|c|b[(r,c)])).collect();
        if m==1 && n==1 {return Ok(DMatrix::from_element(1,1,self.dot(&ar,&br)?));}
        let mut out=vec![0.;m*n];
        self.clear_arithmetic_status()?;
        // SAFETY: Owned row-major buffers match all nonzero leading dimensions.
        // Choose NumPy's vector/matrix BLAS branches rather than a new reduction.
        unsafe {
            if m==1 && n>1 {
                (self.gemv)(101,112,k as i64,n as i64,1.,br.as_ptr(),n as i64,
                    ar.as_ptr(),1,0.,out.as_mut_ptr(),1);
            } else if n==1 && m>1 {
                (self.gemv)(101,111,m as i64,k as i64,1.,ar.as_ptr(),k as i64,
                    br.as_ptr(),1,0.,out.as_mut_ptr(),1);
            } else {
                (self.gemm)(101,111,111,m as i64,n as i64,k as i64,1.,ar.as_ptr(),k as i64,
                    br.as_ptr(),n as i64,0.,out.as_mut_ptr(),n as i64);
            }
        }
        self.check_arithmetic_status()?;
        if out.iter().any(|v|!v.is_finite()) {return Err("lapack_nonfinite");}
        Ok(DMatrix::from_row_slice(m,n,&out))
    }

}
