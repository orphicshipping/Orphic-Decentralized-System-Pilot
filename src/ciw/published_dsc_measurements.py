"""Import the NIST SRM 2232a published DSC table without recertifying it.

The table's uncertainties describe systematic effects. Repeated subsamples,
shared heating-rate effects and a single material lot preclude treating the
180 rows as independent complete uncertainty budgets.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, localcontext, InvalidOperation
import json
from pathlib import Path
import sys

from .measurement_evidence import EvidenceStore, keys, text, integer, dump, load, digest
from .published_measurement_comparisons import decimal, display

SCHEMA = "ciw.nist-srm2232a-published-dsc.v1"
FIELDS = ("id lot_id ingot_id sample_id mass_mg nominal_heating_rate_C_per_min "
          "measured_heating_rate_C_per_min replicate_index_in_table enthalpy_J_per_g "
          "systematic_standard_uncertainty_J_per_g source_pdf_page source_printed_page "
          "source_table source_row_on_page source_row_text")
RATES = (Decimal(1), Decimal(3), Decimal(5), Decimal(10))
PAGE_COUNTS = {47: 28, 48: 29, 49: 30, 50: 29, 51: 29, 52: 30, 53: 5}
SAMPLE_MASSES_MG = {"A1": "8.118", "A2": "8.078", "A3": "8.011", "A4": "8.148", "A5": "8.163",
    "B1": "8.146", "B2": "8.233", "B3": "8.224", "C": "13.663", "D": "7.574", "D2": "7.296",
    "E": "10.647", "F": "9.551", "G": "13.258", "G2": "13.185"}
PUBLISHED_SYSTEMATIC_MEANS = {Decimal(1): "0.091", Decimal(3): "0.074", Decimal(5): "0.106", Decimal(10): "0.187"}


def half_last_place(token):
    """Rounding interval half-width implied by a displayed decimal token."""
    value = decimal(token)
    return Decimal(5).scaleb(value.as_tuple().exponent - 1)


def average(values):
    return sum(values, Decimal(0)) / len(values)


class PublishedDSC(EvidenceStore):
    def evaluate(self):
        m = self.manifest
        keys(m, "schema campaign_id source source_url records metadata")
        if m["schema"] != SCHEMA:
            raise ValueError("Unsupported published DSC schema")
        text(m["campaign_id"])
        if m["source_url"] != "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-242.pdf":
            raise ValueError("This table adapter is scoped to NIST SP 260-242")
        self.read(m["source"])
        metadata = load(self.read(m["metadata"]))
        if type(metadata) is not dict:
            raise ValueError("Require published study metadata")
        if (metadata.get("schema") != "nist.dsc-published-enthalpy-extraction.v1"
            or metadata.get("source_url") != m["source_url"]
            or metadata.get("source_sha256") != m["source"]["sha256"].removeprefix("sha256:")
            or metadata.get("records_sha256") != m["records"]["sha256"].removeprefix("sha256:")):
            raise ValueError("Study metadata must bind to this source and these records")
        uncertainty = metadata.get("uncertainty_evidence")
        if (type(uncertainty) is not dict
            or uncertainty.get("row_uncertainty_kind") != "combined_standard_due_to_systematic_effects"
            or "row_coverage_factor" not in uncertainty or uncertainty["row_coverage_factor"] is not None):
            raise ValueError("Study metadata must preserve systematic standard uncertainty semantics")
        records = load(self.read(m["records"]))
        if type(records) is not list or len(records) != 180:
            raise ValueError("The complete published study requires exactly 180 rows")
        ids, lineage, sample_mass, groups, lots, ingots = set(), {}, {}, defaultdict(list), set(), set()
        normalized, coordinates = [], set()
        with localcontext() as context:
            context.prec = 200
            for row in records:
                keys(row, FIELDS)
                for field in ("id", "lot_id", "ingot_id", "sample_id", "source_table", "source_row_text"):
                    text(row[field])
                if row["id"] in ids:
                    raise ValueError("Duplicate published DSC row")
                ids.add(row["id"])
                sample, ingot, lot = row["sample_id"], row["ingot_id"], row["lot_id"]
                if len(ingot) != 1 or ingot not in "ABCDEFG" or not sample.startswith(ingot):
                    raise ValueError("Sample and source ingot are inconsistent")
                if lineage.setdefault(sample, (ingot, lot)) != (ingot, lot):
                    raise ValueError("A sample cannot change ingot or lot")
                if lot != "135139138114-400" or sample not in SAMPLE_MASSES_MG:
                    raise ValueError("Sample must belong to the published material lot and inventory")
                lots.add(lot)
                ingots.add(ingot)
                rate = decimal(row["nominal_heating_rate_C_per_min"], positive=True)
                if rate not in RATES:
                    raise ValueError("Unsupported nominal heating rate")
                measured_rate = decimal(row["measured_heating_rate_C_per_min"], positive=True)
                if abs(measured_rate - rate) > Decimal("0.003"):
                    raise ValueError("Measured rate does not belong to the published nominal-rate group")
                mass = decimal(row["mass_mg"], positive=True)
                if mass != Decimal(SAMPLE_MASSES_MG[sample]):
                    raise ValueError("Sample mass must agree with source Table 3")
                enthalpy = decimal(row["enthalpy_J_per_g"], positive=True)
                systematic_u = decimal(row["systematic_standard_uncertainty_J_per_g"], positive=True)
                if sample_mass.setdefault(sample, mass) != mass:
                    raise ValueError("Inconsistent tabulated sample mass")
                replicate = integer(row["replicate_index_in_table"], 1, 3)
                page = integer(row["source_pdf_page"], 47, 53)
                if integer(row["source_printed_page"], 39, 45) != page - 8:
                    raise ValueError("PDF and printed-page provenance disagree")
                source_row = integer(row["source_row_on_page"], 1, PAGE_COUNTS[page])
                if (page, source_row) in coordinates:
                    raise ValueError("Duplicate source table coordinate")
                coordinates.add((page, source_row))
                source_tokens = [sample, row["mass_mg"], row["measured_heating_rate_C_per_min"],
                                 row["enthalpy_J_per_g"], row["systematic_standard_uncertainty_J_per_g"]]
                if row["source_row_text"].split() != source_tokens:
                    raise ValueError("Source row text disagrees with retained decimal tokens")
                if row["source_table"] not in ("A.2.1", "Table A.2.1"):
                    raise ValueError("Unexpected source table")
                groups[(sample, rate)].append(row)
                normalized.append(dict(row,
                    mass_kg=display(mass * Decimal("0.000001")),
                    measured_heating_rate_K_per_s=display(measured_rate / 60),
                    enthalpy_J_per_kg=display(enthalpy * 1000),
                    systematic_standard_uncertainty_J_per_kg=display(systematic_u * 1000),
                    uncertainty_scope="systematic_effects_only_not_total_per_measurement",
                    replicate_semantics="ordinal_in_published_table_not_acquisition_order"))
            if len(lots) != 1 or len(ingots) != 7 or len(lineage) != 15 or len(groups) != 60:
                raise ValueError("Published study requires one lot, seven ingots, fifteen samples, and sixty sample/rate groups")
            if coordinates != {(page, row) for page, count in PAGE_COUNTS.items() for row in range(1, count + 1)}:
                raise ValueError("The complete source table coordinate inventory is required")
            for sample in lineage:
                for rate in RATES:
                    rows = groups[(sample, rate)]
                    if len(rows) != 3 or {r["replicate_index_in_table"] for r in rows} != {1, 2, 3}:
                        raise ValueError("Each sample/rate group requires the three published replicates")
                    ordered = sorted(rows, key=lambda r: (r["source_pdf_page"], r["source_row_on_page"]))
                    if [r["replicate_index_in_table"] for r in ordered] != [1, 2, 3]:
                        raise ValueError("Replicate ordinal must follow source table coordinates")
            by_rate = []
            for rate in RATES:
                rows = [r for r in records if decimal(r["nominal_heating_rate_C_per_min"]) == rate]
                enthalpies = [decimal(r["enthalpy_J_per_g"]) for r in rows]
                u_tokens = [r["systematic_standard_uncertainty_J_per_g"] for r in rows]
                mean_u = average([decimal(t) for t in u_tokens])
                radius = average([half_last_place(t) for t in u_tokens])
                published_token = PUBLISHED_SYSTEMATIC_MEANS[rate]
                published_u = decimal(published_token)
                overlap = abs(mean_u - published_u) <= radius + half_last_place(published_token)
                by_rate.append({"nominal_heating_rate_C_per_min": display(rate), "measurement_rows": len(rows),
                    "samples": len({r["sample_id"] for r in rows}), "ingots": len({r["ingot_id"] for r in rows}),
                    "descriptive_mean_enthalpy_J_per_g": display(average(enthalpies)),
                    "minimum_enthalpy_J_per_g": display(min(enthalpies)), "maximum_enthalpy_J_per_g": display(max(enthalpies)),
                    "mean_systematic_standard_uncertainty_J_per_g": display(mean_u),
                    "mean_systematic_u_rounding_half_width_J_per_g": display(radius),
                    "published_model_systematic_u_input_J_per_g": published_token,
                    "published_systematic_u_compatible_with_display_rounding": overlap,
                    "uncertainty_of_mean": "not_computed_shared_effects_and_random_model_required"})
        report = {"schema": "ciw.published-dsc-import-report.v1", "campaign_id": m["campaign_id"],
            "analysis_mode": "published_measurement_table_import_and_descriptive_summary",
            "data_origin": "actual_published_DSC_measurements", "source_url": m["source_url"],
            "manifest_sha256": digest(self.raw_manifest), "source_sha256": m["source"]["sha256"],
            "records_sha256": m["records"]["sha256"], "metadata_sha256": m["metadata"]["sha256"],
            "counts": {"measurement_rows": len(records), "material_lots": len(lots), "ingots": len(ingots),
                       "samples": len(lineage), "sample_rate_groups": len(groups), "nominal_heating_rates": 4},
            "by_heating_rate": by_rate,
            "source_precision_check": "compatible" if all(r["published_systematic_u_compatible_with_display_rounding"] for r in by_rate) else "requires_review",
            "published_model": "Hierarchical Bayesian model with ingot/subsample effects, Student-t random error and heating-rate-specific systematic effects; not refitted here.",
            "certified_value_reproduction": "not_claimed; arithmetic means do not reproduce the zero-rate posterior intercept",
            "independent_held_out_reference": "not_established; the certificate derives from these observations",
            "local_instrument_qualification": "not_established", "release_status": "unchanged",
            "limitations": ["Indium calorimetry supports DSC evidence handling; it is not a polymer Tg, melting-peak or enthalpy validation.",
                "Systematic standard uncertainties remain systematic; random effects are not silently omitted from a claimed total.",
                "Replicates and ingots belong to one material lot; no independent-lot count or uncertainty reduction is inferred.",
                "No raw heat-flow curves, reconstructed acquisition times, independent criteria approval or new physical experiment is supplied."]}
        return report, {"schema": "ciw.normalized-published-dsc-records.v1", "records": normalized}, metadata


def adapt_campaign(manifest_path, output_dir):
    campaign = PublishedDSC(manifest_path)
    report, normalized, metadata = campaign.evaluate()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output / "campaign.json").write_bytes(campaign.raw_manifest)
    (output / "source-records.json").write_bytes(campaign.read(campaign.manifest["records"]))
    (output / "study-metadata.json").write_bytes(dump(metadata))
    (output / "normalized-records.json").write_bytes(dump(normalized))
    (output / "report.json").write_bytes(dump(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = adapt_campaign(args.manifest, args.output_dir)
        print(dump(report).decode())
        return 0 if report["source_precision_check"] == "compatible" else 2
    except (ValueError, OSError, KeyError, TypeError, InvalidOperation) as error:
        print(json.dumps({"status": "refused", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
