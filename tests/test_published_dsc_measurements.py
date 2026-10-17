"""Portable curated NIST table tests; fake evidence bytes test I/O, not calibration.

The complete numeric fixture was extracted from Table A.2.1 of NIST SP260-242,
PDF pages47-53, and independently checked through pypdf, pdfplumber and renders.
It is embedded so unit tests never need network access or a workspace PDF.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

import pytest

from ciw.measurement_evidence import digest, dump, load
from ciw.published_dsc_measurements import SCHEMA, PublishedDSC, adapt_campaign, half_last_place

PUBLISHED_PDF_SHA256 = "686d971f0ae134967aae0ed404708aa54dab9e7199991612713a00dbfd82a6be"
SOURCE_URL = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-242.pdf"
PAGE_COUNTS = {47: 28, 48: 29, 49: 30, 50: 29, 51: 29, 52: 30, 53: 5}
PUBLISHED_TABLE = """D2 7.296 1.000 28.68 0.09
D2 7.296 0.999 28.31 0.09
D2 7.296 0.999 28.54 0.09
D 7.574 0.999 27.58 0.09
D 7.574 0.999 28.82 0.09
D 7.574 0.999 28.78 0.09
A3 8.011 0.999 28.59 0.09
A3 8.011 0.999 29.00 0.09
A3 8.011 0.999 28.69 0.09
A2 8.078 0.999 28.33 0.09
A2 8.078 0.999 28.52 0.09
A2 8.078 0.999 28.66 0.09
A1 8.118 0.999 28.08 0.09
A1 8.118 0.999 28.79 0.09
A1 8.118 0.999 28.66 0.09
B1 8.146 0.999 28.53 0.09
B1 8.146 0.999 28.59 0.09
B1 8.146 0.999 28.72 0.09
A4 8.148 0.999 28.29 0.09
A4 8.148 0.999 28.25 0.09
A4 8.148 1.000 28.37 0.09
A5 8.163 0.999 28.68 0.09
A5 8.163 0.999 28.74 0.09
A5 8.163 0.999 28.79 0.09
B3 8.224 0.999 28.58 0.09
B3 8.224 0.999 28.47 0.09
B3 8.224 0.999 28.70 0.09
B2 8.233 0.999 28.65 0.09
B2 8.233 0.999 28.62 0.09
B2 8.233 0.999 28.73 0.09
F 9.551 0.999 28.80 0.09
F 9.551 0.999 28.59 0.09
F 9.551 0.999 28.65 0.09
E 10.647 0.999 28.77 0.09
E 10.647 0.999 28.55 0.09
E 10.647 0.999 28.56 0.09
G2 13.185 1.000 28.65 0.09
G2 13.185 1.000 28.64 0.09
G2 13.185 0.999 28.84 0.09
G 13.258 0.999 28.77 0.09
G 13.258 0.999 28.13 0.09
G 13.258 0.999 28.56 0.09
C 13.663 0.999 28.51 0.09
C 13.663 0.999 28.69 0.09
C 13.663 0.999 28.73 0.09
D2 7.296 2.999 28.44 0.07
D2 7.296 2.999 28.55 0.07
D2 7.296 2.999 28.64 0.07
D 7.574 2.999 28.64 0.07
D 7.574 2.999 28.66 0.07
D 7.574 2.999 28.70 0.07
A3 8.011 2.999 28.70 0.07
A3 8.011 2.999 28.75 0.07
A3 8.011 2.999 28.69 0.07
A2 8.078 2.999 28.76 0.07
A2 8.078 2.999 28.63 0.07
A2 8.078 2.999 28.76 0.07
A1 8.118 2.999 28.67 0.07
A1 8.118 2.999 28.68 0.07
A1 8.118 2.998 28.71 0.07
B1 8.146 2.999 28.92 0.07
B1 8.146 2.999 28.87 0.07
B1 8.146 2.999 28.82 0.07
A4 8.148 2.999 28.45 0.07
A4 8.148 2.998 28.32 0.07
A4 8.148 2.999 28.30 0.07
A5 8.163 2.999 28.66 0.07
A5 8.163 2.998 28.51 0.07
A5 8.163 2.998 28.68 0.07
B3 8.224 2.999 28.72 0.07
B3 8.224 2.998 28.61 0.08
B3 8.224 2.999 28.67 0.07
B2 8.233 2.999 28.65 0.07
B2 8.233 2.999 28.39 0.07
B2 8.233 2.999 28.71 0.07
F 9.551 2.999 28.69 0.07
F 9.551 2.999 28.64 0.07
F 9.551 2.999 28.66 0.07
E 10.647 2.999 28.54 0.07
E 10.647 2.999 28.65 0.07
E 10.647 2.999 28.59 0.07
G2 13.185 2.999 28.67 0.07
G2 13.185 2.999 28.72 0.07
G2 13.185 2.999 28.40 0.07
G 13.258 2.999 28.81 0.07
G 13.258 2.999 28.74 0.07
G 13.258 2.999 28.81 0.07
C 13.663 2.999 28.75 0.07
C 13.663 2.999 28.88 0.07
C 13.663 2.999 28.55 0.07
D2 7.296 4.998 28.76 0.10
D2 7.296 4.998 28.73 0.11
D2 7.296 4.998 28.40 0.11
D 7.574 4.998 28.77 0.11
D 7.574 4.998 28.70 0.11
D 7.574 4.998 28.69 0.11
A3 8.011 4.998 28.89 0.11
A3 8.011 4.998 28.90 0.11
A3 8.011 4.998 28.92 0.11
A2 8.078 4.998 28.87 0.11
A2 8.078 4.998 28.92 0.11
A2 8.078 4.998 28.78 0.11
A1 8.118 4.998 28.77 0.11
A1 8.118 4.998 28.77 0.11
A1 8.118 4.998 28.71 0.11
B1 8.146 4.998 28.86 0.11
B1 8.146 4.998 28.82 0.11
B1 8.146 4.998 28.97 0.11
A4 8.148 4.998 28.25 0.11
A4 8.148 4.998 28.18 0.11
A4 8.148 4.998 28.14 0.10
A5 8.163 4.998 28.68 0.10
A5 8.163 4.998 28.70 0.10
A5 8.163 4.998 28.52 0.10
B3 8.224 4.998 28.69 0.10
B3 8.224 4.998 28.78 0.10
B3 8.224 4.998 28.82 0.10
B2 8.233 4.998 28.78 0.10
B2 8.233 4.998 28.79 0.10
B2 8.233 4.998 28.72 0.10
F 9.551 4.998 28.69 0.10
F 9.551 4.998 28.66 0.10
F 9.551 4.998 28.73 0.10
E 10.647 4.998 28.77 0.10
E 10.647 4.998 28.53 0.10
E 10.647 4.998 28.77 0.10
G2 13.185 4.998 28.45 0.10
G2 13.185 4.998 28.48 0.10
G2 13.185 4.998 28.26 0.10
G 13.258 4.998 28.87 0.11
G 13.258 4.998 28.83 0.11
G 13.258 4.998 28.80 0.11
C 13.663 4.998 28.89 0.11
C 13.663 4.998 28.83 0.11
C 13.663 4.998 28.90 0.11
D2 7.296 9.999 28.81 0.19
D2 7.296 9.998 28.76 0.19
D2 7.296 9.998 28.69 0.19
D 7.574 9.998 28.90 0.19
D 7.574 9.997 28.82 0.19
D 7.574 9.997 28.91 0.19
A3 8.011 9.998 28.95 0.19
A3 8.011 9.998 28.97 0.19
A3 8.011 9.998 28.95 0.19
A2 8.078 9.998 28.79 0.19
A2 8.078 9.998 28.87 0.19
A2 8.078 9.998 28.93 0.19
A1 8.118 9.997 28.89 0.19
A1 8.118 9.997 28.97 0.19
A1 8.118 9.998 28.94 0.19
B1 8.146 9.998 29.12 0.19
B1 8.146 9.998 29.06 0.19
B1 8.146 9.997 29.09 0.19
A4 8.148 9.997 28.39 0.19
A4 8.148 9.998 28.41 0.19
A4 8.148 9.998 28.40 0.19
A5 8.163 9.997 28.70 0.19
A5 8.163 9.997 28.64 0.19
A5 8.163 9.998 28.78 0.19
B3 8.224 9.998 28.79 0.19
B3 8.224 9.997 28.86 0.19
B3 8.224 9.997 28.84 0.19
B2 8.233 9.998 28.81 0.19
B2 8.233 9.998 28.83 0.19
B2 8.233 9.998 28.86 0.19
F 9.551 9.998 28.93 0.19
F 9.551 9.997 28.90 0.19
F 9.551 9.997 28.88 0.19
E 10.647 9.997 28.84 0.19
E 10.647 9.997 28.82 0.19
E 10.647 9.997 28.82 0.19
G2 13.185 9.998 28.59 0.19
G2 13.185 9.998 28.62 0.18
G2 13.185 9.997 28.42 0.18
G 13.258 9.997 28.88 0.19
G 13.258 9.998 28.61 0.19
G 13.258 9.997 28.87 0.19
C 13.663 9.998 28.95 0.19
C 13.663 9.998 28.92 0.19
C 13.663 9.998 28.93 0.19"""
PUBLISHED_METADATA = json.loads(r'''{
  "schema": "nist.dsc-published-enthalpy-extraction.v1",
  "source_url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.260-242.pdf",
  "source_file": "NIST.SP.260-242.pdf",
  "source_sha256": "686d971f0ae134967aae0ed404708aa54dab9e7199991612713a00dbfd82a6be",
  "source_doi": "10.6028/NIST.SP.260-242",
  "publication": "NIST SP 260-242, September 2024",
  "material": "SRM 2232a indium metal",
  "endpoint": "Measured enthalpy of fusion at the stated nonzero measured heating rate",
  "records_file": "records.json",
  "records_sha256": "a8d578d37a29ea6c54fca8acde41b5e0258fc0fff66d94bf9a65ab73f3083ec6",
  "record_count": 180,
  "distinct_sample_count": 15,
  "distinct_ingot_count": 7,
  "distinct_lot_count": 1,
  "record_counts_by_ingot": {
    "A": 60,
    "B": 36,
    "C": 12,
    "D": 24,
    "E": 12,
    "F": 12,
    "G": 24
  },
  "nominal_heating_rate_C_per_min": [
    "1",
    "3",
    "5",
    "10"
  ],
  "reported_measurement_sequence_nominal_C_per_min": [
    "1",
    "10",
    "3",
    "5"
  ],
  "table_order_nominal_C_per_min": [
    "1",
    "3",
    "5",
    "10"
  ],
  "replicate_semantics": "One-based occurrence within a sample and nominal-rate group in Table A.2.1. The publication does not provide per-row acquisition timestamps or prove this table order is acquisition order.",
  "nominal_rate_assignment": "Derived from the four documented nominal groups, retaining every measured rate token independently.",
  "ingot_assignment": "Sample label prefix A through G; Sec.2.3.2 PDF16 and Fig.7 PDF23 distinguish ingots and subsamples. All source ingots are from one lot, reported in Sec.2.1 PDF11.",
  "sample_masses_from_table3_mg": {
    "A1": "8.118",
    "A2": "8.078",
    "A3": "8.011",
    "A4": "8.148",
    "A5": "8.163",
    "B1": "8.146",
    "B2": "8.233",
    "B3": "8.224",
    "C": "13.663",
    "D": "7.574",
    "D2": "7.296",
    "E": "10.647",
    "F": "9.551",
    "G": "13.258",
    "G2": "13.185"
  },
  "calibration_evidence": {
    "instrument": "TA Instruments Q2000 DSC",
    "source_pdf_pages": [
      14,
      15,
      16,
      17,
      31,
      32
    ],
    "scope": "Calibration method, standards, and uncertainty treatment reported in the primary publication; original certificates and original instrument files are not present in this extraction.",
    "temperature_calibrants": [
      {
        "material": "PTB indium",
        "serial_number": "KM-31402",
        "melting_temperature_C": "156.598",
        "expanded_uncertainty_C": "0.004",
        "coverage_factor": "2"
      },
      {
        "material": "PTB tin",
        "serial_number": "KM-31403",
        "melting_temperature_C": "231.928",
        "expanded_uncertainty_C": "0.004",
        "coverage_factor": "2"
      }
    ],
    "enthalpy_calibrant_used_for_srm_measurements": {
      "material": "PTB indium",
      "serial_number": "KM-31402",
      "reference_enthalpy_J_per_g": "28.64",
      "expanded_uncertainty_J_per_g": "0.06",
      "coverage_factor": "2",
      "standard_uncertainty_J_per_g": "0.03",
      "rate_specific_Kq": {
        "1": "1.05517",
        "3": "1.05331",
        "5": "1.05531",
        "10": "1.05941"
      },
      "source_table": "2",
      "source_pdf_page": 16
    },
    "calibration_reference_certification_methods": "Publication reports fixed-point comparison for temperature and modified Tian-Calvet calorimetry for enthalpy.",
    "conditions": "Hermetically sealed aluminum pans; dry nitrogen purge at 50 mL/min; initial premelt at 20 C/min; rate-specific calibration coefficients.",
    "mass_method": "Microbalance, double-substitution ABBA scheme; reported standard mass uncertainty 0.003 mg."
  },
  "uncertainty_evidence": {
    "source_pdf_pages": [
      24,
      29,
      30,
      31,
      32,
      33,
      34,
      35,
      53
    ],
    "row_semantics": "Table A.2.1 footnote c calls u(Hmeas) combined standard uncertainty. Section4.2 specifies combined STANDARD uncertainty attributable to SYSTEMATIC effects only. It is not a total independent per-row error bar.",
    "row_coverage_factor": null,
    "row_uncertainty_kind": "combined_standard_due_to_systematic_effects",
    "calculation": "Equation4 propagates integrated-peak-area and mass uncertainty. Peak-area uncertainty uses 10000 simulated heat-flow/baseline perturbations; calibration reference uncertainty and rate-dependent calibration effects enter the budget.",
    "reported_model_systematic_inputs_J_per_g": {
      "1": "0.091",
      "3": "0.074",
      "5": "0.106",
      "10": "0.187"
    },
    "reported_model_input_source_pdf_page": 30,
    "rounding_limit": "Appendix uncertainties are displayed to 0.01 J/g; source model inputs have 0.001 J/g precision. Means of displayed row tokens need not reproduce unrounded model inputs.",
    "correlations": "The same subsamples recur across rates. Equation1 includes ingot and subsample effects, Student-t(4) random measurement error, and rate-specific systematic terms.",
    "forbidden_shortcut": "Do not divide systematic uncertainty by sqrt(180), treat rows as 180 independent samples, or use inverse-variance averaging as a reproduction of the certification model."
  },
  "reference_limit": {
    "independent_held_out_enthalpy_reference": "not_established",
    "certificate_value_J_per_g": "28.58",
    "certificate_expanded_uncertainty_J_per_g": "0.17",
    "certificate_coverage_factor": "1.93",
    "certificate_source_pdf_pages": [
      9,
      25
    ],
    "reason": "The certificate value is the zero-rate intercept estimated by the hierarchical Bayesian model fitted to these measurements; comparing these data to it is not independent validation.",
    "additional_calibration_checks": "Three fresh PTB indium check samples use the same reference material as calibration, and their differences inform the systematic calibration budget. Baseline verification also reuses a calibration sample; neither supplies an untouched reference for this dataset."
  },
  "descriptive_checks_from_displayed_tokens_only": [
    {
      "nominal_heating_rate_C_per_min": "1",
      "record_count": 45,
      "sample_count": 15,
      "arithmetic_mean_of_displayed_enthalpy_J_per_g": "28.581777777777777777777777777777777777777777777778",
      "minimum_displayed_enthalpy_J_per_g": "27.58",
      "maximum_displayed_enthalpy_J_per_g": "29.00",
      "arithmetic_mean_of_displayed_systematic_standard_uncertainty_J_per_g": "0.09",
      "systematic_standard_uncertainty_token_counts": {
        "0.09": 45
      }
    },
    {
      "nominal_heating_rate_C_per_min": "3",
      "record_count": 45,
      "sample_count": 15,
      "arithmetic_mean_of_displayed_enthalpy_J_per_g": "28.652444444444444444444444444444444444444444444444",
      "minimum_displayed_enthalpy_J_per_g": "28.30",
      "maximum_displayed_enthalpy_J_per_g": "28.92",
      "arithmetic_mean_of_displayed_systematic_standard_uncertainty_J_per_g": "0.070222222222222222222222222222222222222222222222222",
      "systematic_standard_uncertainty_token_counts": {
        "0.07": 44,
        "0.08": 1
      }
    },
    {
      "nominal_heating_rate_C_per_min": "5",
      "record_count": 45,
      "sample_count": 15,
      "arithmetic_mean_of_displayed_enthalpy_J_per_g": "28.705333333333333333333333333333333333333333333333",
      "minimum_displayed_enthalpy_J_per_g": "28.14",
      "maximum_displayed_enthalpy_J_per_g": "28.97",
      "arithmetic_mean_of_displayed_systematic_standard_uncertainty_J_per_g": "0.10555555555555555555555555555555555555555555555556",
      "systematic_standard_uncertainty_token_counts": {
        "0.10": 20,
        "0.11": 25
      }
    },
    {
      "nominal_heating_rate_C_per_min": "10",
      "record_count": 45,
      "sample_count": 15,
      "arithmetic_mean_of_displayed_enthalpy_J_per_g": "28.815777777777777777777777777777777777777777777778",
      "minimum_displayed_enthalpy_J_per_g": "28.39",
      "maximum_displayed_enthalpy_J_per_g": "29.12",
      "arithmetic_mean_of_displayed_systematic_standard_uncertainty_J_per_g": "0.18955555555555555555555555555555555555555555555556",
      "systematic_standard_uncertainty_token_counts": {
        "0.18": 2,
        "0.19": 43
      }
    }
  ],
  "source_apparent_anomalies_and_dispositions": [
    {
      "item": "Sample D at nominal 1 C/min has 27.58 J/g, much lower than its other replicates.",
      "source_pdf_pages": [
        24,
        47
      ],
      "disposition": "Retained. The paper explicitly discusses an outlying sample-D point and models random error with a Student-t distribution."
    },
    {
      "item": "B3 at nominal 3 C/min, second table replicate, has u=0.08 rather than the common 0.07.",
      "source_pdf_pages": [
        49
      ],
      "disposition": "Visually confirmed and retained."
    },
    {
      "item": "Displayed uncertainty varies between 0.10 and 0.11 at 5 C/min and 0.18 and 0.19 at 10 C/min.",
      "source_pdf_pages": [
        50,
        51,
        52,
        53
      ],
      "disposition": "Retained. Do not replace individual tokens with a rate-wide constant."
    },
    {
      "item": "Measured rates differ slightly from nominal rates, including 2.998 and 9.997 C/min.",
      "source_pdf_pages": [
        49,
        51,
        52,
        53
      ],
      "disposition": "Both measured and nominal rates retained; never silently round measured rates to nominal."
    }
  ],
  "page_verification": [
    {
      "source_pdf_page": 47,
      "source_printed_page": 39,
      "expected_rows": 28,
      "extracted_rows": 28,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "D2 7.296 1.000 28.68 0.09",
        "D 7.574 0.999 27.58 0.09",
        "B2 8.233 0.999 28.65 0.09"
      ]
    },
    {
      "source_pdf_page": 48,
      "source_printed_page": 40,
      "expected_rows": 29,
      "extracted_rows": 29,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "B2 8.233 0.999 28.62 0.09",
        "C 13.663 0.999 28.73 0.09",
        "A2 8.078 2.999 28.76 0.07"
      ]
    },
    {
      "source_pdf_page": 49,
      "source_printed_page": 41,
      "expected_rows": 30,
      "extracted_rows": 30,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "A1 8.118 2.999 28.67 0.07",
        "B3 8.224 2.998 28.61 0.08",
        "G 13.258 2.999 28.81 0.07"
      ]
    },
    {
      "source_pdf_page": 50,
      "source_printed_page": 42,
      "expected_rows": 29,
      "extracted_rows": 29,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "C 13.663 2.999 28.75 0.07",
        "D2 7.296 4.998 28.76 0.10",
        "B3 8.224 4.998 28.78 0.10"
      ]
    },
    {
      "source_pdf_page": 51,
      "source_printed_page": 43,
      "expected_rows": 29,
      "extracted_rows": 29,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "B3 8.224 4.998 28.82 0.10",
        "D2 7.296 9.999 28.81 0.19",
        "A2 8.078 9.998 28.79 0.19"
      ]
    },
    {
      "source_pdf_page": 52,
      "source_printed_page": 44,
      "expected_rows": 30,
      "extracted_rows": 30,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "A2 8.078 9.998 28.87 0.19",
        "G2 13.185 9.998 28.62 0.18",
        "G 13.258 9.997 28.88 0.19"
      ]
    },
    {
      "source_pdf_page": 53,
      "source_printed_page": 45,
      "expected_rows": 5,
      "extracted_rows": 5,
      "pypdf_equals_pdfplumber": true,
      "visual_review": "Rendered page layout, headings, and listed numeric anchors inspected",
      "visual_anchors": [
        "G 13.258 9.998 28.61 0.19",
        "C 13.663 9.998 28.93 0.19"
      ]
    }
  ],
  "exclusions": [],
  "scope_limit": "Complete published processed DSC results, not raw traces, not an independent validation campaign, not polymer data, and not present-day local instrument qualification. Release unchanged."
}''')


def curated_rows():
    result = []
    lines = iter(PUBLISHED_TABLE.splitlines())
    groups = Counter()
    for page, count in PAGE_COUNTS.items():
        for row_index in range(1, count + 1):
            line = next(lines)
            sample, mass, measured_rate, enthalpy, uncertainty = line.split()
            rate = min((1, 3, 5, 10), key=lambda n: abs(Decimal(measured_rate) - n))
            groups[(sample, rate)] += 1
            rep = groups[(sample, rate)]
            result.append(dict(
                id=f"NIST-SP260-242-{sample}-rate{rate}-rep{rep}",
                lot_id="135139138114-400", ingot_id=sample[0], sample_id=sample,
                mass_mg=mass, nominal_heating_rate_C_per_min=str(rate),
                measured_heating_rate_C_per_min=measured_rate,
                replicate_index_in_table=rep, enthalpy_J_per_g=enthalpy,
                systematic_standard_uncertainty_J_per_g=uncertainty,
                source_pdf_page=page, source_printed_page=page-8,
                source_table="A.2.1", source_row_on_page=row_index, source_row_text=line))
    assert next(lines, None) is None
    return result


class DSCCampaign:
    def __init__(self, root):
        root.mkdir()
        self.root = root
        self.path = root / "campaign.json"
        self.rows = curated_rows()
        self.metadata = deepcopy(PUBLISHED_METADATA)
        self.m = dict(schema=SCHEMA, campaign_id="DSC adapter software regression",
                      source=self.write("source.pdf", b"SYNTHETIC source bytes for I/O tests; not a real PDF or measurement evidence."),
                      source_url=SOURCE_URL, records=None, metadata=None)
        self.save()

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return dict(file=name, sha256=digest(raw))

    def save(self, refresh=True):
        if refresh:
            self.m["records"] = self.write("records.json", dump(self.rows))
            self.metadata["source_sha256"] = self.m["source"]["sha256"].removeprefix("sha256:")
            self.metadata["records_sha256"] = self.m["records"]["sha256"].removeprefix("sha256:")
            self.m["metadata"] = self.write("metadata.json", dump(self.metadata))
        self.path.write_bytes(dump(self.m))
        return self.path

    def evaluate(self, refresh=True):
        return PublishedDSC(self.save(refresh)).evaluate()


@pytest.fixture
def campaign(tmp_path):
    return DSCCampaign(tmp_path / "campaign")


def sync_text(row):
    row["source_row_text"] = " ".join(row[k] for k in (
        "sample_id", "mass_mg", "measured_heating_rate_C_per_min",
        "enthalpy_J_per_g", "systematic_standard_uncertainty_J_per_g"))


def test_complete_real_table_topology_and_rate_counts(campaign):
    report, normalized, metadata = campaign.evaluate()
    assert report["counts"] == dict(measurement_rows=180, material_lots=1, ingots=7,
        samples=15, sample_rate_groups=60, nominal_heating_rates=4)
    assert len(normalized["records"]) == 180
    assert [r["measurement_rows"] for r in report["by_heating_rate"]] == [45]*4
    assert [r["samples"] for r in report["by_heating_rate"]] == [15]*4
    assert Counter(r["ingot_id"] for r in normalized["records"]) == dict(A=60, B=36, C=12, D=24, E=12, F=12, G=24)
    assert metadata["uncertainty_evidence"]["row_uncertainty_kind"] == "combined_standard_due_to_systematic_effects"


def test_real_table_keeps_extreme_values_and_varying_uncertainties(campaign):
    report, normalized, _ = campaign.evaluate()
    rows = normalized["records"]
    assert min(Decimal(r["enthalpy_J_per_g"]) for r in rows) == Decimal("27.58")
    assert max(Decimal(r["enthalpy_J_per_g"]) for r in rows) == Decimal("29.12")
    special = next(r for r in rows if r["id"] == "NIST-SP260-242-B3-rate3-rep2")
    assert special["systematic_standard_uncertainty_J_per_g"] == "0.08"
    assert special["measured_heating_rate_C_per_min"] == "2.998"
    assert [r["descriptive_mean_enthalpy_J_per_g"] for r in report["by_heating_rate"]] == [
        "28.5817777778", "28.6524444444", "28.7053333333", "28.8157777778"]


def test_exact_unit_scaling_retains_source_literals_and_systematic_semantics(campaign):
    _, normalized, _ = campaign.evaluate()
    for source, result in zip(campaign.rows, normalized["records"]):
        assert all(result[key] == value for key, value in source.items())
        assert Decimal(result["mass_kg"]) == Decimal(source["mass_mg"]) * Decimal("0.000001")
        assert Decimal(result["enthalpy_J_per_kg"]) == Decimal(source["enthalpy_J_per_g"]) * 1000
        assert Decimal(result["systematic_standard_uncertainty_J_per_kg"]) == Decimal(source["systematic_standard_uncertainty_J_per_g"]) * 1000
        assert result["uncertainty_scope"] == "systematic_effects_only_not_total_per_measurement"
        assert result["replicate_semantics"] == "ordinal_in_published_table_not_acquisition_order"
    # This measured rate converts exactly, rather than rounding it to nominal 1 C/min.
    result = normalized["records"][1]
    assert result["measured_heating_rate_C_per_min"] == "0.999"
    assert Decimal(result["measured_heating_rate_K_per_s"]) == Decimal("0.01665")


def test_actual_source_rounding_intervals_overlap_without_forcing_equal_means(campaign):
    report, _, _ = campaign.evaluate()
    assert report["source_precision_check"] == "compatible"
    for rate in report["by_heating_rate"]:
        assert rate["published_systematic_u_compatible_with_display_rounding"] is True
        assert rate["mean_systematic_standard_uncertainty_J_per_g"] != rate["published_model_systematic_u_input_J_per_g"]
        assert Decimal(rate["mean_systematic_u_rounding_half_width_J_per_g"]) == Decimal("0.005")
        assert rate["uncertainty_of_mean"] == "not_computed_shared_effects_and_random_model_required"


@pytest.mark.parametrize("token,half_width", [("0.09", "0.005"), ("0.090", "0.0005"), ("1", "0.5")])
def test_rounding_interval_uses_displayed_precision(token, half_width):
    assert half_last_place(token) == Decimal(half_width)


def test_uncertainty_disagreement_is_reported_as_review_not_success(campaign):
    for row in campaign.rows:
        if row["nominal_heating_rate_C_per_min"] == "1":
            row["systematic_standard_uncertainty_J_per_g"] = "0.50"
            sync_text(row)
    report, _, _ = campaign.evaluate()
    assert report["source_precision_check"] == "requires_review"
    assert report["by_heating_rate"][0]["published_systematic_u_compatible_with_display_rounding"] is False


def test_no_certificate_reproduction_independence_or_local_qualification_claim(campaign):
    report, _, _ = campaign.evaluate()
    assert report["release_status"] == "unchanged"
    assert report["local_instrument_qualification"] == "not_established"
    assert report["certified_value_reproduction"].startswith("not_claimed")
    assert report["independent_held_out_reference"].startswith("not_established")
    assert "not refitted here" in report["published_model"]
    assert not any("uncertainty_of_mean" in r and isinstance(r["uncertainty_of_mean"], (int, float))
                   for r in report["by_heating_rate"])


@pytest.mark.parametrize("kind", ["source", "records", "metadata"])
def test_tampered_evidence_bytes_are_rejected(campaign, kind):
    (campaign.root / campaign.m[kind]["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.evaluate(refresh=False)


@pytest.mark.parametrize("kind", ["source", "records", "metadata"])
def test_evidence_path_escape_is_rejected(campaign, kind):
    outside = campaign.root.parent / f"{kind}-outside.bin"
    outside.write_bytes((campaign.root / campaign.m[kind]["file"]).read_bytes())
    campaign.m[kind]["file"] = "../" + outside.name
    with pytest.raises(ValueError, match="inside its campaign"):
        campaign.evaluate(refresh=False)


@pytest.mark.parametrize("change", ["missing", "additional", "duplicate"])
def test_complete_table_requires_exact_row_count_and_unique_ids(campaign, change):
    if change == "missing":
        campaign.rows.pop()
    elif change == "additional":
        campaign.rows.append(deepcopy(campaign.rows[0]))
    else:
        campaign.rows[1]["id"] = campaign.rows[0]["id"]
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("field,value", [
    ("lot_id", "another-lot"), ("ingot_id", "A"), ("sample_id", "D3"),
    ("replicate_index_in_table", 2), ("nominal_heating_rate_C_per_min", "2"),
    ("mass_mg", "7.297")])
def test_topology_rate_replicate_and_mass_inconsistencies_rejected(campaign, field, value):
    campaign.rows[0][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("field,value", [
    ("source_pdf_page", 46), ("source_pdf_page", True),
    ("source_printed_page", 40), ("source_row_on_page", 0), ("source_table", "A.2.2"),
    ("replicate_index_in_table", True)])
def test_invalid_source_coordinates_and_integer_tokens_rejected(campaign, field, value):
    campaign.rows[0][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("field,value", [
    ("mass_mg", "0"), ("enthalpy_J_per_g", "-1"),
    ("systematic_standard_uncertainty_J_per_g", "0"),
    ("measured_heating_rate_C_per_min", "0"),
    ("enthalpy_J_per_g", True), ("enthalpy_J_per_g", 28.68),
    ("enthalpy_J_per_g", "NaN"), ("enthalpy_J_per_g", "Infinity"),
    ("enthalpy_J_per_g", "1e999"), ("enthalpy_J_per_g", " 28.68")])
def test_invalid_decimal_values_rejected(campaign, field, value):
    campaign.rows[0][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("extra", [True, False])
def test_row_schema_is_exact(campaign, extra):
    if extra:
        campaign.rows[0]["total_uncertainty_J_per_g"] = "0.09"
    else:
        del campaign.rows[0]["systematic_standard_uncertainty_J_per_g"]
    with pytest.raises(ValueError, match="fields"):
        campaign.evaluate()


def test_only_the_documented_source_url_is_accepted(campaign):
    campaign.m["source_url"] = "https://example.invalid/a-different-paper"
    with pytest.raises(ValueError, match="scoped"):
        campaign.evaluate()


def test_outputs_retain_exact_records_and_refuse_overwrite(campaign, tmp_path):
    campaign.save()
    raw_records = (campaign.root / "records.json").read_bytes()
    raw_manifest = campaign.path.read_bytes()
    target = tmp_path / "output"
    report = adapt_campaign(campaign.path, target)
    assert (target / "campaign.json").read_bytes() == raw_manifest
    assert (target / "source-records.json").read_bytes() == raw_records
    assert report["records_sha256"] == digest(raw_records)
    assert load((target / "study-metadata.json").read_bytes()) == campaign.metadata
    assert len(load((target / "normalized-records.json").read_bytes())["records"]) == 180
    original_report = (target / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        adapt_campaign(campaign.path, target)
    assert (target / "report.json").read_bytes() == original_report


def test_optional_published_pdf_integration(campaign):
    source_path = os.environ.get("CIW_NIST_DSC_SOURCE_PDF")
    if not source_path:
        pytest.skip("Set CIW_NIST_DSC_SOURCE_PDF to the retained NIST publication for integration")
    raw = Path(source_path).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PUBLISHED_PDF_SHA256
    campaign.m["source"] = campaign.write("source.pdf", raw)
    report, _, _ = campaign.evaluate()
    assert report["source_sha256"] == "sha256:" + PUBLISHED_PDF_SHA256
    assert report["counts"]["measurement_rows"] == 180


@pytest.mark.parametrize("field,value", [
    ("schema", "unrelated.schema"),
    ("source_url", "https://example.invalid/source"),
    ("source_sha256", "0" * 64),
    ("records_sha256", "0" * 64),
])
def test_metadata_must_match_study_and_evidence_identity(campaign, field, value):
    campaign.metadata[field] = value
    campaign.m["metadata"] = campaign.write("metadata.json", dump(campaign.metadata))
    with pytest.raises(ValueError):
        campaign.evaluate(refresh=False)


@pytest.mark.parametrize("field,value", [
    ("row_uncertainty_kind", "total_per_measurement"),
    ("row_coverage_factor", "2"),
    ("row_coverage_factor", 1),
])
def test_metadata_cannot_relabel_systematic_standard_uncertainty(campaign, field, value):
    campaign.metadata["uncertainty_evidence"][field] = value
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_constant_but_wrong_sample_mass_is_rejected(campaign):
    for row in campaign.rows:
        if row["sample_id"] == "D2":
            row["mass_mg"] = "7.297"
            sync_text(row)
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_single_wrong_lot_is_rejected_even_with_consistent_lineage(campaign):
    for row in campaign.rows:
        row["lot_id"] = "wrong-single-lot"
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_renamed_sample_cannot_preserve_counts_and_pass_as_known_study(campaign):
    for row in campaign.rows:
        if row["sample_id"] == "D2":
            row["sample_id"] = "D3"
            sync_text(row)
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_duplicate_source_coordinates_are_rejected(campaign):
    campaign.rows[1]["source_row_on_page"] = campaign.rows[0]["source_row_on_page"]
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("index,wrong_row", [(0, 29), (-1, 6)])
def test_source_row_number_must_fit_actual_page(campaign, index, wrong_row):
    campaign.rows[index]["source_row_on_page"] = wrong_row
    with pytest.raises(ValueError):
        campaign.evaluate()


@pytest.mark.parametrize("measured,valid", [("0.996", False), ("0.997", True),
                                           ("1.003", True), ("1.004", False)])
def test_measured_rate_must_remain_within_documented_nominal_group(campaign, measured, valid):
    campaign.rows[0]["measured_heating_rate_C_per_min"] = measured
    sync_text(campaign.rows[0])
    if valid:
        _, normalized, _ = campaign.evaluate()
        assert normalized["records"][0]["measured_heating_rate_C_per_min"] == measured
    else:
        with pytest.raises(ValueError):
            campaign.evaluate()


def test_raw_row_text_cannot_contradict_extracted_values(campaign):
    campaign.rows[0]["source_row_text"] = campaign.rows[0]["source_row_text"].replace("28.68", "29.68")
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_replicate_ordinals_must_match_source_table_order(campaign):
    campaign.rows[0]["replicate_index_in_table"], campaign.rows[1]["replicate_index_in_table"] = 2, 1
    with pytest.raises(ValueError):
        campaign.evaluate()


def test_input_list_order_does_not_change_published_replicate_identity(campaign):
    campaign.rows.reverse()
    report, normalized, _ = campaign.evaluate()
    assert report["counts"]["measurement_rows"] == 180
    first_source = next(r for r in normalized["records"] if r["source_pdf_page"] == 47 and r["source_row_on_page"] == 1)
    assert first_source["sample_id"] == "D2"
    assert first_source["replicate_index_in_table"] == 1
