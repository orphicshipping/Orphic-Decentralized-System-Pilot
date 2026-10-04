# Public measured thermal data candidate

A bounded search on 2026-10-04 located actual author-published recordings from
Richardson and Howey's battery temperature experiment. The data are useful for
research; they do not satisfy NET's calibrated four-stream pilot contract.
The [machine-readable assessment](data/public-thermal-candidate.json) records
channel mappings, provenance, alternatives and the remaining evidence.

## Source and reproducible retrieval

- [Author repository at the selected revision](https://github.com/robert-richardson/EKF-Battery-Impedance-Temperature/tree/194919b94b45f66309475bc88aa8e8826eb603bb).
- [Open paper](https://arxiv.org/abs/1501.06160) and [publication DOI](https://doi.org/10.1109/TSTE.2015.2420375).
- Original temperature, electrical and impedance MAT files, the interpreting
  MATLAB source, README and BSD-3-Clause licence are fetched by exact revision
  and checked against retained SHA256 values. The README describes a code
  licence; no separate dataset licence statement was located.

In a separate research environment with NumPy 2.3.5 and SciPy 1.16.3:

```sh
python scripts/inspect_public_thermal_data.py --output-dir research-data --fetch
```

The script only downloads original bytes and inventories numeric arrays. It
executes none of the downloaded MATLAB code and creates no NET measured source.
Existing files must match the pinned hashes; they are never silently replaced.
NumPy/SciPy versions are retained in the inventory. These research dependencies
are not new NET runtime requirements.

## Available channels and missing evidence

`Temperature_data.mat` contains 3,222 rows in `T_data_2`, spaced 1.1 seconds
apart. MATLAB columns 2, 3 and 4 are surface, core and coolant/environment
temperatures in degrees Celsius. Column 5 is undocumented and remains unused.
Electrical current and voltage use irregular timestamps.

There is no independently measured heat-power stream. The author's total-heat
calculation includes a reversible term using the measured core temperature.
Feeding that quantity into NET would leak the withheld reference into inference.
A future separately declared electrical heat estimate must exclude that channel,
retain its assumptions and uncertainty, and be identified as derived power.

The downloaded material does not contain the thermocouple calibration
certificates/application receipts, device identities, acquisition epoch or
calibrated covariances required for the intended pilot. File-creation timestamps
are not acquisition times. The published radial thermal model also does not
directly supply NET's two-capacity parameters.

Any adaptation must reserve cycle 1 for development and cycle 2 for assessment,
justify model reduction and clock alignment, retain unit transformations, and
declare acceptance criteria before evaluating the held-out data. No such
adaptation or physical qualification is claimed here. Missing calibration,
power and model facts must not be filled with synthetic declarations.

The assessed alternatives include NIST thermal-abuse surface measurements,
Zenodo pouch-cell surface imaging, TCLab teaching recordings, and a thermal
test-cell publication with promising apparatus but no located raw recordings.
Their specific limitations and source links are in the assessment.
