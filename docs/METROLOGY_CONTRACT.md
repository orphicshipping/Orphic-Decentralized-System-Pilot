# Additive sampled inspection contract

`ciw.metrology-case.v1` binds exact inspection source bytes, a selected profile,
feature/check policy and revisions, capture/frame/metre units, calibration and its
evidence digest, uncertainty model and fixed numerical-provider revision. Existing
BIM and observation schemas are unchanged. Profile declarations cover only sampled
planes, frame-aligned freight envelopes and visible straight axes; they are not
registration of executable operations or general GD&T support.

Cases are bounded at 48 KiB and 3–256 finite xyz coordinates within ±10,000 m.
Null points preserve missingness. Coverage, calibration applicability, motion/blur,
scale correction, bounded residual/shared translation/angular uncertainty and
origin declarations are explicit. Freight requires a versioned carrier rule;
lean requires gravity/frame and camera-calibration bindings. Contradictory
capture/frame/unit bindings, unsupported geometry and unknown fields refuse.
Optional semantic ISO 1101:2017 perpendicularity retains feature/datum association
only; no normative calculation or standard conformance is implemented here.

`metrology_ingress.retain` creates a content-addressed file in a host-controlled
root. `resolve` accepts only opaque digests explicitly authorized by the host,
checks exact bytes and limits, and refuses URL/path handles and symlinks. Root
parents must remain host-controlled. No reference fetches a file or a URL. These
primitives are host APIs, not an unauthenticated upload endpoint.

All included cases are synthetic. Declared calibration, coverage, provider
revision or `recorded` origin do not authenticate acquisition or grant physical,
approval or actuation authority. Numerical operations and retained-result status
handling are additive follow-up implementations.

```sh
python -m pytest -q tests/test_metrology_contract.py
python -m pip wheel --no-deps . -w dist
```
