//! New workload, same existing ste.sp1.kernel-io.v1 public-values convention.
#![no_main]
use execution_commitment::commit;
use execution_model::{INPUT_TAG,OUTPUT_TAG};
mod kernel;
sp1_zkvm::entrypoint!(main);
pub fn main() {
    let input=sp1_zkvm::io::read_vec();
    sp1_zkvm::io::commit_slice(b"ste.sp1.kernel-io.v1");
    sp1_zkvm::io::commit_slice(commit(INPUT_TAG,&[&input]).as_bytes());
    match kernel::evaluate(&input) {
        Ok(output)=> {
            sp1_zkvm::io::commit_slice(&[0]);
            sp1_zkvm::io::commit_slice(commit(OUTPUT_TAG,&[&output]).as_bytes());
            sp1_zkvm::io::commit_slice(&0u32.to_le_bytes());
        },
        Err(code)=> {sp1_zkvm::io::commit_slice(&[1]);sp1_zkvm::io::commit_slice(&code.to_le_bytes());}
    }
}
