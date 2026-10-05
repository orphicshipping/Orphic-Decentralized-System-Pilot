"""Independent Decimal closed forms for the bounded uniaxial Maxwell profile.

No imports of the production response, exp/expm1 or its frequency branches.
Inputs are prevalidated binary64 declarations. Precision is 80 decimal digits;
this is an independent numerical oracle, not a formal interval certificate.
"""
from decimal import Decimal, localcontext


def reference_response(p):
    with localcontext() as context:
        context.prec = 80
        d = lambda x: Decimal.from_float(float(x))
        zero, one, two = Decimal(0), Decimal(1), Decimal(2)
        base, strain = d(p["equilibrium_modulus_pa"]), d(p["step_strain"])
        modes = [(d(m["modulus_pa"]), d(m["relaxation_time_s"])) for m in p["modes"]]
        times = {key: [] for key in ("relaxation_modulus_pa", "stress_pa", "stored_energy_j_m3",
                                     "dissipated_energy_j_m3", "dissipation_rate_w_m3")}
        for time in p["time_s"]:
            elastic, stored, lost, rate = base, base, zero, zero
            for modulus, tau in modes:
                x = d(time) / tau
                # At x > 800 all exponential contributions round to binary64
                # zero even after the largest supported stiffness/rate scaling.
                f = (-x).exp() if x <= 800 else zero
                f2 = (-two*x).exp() if x <= 800 else zero
                elastic += modulus * f
                stored += modulus * f2
                lost += modulus * (one - f2)
                rate += modulus / tau * f2
            for name, value in (("relaxation_modulus_pa", elastic), ("stress_pa", strain*elastic),
                                ("stored_energy_j_m3", strain*strain*stored/two),
                                ("dissipated_energy_j_m3", strain*strain*lost/two),
                                ("dissipation_rate_w_m3", strain*strain*rate)):
                times[name].append(float(value))
        frequency = {"storage_modulus_pa": [], "loss_modulus_pa": []}
        for omega in p["angular_frequency_rad_s"]:
            storage, loss = base, zero
            for modulus, tau in modes:
                x = d(omega) * tau
                denominator = one + x*x
                storage += modulus*x*x/denominator
                loss += modulus*x/denominator
            frequency["storage_modulus_pa"].append(float(storage))
            frequency["loss_modulus_pa"].append(float(loss))
        return {"time_response": times, "frequency_response": frequency}
