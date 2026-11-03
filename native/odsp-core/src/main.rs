// SPDX-License-Identifier: AGPL-3.0-or-later
#![forbid(unsafe_code)]
use std::io::{self,Read,Write};
use serde_json::json;
use odsp_numerical_core::{run_with_lapack,lapack::Lapack,MAX_INPUT,MAX_OUTPUT};

fn main() {
    let args:Vec<_>=std::env::args().collect();
    if args.len()==3 && args[1]=="identity-v1" {
        match Lapack::load(std::path::Path::new(&args[2])) {
            Ok(lapack) => {
                let mut manifest:serde_json::Value=serde_json::from_slice(include_bytes!(concat!(env!("OUT_DIR"),"/build-identity.json"))).unwrap();
                manifest["native_configuration"]=lapack.configuration();
                io::stdout().write_all(&serde_json::to_vec(&manifest).unwrap()).unwrap();
            },
            Err(_) => std::process::exit(2),
        }
        return;
    }
    let mut raw=Vec::new();
    let result=if args.len()!=3 || args[1]!="compute-v1" {Err("protocol_argument")}
        else if io::stdin().take((MAX_INPUT+1) as u64).read_to_end(&mut raw).is_err() {Err("input_io")}
        else {Lapack::load(std::path::Path::new(&args[2])).and_then(|library| run_with_lapack(&raw, Some(&library)))};
    let reply=match result {
        Ok(value)=>{
            let bytes=serde_json::to_vec(&value).unwrap();
            json!({"schema":"odsp.rust-numerical.reply.v1","status":"computed",
                "input_commitment":execution_core::InputIdentity::of(&raw).to_hex(),
                "output_commitment":execution_core::OutputIdentity::of(&bytes).to_hex(),
                "result_json":String::from_utf8(bytes).unwrap(),"proof_established":false})
        },
        Err(reason)=>json!({"schema":"odsp.rust-numerical.reply.v1","status":"refused","reason":reason,"proof_established":false})
    };
    let mut bytes=serde_json::to_vec(&reply).unwrap();bytes.push(b'\n');
    if bytes.len()>MAX_OUTPUT {
        io::stdout().write_all(b"{\"schema\":\"odsp.rust-numerical.reply.v1\",\"status\":\"refused\",\"reason\":\"output_budget\",\"proof_established\":false}\n").unwrap();
    } else {io::stdout().write_all(&bytes).unwrap();}
}
