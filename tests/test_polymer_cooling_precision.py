"""Regression for a valid slow-cooling uncertainty contribution lost to cancellation."""
from decimal import Decimal, localcontext
import math

import pytest

from ciw.polymer_contract import example_request, validate_request
from ciw.polymer_models import engineering_estimate, PROPERTY_BOUNDS
from ciw.polymer_verification import _cooling_oracle


def slow_request(horizon):
    p=example_request();p["model"]["validity"]["elapsed_s"][1]=2000;cooling=p["model"]["cooling"];cooling["forecast_horizon_s"]=horizon
    for name in PROPERTY_BOUNDS:cooling[name]["standard_uncertainty"]=0
    for name,value in {"density_kg_m3":1e4,"heat_capacity_j_kg_k":1e5,"volume_m3":1,
                       "area_m2":1e-4,"heat_transfer_w_m2_k":1e-4,"conductivity_w_m_k":1e4}.items():
        cooling[name]["value"]=value
    for sensor in p["sensors"]:
        if sensor["sensor_id"]=="part-temperature":
            for sample in sensor["samples"]:sample["standard_uncertainty"]=0
    return validate_request(p)


@pytest.mark.parametrize("horizon", [0,1e-6,1e-3,1,5,10,100,1000])
def test_ambient_sensitivity_survives_near_unity_decay(horizon):
    p=slow_request(horizon)
    with localcontext() as ctx:
        ctx.prec=70
        expected=float((Decimal(1)-(-Decimal.from_float(float(horizon))/Decimal('1e17')).exp())*Decimal.from_float(0.2))
    for report in (engineering_estimate(p)["cooling"],_cooling_oracle(p)):
        assert report["status"]=="ESTIMATED"
        assert math.isclose(report["standard_uncertainty_k"],expected,rel_tol=2e-15,abs_tol=0)
    if horizon>0:assert expected>0
