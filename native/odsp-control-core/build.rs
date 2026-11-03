// SPDX-License-Identifier: AGPL-3.0-or-later
use std::{collections::BTreeMap, env, fs, path::Path, process::Command};
fn inventory(path: &Path, root: &Path, values: &mut BTreeMap<String, String>) {
    if path.is_dir() {
        for entry in fs::read_dir(path).expect("source directory") {
            inventory(&entry.expect("source entry").path(), root, values);
        }
    } else {
        let name = path
            .strip_prefix(root)
            .expect("source within root")
            .to_str()
            .unwrap()
            .replace('\\', "/");
        let digest = execution_commitment::sha256(&fs::read(path).expect("source bytes"));
        values.insert(name, digest.iter().map(|b| format!("{b:02x}")).collect());
        println!("cargo:rerun-if-changed={}", path.display());
    }
}
fn main() {
    let local = env::var("CARGO_MANIFEST_DIR").unwrap();
    let root = Path::new(&local).parent().unwrap().parent().unwrap();
    let mut sources = BTreeMap::new();
    for file in [
        "Cargo.toml",
        "Cargo.lock",
        "build.rs",
        "rust-toolchain.toml",
        "src",
    ] {
        inventory(&Path::new(&local).join(file), root, &mut sources);
    }
    // Pin the Python semantics/catalog against which this native engine was qualified.
    for name in [
        "src/ciw/rust_principal_policy.py",
        "src/ciw/principal_policy.py",
        "src/ciw/agent_tools.py",
        "src/ciw/agent_api.py",
        "src/ciw/control_contracts.py",
        "src/ciw/campaign_contracts.py",
        "src/ciw/nise_contract.py",
        "src/ciw/domain_surface.py",
        "src/ciw/workbench.py",
        "src/ciw/fluid_contract.py",
        "src/ciw/declared_workload.py",
        "src/ciw/control_gateway.py",
        "src/ciw/knowledge_mcp.py",
        "src/ciw/state_read_profile.py",
        "src/ciw/logistics_profile.py",
        "src/ciw/recompiler_profile.py",
        "src/ciw/schematic_profile.py",
        "src/ciw/clocksync_profile.py",
        "src/ciw/nise_profile.py",
        "src/ciw/offline_intake.py",
        "src/ciw/proof_profile.py",
        "src/ciw/domain_read_profile.py",
        "src/ciw/mcp_toolbox.py",
        "src/ciw/live_intake.py",
        "src/ciw/metrology_operations.py",
        "src/ciw/adapters/subprocess.py",
        "src/ciw/process_policy.py",
        "src/ciw/session.py",
    ] {
        inventory(&root.join(name), root, &mut sources);
    }
    let imported = root.join("instruments/execution/compute-runtime/crates");
    for file in [
        "Cargo.toml",
        "execution-commitment/Cargo.toml",
        "execution-commitment/src",
        "execution-serialization/Cargo.toml",
        "execution-serialization/src",
    ] {
        inventory(&imported.join(file), root, &mut sources);
    }
    let compiler = Command::new(env::var("RUSTC").unwrap())
        .arg("--version")
        .output()
        .unwrap();
    assert!(compiler.status.success());
    let manifest = serde_json::json!({"schema":"odsp.rust-control-build.v1", "native_sources_sha256":sources,
        "rustc":String::from_utf8(compiler.stdout).unwrap().trim(), "target":env::var("TARGET").unwrap(),
        "profile":env::var("PROFILE").unwrap()});
    fs::write(
        Path::new(&env::var("OUT_DIR").unwrap()).join("build-identity.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
}
