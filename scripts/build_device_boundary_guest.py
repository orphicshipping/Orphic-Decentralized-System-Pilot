#!/usr/bin/env python3
"""Build and independently rebuild a pending SP1 guest via existing SCR machinery.

Explicit developer command only. Does not edit generated guest registration,
install runtimes, select network provers, or grant device capabilities.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import shutil


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compute-runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    root=args.compute_runtime.resolve(strict=True)
    output=args.output.absolute()
    if output.resolve().is_relative_to(root):parser.error('output must be outside the Compute Runtime')
    output.mkdir(parents=True,exist_ok=False)
    report={'status':'NOT_RUN','created_at':datetime.now(timezone.utc).isoformat(),
            'registered':False,'equipment_authority':False,'proof_generated':False}
    code=2
    try:
        absent=[name for name in ('cargo','rustup') if shutil.which(name) is None]
        if absent:raise FileNotFoundError('missing developer toolchain: '+', '.join(absent))
        sys.path.insert(0,str(root))
        from ciw.device_boundary import PROGRAM
        from execution.build import make_recipe,build_from_recipe,verify_build,save_recipe
        recipe=make_recipe(PROGRAM,'sp1','zk/guest-device-boundary',root)
        save_recipe(recipe,output/'device-boundary.recipe')
        report['status']='FAIL';code=1
        first=build_from_recipe(recipe,root,out_path=output/'device-boundary.elf')
        second=verify_build(recipe,first.elf_sha256,root)
        if first.elf_sha256!=second.elf_sha256:raise ValueError('independent guest rebuild differs')
        report.update(status='PASS',recipe_identity=recipe.identity(),elf_sha256=first.elf_sha256,
                      next_gate='review and register in existing generated guest index; then execute native proof-gate')
        code=0
    except Exception as exc:
        report.update(reason=type(exc).__name__+': '+str(exc))
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    raw=(json.dumps(report,indent=2,allow_nan=False)+'\n').encode()
    (output/'build-gate.json').write_bytes(raw)
    print(json.dumps({'status':report['status'],'report_sha256':sha256(raw).hexdigest()}))
    return code


if __name__=='__main__':raise SystemExit(main())
