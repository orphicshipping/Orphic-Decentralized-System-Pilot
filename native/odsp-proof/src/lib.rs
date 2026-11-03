//! Qualification of the existing device guest; never physical or principal authority.
//! SPDX-License-Identifier: AGPL-3.0-or-later
#![forbid(unsafe_code)]

#[path = "../../../instruments/execution/compute-runtime/zk/guest-device-boundary/src/kernel.rs"]
mod guest_kernel;

pub use guest_kernel::evaluate;

fn statement(context: [u8; 32], count: i64, gain: i64, offset: i64, denominator: u64) -> [u8; 68] {
    let mut raw = [0; 68];
    raw[..4].copy_from_slice(b"DBI1");
    raw[4..36].copy_from_slice(&context);
    raw[36..44].copy_from_slice(&count.to_le_bytes());
    raw[44..52].copy_from_slice(&gain.to_le_bytes());
    raw[52..60].copy_from_slice(&offset.to_le_bytes());
    raw[60..68].copy_from_slice(&denominator.to_le_bytes());
    raw
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corners_preserve_exact_affine_result_and_context() {
        for count in [-(1_i64 << 40), -1, 0, 1, 1_i64 << 40] {
            for gain in [-(1_i64 << 20), -1, 0, 1, 1_i64 << 20] {
                for offset in [-(1_i64 << 60), -1, 0, 1, 1_i64 << 60] {
                    for denominator in [1, 1_u64 << 20] {
                        let input = statement([0xa7; 32], count, gain, offset, denominator);
                        let output = evaluate(&input).unwrap();
                        assert_eq!(output.len(), 60);
                        assert_eq!(&output[..4], b"DBO1");
                        assert_eq!(&output[4..36], &input[4..36]);
                        assert_eq!(i128::from_le_bytes(output[36..52].try_into().unwrap()),
                                   i128::from(count) * i128::from(gain) + i128::from(offset));
                        assert_eq!(&output[52..60], &denominator.to_le_bytes());
                    }
                }
            }
        }
    }

    #[test]
    fn malformed_inputs_and_out_of_domain_values_refuse() {
        let valid = statement([0; 32], 0, 0, 0, 1);
        for length in 0..68 { assert_eq!(evaluate(&valid[..length]), Err(2)); }
        assert_eq!(evaluate(&[0; 69]), Err(2));
        let mut bad_header = valid;
        bad_header[0] = b'X';
        assert_eq!(evaluate(&bad_header), Err(2));
        for (count, gain, offset, denominator) in [
            ((1 << 40) + 1, 0, 0, 1), (-(1 << 40) - 1, 0, 0, 1),
            (0, (1 << 20) + 1, 0, 1), (0, -(1 << 20) - 1, 0, 1),
            (0, 0, (1 << 60) + 1, 1), (0, 0, -(1 << 60) - 1, 1),
            (0, 0, 0, 0), (0, 0, 0, (1 << 20) + 1),
        ] {
            assert_eq!(evaluate(&statement([0; 32], count, gain, offset, denominator)), Err(3));
        }
    }
}

// Kani executes the unmodified imported kernel. No stubbing, disabled checks,
// handwritten replacement implementation, or narrowed accepted numeric range.
#[cfg(kani)]
mod proofs {
    use super::*;

    #[kani::proof]
    #[kani::unwind(70)]
    fn every_accepted_statement_is_exact_and_bounded() {
        let context: [u8; 32] = kani::any();
        let count: i64 = kani::any();
        let gain: i64 = kani::any();
        let offset: i64 = kani::any();
        let denominator: u64 = kani::any();
        kani::assume((-(1_i64 << 40)..=(1_i64 << 40)).contains(&count));
        kani::assume((-(1_i64 << 20)..=(1_i64 << 20)).contains(&gain));
        kani::assume((-(1_i64 << 60)..=(1_i64 << 60)).contains(&offset));
        kani::assume((1..=(1_u64 << 20)).contains(&denominator));
        let input = statement(context, count, gain, offset, denominator);
        let output = evaluate(&input).expect("every input in the declared domain succeeds");
        assert_eq!(output.len(), 60);
        assert_eq!(&output[..4], b"DBO1");
        assert_eq!(&output[4..36], &context);
        let result = i128::from_le_bytes(output[36..52].try_into().unwrap());
        assert_eq!(result, i128::from(count) * i128::from(gain) + i128::from(offset));
        assert!((-(1_i128 << 61)..=(1_i128 << 61)).contains(&result));
        assert_eq!(u64::from_le_bytes(output[52..60].try_into().unwrap()), denominator);
        kani::cover!(count == 0 && gain == 0 && offset == 0 && denominator == 1);
        kani::cover!(count == (1_i64 << 40) && gain == (1_i64 << 20) && offset == (1_i64 << 60));
    }

    #[kani::proof]
    #[kani::unwind(70)]
    fn every_out_of_domain_statement_refuses() {
        let context: [u8; 32] = kani::any();
        let count: i64 = kani::any();
        let gain: i64 = kani::any();
        let offset: i64 = kani::any();
        let denominator: u64 = kani::any();
        kani::assume(!(-(1_i64 << 40)..=(1_i64 << 40)).contains(&count)
            || !(-(1_i64 << 20)..=(1_i64 << 20)).contains(&gain)
            || !(-(1_i64 << 60)..=(1_i64 << 60)).contains(&offset)
            || !(1..=(1_u64 << 20)).contains(&denominator));
        assert_eq!(evaluate(&statement(context, count, gain, offset, denominator)), Err(3));
        kani::cover!(denominator == 0);
    }

    #[kani::proof]
    #[kani::unwind(70)]
    fn every_wrong_header_refuses() {
        let raw: [u8; 68] = kani::any();
        kani::assume(&raw[..4] != b"DBI1");
        assert_eq!(evaluate(&raw), Err(2));
    }

    #[kani::proof]
    #[kani::unwind(70)]
    fn every_short_or_single_trailing_byte_refuses() {
        let raw: [u8; 69] = kani::any();
        let length: usize = kani::any();
        kani::assume(length <= 69 && length != 68);
        assert_eq!(evaluate(&raw[..length]), Err(2));
    }
}

