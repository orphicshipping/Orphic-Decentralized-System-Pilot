#!/usr/bin/env python3
"""Project the owned VFE teaching inputs; native execution is NOT_RUN."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _teaching_payloads import build_payload as _build, main as _main

def build_payload() -> dict:
    return _build("VFE")

if __name__ == "__main__":
    raise SystemExit(_main("VFE", "vfe-sensor-bias-teach/results/vfe_sensor_bias_render.json"))
