# Native device kernel qualification

The existing `guest-device-boundary` kernel has executed native Kani verification.
The qualification crate compiles that exact imported Rust file by path, with no
replacement numerical engine. It also compiles the existing execution commitment
and model crates. Imported sources and the guest registry remain unchanged.

The actual SP1 CPU proof gate remains open. A successful Kani run is not an SP1
proof, hardware attestation, physical calibration, or principal grant.

## Executed results

Kani **0.68.0**, its matching Rust **1.100.0-nightly (8925ea358 2026-08-20)**,
and CBMC **6.11.0** ran four harnesses with zero failed harnesses. The first run
took 68.756 seconds. The source/runtime-verified replay runner then completed all
four harnesses again in 77.150 seconds. No stub, mock, disabled safety check or
cloud prover was used.
All harnesses use unwind bound 70; unwinding assertions remain enabled.

| Harness | Verified domain |
|---|---|
| Accepted statement | Every context byte and every count, gain, offset and denominator in the existing declared domain; exact output header/context, integer arithmetic, result bound and denominator |
| Invalid numerical domain | All i64/i64/i64/u64 values violating at least one declared bound refuse with the existing error code |
| Incorrect header | Every 68-byte input whose first four bytes are not `DBI1` refuses |
| Incorrect length | Every byte content at lengths 0 through 69 except 68 refuses |

The proof does not quantify arbitrarily large allocations, a different target
compiler, the full SP1 runtime, cryptographic soundness, or live device behavior.
The log retains compiler warnings and unsupported operations on unreachable paths;
it does not suppress them. Concrete native tests additionally passed: three Rust
tests, **762** valid Python/kernel/commitment comparisons and **79** invalid-input
refusal comparisons. Five Python qualification tests passed with no skips.

`native/odsp-proof/source-lock.json` binds 52 existing source inputs.
`runtime-lock.json` gives official archive URLs, exact byte sizes and SHA-256s.
`tool-files.json` binds the complete extracted Kani and matching nightly runtime
used here. These local receipts are reproducibility evidence; editable JSON is
not a signed proof certificate or a new trust authority.

## Replay

Provision the archive bytes in `runtime-lock.json` into a private directory after
verifying their hashes. The layout used here is `kani-0.68.0/` for the Kani bundle
and `nightly/` for the matching rustc, rust-std, cargo and rust-src component trees.
The original qualification retained compressed and unpacked size manifests; no
global `rustup` change was required. See the official [Kani installation guide](https://model-checking.github.io/kani/install-guide.html)
and [harness attributes](https://model-checking.github.io/kani/reference/attributes.html).

From the repository root:

```sh
python native/odsp-proof/qualification.py sources
python native/odsp-proof/run_kani.py --runtime /tmp/odsp-native-proof --output /tmp/odsp-kani-new-run
```

The replay runner verifies source/runtime hashes, starts with a minimal environment,
reserves 1 GiB free space plus 64 MiB working capacity, retains command/exit/log/result
files, and accepts only all four successful harnesses with no unresolved properties.
An existing output directory refuses rather than overwriting prior evidence.

The small native comparison driver has only existing local path dependencies:

```sh
cargo test --manifest-path native/odsp-proof/Cargo.toml --release --locked --offline
cargo build --manifest-path native/odsp-proof/Cargo.toml --release --locked --offline
ODSP_REQUIRE_NATIVE_PROOF=1 ODSP_KANI_RESULT=/path/to/result.json ODSP_DEVICE_KERNEL_BINARY=/path/to/device_kernel PYTHONPATH=src python -m pytest tests/test_native_proof_qualification.py -q
```

The actual run used a private Cargo home/target directory and the pinned matching
nightly compiler. Missing native artifacts fail when `ODSP_REQUIRE_NATIVE_PROOF=1`;
ordinary source-only runs explicitly skip the unavailable native checks.

## SP1 build and proof inputs; gate still held

```sh
python native/odsp-proof/qualification.py sp1-preflight
PYTHONPATH=src python native/odsp-proof/prepare_sp1_inputs.py --output /tmp/odsp-sp1-new-inputs
```

Preflight exited **2**, identifying the actual missing fork, compiler, guest lock
and qualified guest registration, plus insufficient scratch capacity. Preparation
produces the exact existing descriptor, three accepted input/output vectors and
concrete original-host CPU prove/verify/wrong-output command arrays. It reports
`native_commands_executed=false` and never registers a guest.

Required source checkout: the existing project's SP1 fork revision
`b38b61209e45e969289e70d5cf79dc763460bc41`, tree
`7deca3aced8d8eb84dfcede98285a192c862ea4c`. The existing Succinct toolchain archive
is **384,963,362 bytes**, SHA-256
`12c94435d41bfe4e20131bbcce40b35abd32270ad792befc653af4e3fabc192f`.
The required target is `riscv64im-succinct-zkvm-elf`. Exact URLs are in the source
lock; neither fork nor compiler was downloaded in this lane.

The [official SP1 hardware guidance](https://docs.succinct.xyz/docs/sp1/getting-started/hardware-requirements)
requires at least 10 GB disk for local setup, beyond this lane's 1.5 GiB scratch
budget and the shared environment's available capacity. The preflight enforces
10,000,000,000 working bytes plus the campaign's 1 GiB reserve. This is a capacity
hold, not an automatic approval denial. A larger authorized existing environment
can run the following remaining work without using a cloud prover:

1. Make an isolated source copy. Place the exact clean fork at the existing adjacent
   `notationsystems/SP1-zero-knowledge-virtual-machine` path and install the verified
   Succinct compiler privately. Resolve and retain a guest `Cargo.lock` from the
   pinned source; the device guest intentionally has no fabricated lock today.
2. Use original `execution.build.make_recipe(PROGRAM, 'sp1',
   'zk/guest-device-boundary', repo_root=isolated_compute_runtime)`, then
   `build_from_recipe` and `verify_build`. Retain the generated dependency lock,
   recipe, compiler identity and two matching ELF hashes. The generated lock is a
   new build input requiring review; no source manifest is silently updated.
3. Build original `zk/sp1-adapter` with its existing locked dependencies and run the
   prepared CPU commands. Require successful fresh verification and rejection of
   changed descriptor, input, output, exit code and corrupted proof. The prepared
   command arrays cover valid and changed-output cases; the remaining tamper cases
   must also be executed before closing the gate.
4. Only after those results, review any source/ELF registry change and bind the
   existing `ExistingSP1Verifier`. The preparation tools do not perform this step.

Local evidence is retained under `/tmp/odsp-native-proof`: `kani-attempt-1/`,
`kani-replay-qualified/`, Cargo logs/commands, `native-qualification.xml`,
`sp1-preflight.json`, prepared input bytes and the final receipt. Original logs
and failures are preserved. Hosted, physical and full-programme qualification are
separate gates.
