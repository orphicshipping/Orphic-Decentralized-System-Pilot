#!/usr/bin/env python3
"""Project the owned PLSR teaching inputs; native execution is NOT_RUN."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _teaching_payloads import build_payload as _build, main as _main

def build_payload() -> dict:
    return _build("PLSR")

if __name__ == "__main__":
    raise SystemExit(_main("PLSR", "plsr-stability-verdict/results/plsr_render.json"))
