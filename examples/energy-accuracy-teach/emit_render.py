#!/usr/bin/env python3
"""Project the owned ENERGY_ACCURACY teaching inputs; native execution is NOT_RUN."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _teaching_payloads import build_payload as _build, main as _main

def build_payload() -> dict:
    return _build("ENERGY_ACCURACY")

if __name__ == "__main__":
    raise SystemExit(_main("ENERGY_ACCURACY", "energy-accuracy-teach/results/energy_accuracy_render.json"))
