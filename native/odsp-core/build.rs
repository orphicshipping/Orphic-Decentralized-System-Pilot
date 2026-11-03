// SPDX-License-Identifier: AGPL-3.0-or-later
use std::{collections::BTreeMap, env, fs, path::Path, process::Command};

fn inventory(path: &Path, root: &Path, values: &mut BTreeMap<String, String>) {
    if path.is_dir() {
        for entry in fs::read_dir(path).expect("source directory") {
            inventory(&entry.expect("source entry").path(), root, values);
        }
    } else {
        let name = path.strip_prefix(root).expect("source within root").to_str().unwrap().replace('\\', "/");
        let digest = execution_commitment::sha256(&fs::read(path).expect("source bytes"));
        values.insert(name, digest.iter().map(|b| format!("{b:02x}")).collect());
        println!("cargo:rerun-if-changed={}", path.display());
    }
}

fn main() {
    let local = env::var("CARGO_MANIFEST_DIR").unwrap();
    let root = Path::new(&local).parent().unwrap().parent().unwrap();
    let mut sources = BTreeMap::new();
    for file in ["Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml", "src"] {
        inventory(&Path::new(&local).join(file), root, &mut sources);
    }
    let imported = root.join("instruments/execution/compute-runtime/crates");
    inventory(&imported.join("Cargo.toml"), root, &mut sources);
    for name in ["execution-core", "execution-commitment", "execution-serialization", "execution-model", "execution-kernel",
                 "execution-trace", "execution-native", "execution-verification"] {
        for file in ["Cargo.toml", "src"] {
            inventory(&imported.join(name).join(file), root, &mut sources);
        }
    }
    let compiler = Command::new(env::var("RUSTC").unwrap()).arg("--version").output().unwrap();
    assert!(compiler.status.success());
    let manifest = serde_json::json!({"schema":"odsp.rust-numerical-build.v1",
        "native_sources_sha256":sources,
        "rustc":String::from_utf8(compiler.stdout).unwrap().trim(),
        "target":env::var("TARGET").unwrap(), "profile":env::var("PROFILE").unwrap()});
    fs::write(Path::new(&env::var("OUT_DIR").unwrap()).join("build-identity.json"),
              serde_json::to_vec(&manifest).unwrap()).unwrap();
}
