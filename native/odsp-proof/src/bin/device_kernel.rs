//! Local differential test driver; no principal or execution grant.
use std::io::{Read, Write};
use execution_commitment::commit;
use execution_model::{INPUT_TAG, OUTPUT_TAG};
fn main() {
    let mut input = Vec::new();
    std::io::stdin().take(70).read_to_end(&mut input).unwrap();
    let mut output = match odsp_device_kernel_qualification::evaluate(&input) {
        Ok(value) => {
            let mut result = b"OK".to_vec();
            result.extend(&value);
            // Compile exactly the commitment crates used by the real SP1 guest.
            result.extend(commit(INPUT_TAG, &[&input]).as_bytes());
            result.extend(commit(OUTPUT_TAG, &[&value]).as_bytes());
            result
        }
        Err(code) => { let mut result = b"ERR".to_vec(); result.extend(code.to_le_bytes()); result }
    };
    std::io::stdout().write_all(&output).unwrap();
    output.clear();
}
