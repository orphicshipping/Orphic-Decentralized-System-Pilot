# Temperature pilot: product-conformity assessment

Status: unassessed planning record. The implemented temperature MCP profile
processes declared observations; the product offered to a customer, hardware,
deployment arrangement and target markets have not been specified. This guide
and [assessment template](../examples/temperature/conformity-assessment.json)
organize the evidence needed to decide which product obligations apply. They
establish no conformity, certification or permission to affix a mark.

This is a companion to the [measurement qualification protocol](TEMPERATURE_QUALIFICATION.md).
The template is a manually reviewed project record, not an executable request,
MCP operation, legal rules engine or software release interlock. A temperature
report's `conditional_pass` concerns its declared numerical acceptance rule;
it cannot approve a product for sale or establish an accredited calibration.

## Define the supplied product

Make a copy of the template for each materially different supplied configuration.
Record the responsible manufacturer and other economic operators, intended use,
users and environment, delivery form, countries and planned supply dates.
Distinguish report-processing software, a hosted service, a sensor/readout kit,
an installed system, and equipment that controls a heater or safety function.
An internal experiment and a supplied pilot can require different assessments;
the label "pilot" alone is not an exemption.

Bind the record to the actual bill of materials, hardware revisions, sensors,
readout, power supplies, enclosure, cables, radio modules, firmware, software
release, integrations and installation instructions. Record networking,
actuation, alarms and safety-related uses explicitly. The repository's current
processing scope does not determine the scope of a customer's combined system.
Component certificates and module approvals need review against the finished
configuration and their conditions of use.

`null` and empty evidence lists mean information has not been supplied. They do
not mean an obligation is inapplicable or satisfied. Select target markets
explicitly; the topics below are screening prompts, not a list of markets in
which this project is ready to sell.

## Record applicability and the assessment route

Guidance reviewed on 2026-10-04. For each selected market, create a separate
requirement entry for each relevant legal instrument or certification scheme.
Record the current legal text, territorial scope, effective and transition dates,
product facts, exclusions, reasoning, assessment route and responsible reviewer.
Use `unassessed`, `applicable` or `not_applicable` for applicability. Both a
positive decision and an exclusion require a basis; unknown is not an exclusion.
Keep applicability separate from evidence status (`not_reviewed`, `gaps_open`,
`reviewed_satisfactory` or `reviewed_unsatisfactory`).

| Screening topic | What to resolve for this pilot |
| --- | --- |
| EU/EEA CE scope | Identify legislation covering the supplied product. CE is required only where the relevant legislation requires it and must not be applied otherwise. The prescribed assessment route determines whether a notified body is involved; the manufacturer retains responsibility. [EU guidance](https://europa.eu/youreurope/business/product-rules-compliance/general-product-compliance/ce-marking/index_en.htm) |
| EU electrical, radio and substances rules | Screen EMC, Low Voltage, Radio Equipment and RoHS scope separately against the hardware and integration. Record apparatus/fixed-installation distinctions, rated supply, radio functions, exclusions and interactions between instruments; do not automatically add every regime to every sensor. [EMC](https://single-market-economy.ec.europa.eu/sectors/electrical-and-electronic-engineering-industries-eei/electromagnetic-compatibility-emc-directive_en), [LVD](https://single-market-economy.ec.europa.eu/sectors/electrical-and-electronic-engineering-industries-eei/low-voltage-directive-lvd_en), [RED](https://single-market-economy.ec.europa.eu/sectors/electrical-and-electronic-engineering-industries-eei/radio-equipment-directive-red_en), [RoHS](https://environment.ec.europa.eu/topics/waste-and-recycling/rohs-directive_en) |
| EU machinery and safety integration | If the supplied configuration includes machinery, control or safety functions, classify that configuration and its responsibilities. Record the transition to Regulation (EU) 2023/1230, generally applicable from 20 January 2027. [Commission machinery guidance](https://single-market-economy.ec.europa.eu/sectors/mechanical-engineering/machinery_en) |
| EU connected software and hardware | Screen the Cyber Resilience Act against the actual distributed product, connectivity, commercial context and exclusions. Software delivery alone is not a sufficient exclusion. CRA reporting obligations apply from 11 September 2026; the main requirements apply from 11 December 2027. Record obligations by applicable date. [Commission CRA guidance](https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act) |
| EU legal metrology | Determine whether the intended use and instrument category fall within applicable legal-metrology rules. Measuring temperature alone does not establish coverage by the Measuring Instruments Directive. [Commission legal-metrology guidance](https://single-market-economy.ec.europa.eu/single-market/goods/building-blocks/legal-metrology/measuring-weighing-instruments-eu_en) |
| Great Britain | Identify the product regime and accepted conformity route. CE continues to be recognized for many product categories; UKCA is not a universal mandatory replacement. Check sector-specific arrangements for the supply date. [GB guidance](https://www.gov.uk/guidance/placing-ukca-or-ce-marked-products-on-the-market-in-great-britain) |
| Northern Ireland | Assess separately from Great Britain, including CE and any UKNI conditions arising from the assessment body used. [NI guidance](https://www.gov.uk/guidance/placing-ce-or-ce-and-ukni-marked-products-on-the-market-in-northern-ireland) |
| United States: radio/emissions | Determine applicable FCC equipment-authorization requirements and whether certification or Supplier's Declaration of Conformity applies. An FCC logo is not a universal mandatory mark; it is voluntary for equipment subject to SDoC. Record authorization, labeling and user-information requirements separately. [FCC equipment authorization](https://docs.fcc.gov/public/attachments/DA-24-415A1.pdf), [47 CFR 2.1074](https://www.govinfo.gov/content/pkg/CFR-2025-title47-vol1/pdf/CFR-2025-title47-vol1-sec2-1074.pdf) |
| United States: workplace electrical safety | Identify any requirement for approval by an OSHA-recognized NRTL and the relevant product scope. UL is one certification organization, not a universally required brand. [OSHA NRTL guidance](https://www.osha.gov/nationally-recognized-testing-laboratory-program/frequently-asked-questions) |
| Canada: electrical approval | Select the province/territory and installation jurisdiction, then establish accepted approval routes and marks. CSA is one organization; a recognized component does not automatically approve a finished assembly. The Ontario list is an example, not a Canada-wide determination. [Ontario ESA recognized marks](https://esasafe.com/electrical-products/recognized-certification-marks/) |
| Canada: radio/emissions | Screen ISED radio certification and applicable interference-causing equipment standards separately from electrical approval. Determine the equipment category before selecting an ICES or RSS standard. [ISED standards and certification](https://ised-isde.canada.ca/site/spectrum-management-telecommunications/en/devices-and-equipment) |
| Management-system requirements | Record ISO 9001/14001 or customer contractual requirements separately. Management-system certification is not product approval or evidence of temperature accuracy; ISO does not itself perform certification. [ISO certification guidance](https://www.iso.org/certification.html) |

This is an initial screening list. Add other obligations indicated by the actual
product, intended use and selected markets. Preserve the reviewed source version
or retrieval date and the basis for each decision. Do not treat this table as a
completed legal classification or assume its dated summaries remain current.

## Assemble the evidence for the selected configuration

Maintain a standards register with identifier, edition/amendments, national or
regional adoption, applicable clauses, and the legal or contractual role being
claimed. A useful engineering standard is not automatically an EU harmonized
standard or a GB designated standard. Verify the relevant published listing,
scope, restrictions and transition dates when relying on it for a presumption
of conformity. [EU harmonized standards](https://single-market-economy.ec.europa.eu/single-market/goods/european-standards/harmonised-standards_en).

Each requirement entry should link to reviewed evidence and identify unresolved
gaps. Use the template's entry shapes to record:

- Product definition, configuration and intended-use assessment, including
  thermal, electrical, mechanical and control hazards where relevant.
- Applicable requirements and the chosen assessment procedure, including the
  notified, approved or certification body's identity and relevant scope when
  that route calls for one.
- Test plans and reports for the supplied configuration, methods, conditions,
  deviations, results, reviewer and any limits on applicability. Retain the link
  between a tested sample and the production or installed configuration.
- Technical documentation, instructions, installation restrictions, required
  declarations, markings and other regulatory records for the chosen market.
- Production/configuration controls, support responsibilities, retention periods
  and change, complaint, incident and corrective-action procedures as required.

Keep evidence identity and evidence sufficiency separate. A stored document or
digest identifies a record; its issuer, scope, result and applicability still
need review. Missing evidence or an unresolved requirement leaves the relevant
decision open. A software test result or future SP1 execution proof can support
its stated computational claim only.

## Keep calibration and product approval distinct

For external calibration, review the laboratory's ISO/IEC 17025 accreditation
scope, method, temperature range and relevant uncertainty capability, together
with the delivered calibration results and their uncertainty. Accreditation is
not a universal legal requirement for every calibration; identify any customer
or regulatory requirement that makes it necessary for this pilot. A generic
accreditation logo or ISO 9001 certificate does not establish the required scope.
[ISO/IEC 17025](https://www.iso.org/standard/66912.html).

Retain the documented calibration chain and uncertainty contributions required
by the [qualification protocol](TEMPERATURE_QUALIFICATION.md). Traceability is
a property of a measurement result supported by that chain, not a consequence
of CE marking, a certificate hash or a successful processing run. The installed
probe's lag, placement and thermal interaction still need experimental evidence.
[NIST traceability guidance](https://www.nist.gov/metrology/metrological-traceability).

## Review, release and reassessment

For each selected market and frozen product configuration, record the reviewed
applicability entries, evidence decision, remaining restrictions and responsible
sign-off. A completed inventory is not a satisfactory evidence review. Preserve
an explicit manual release decision and the scope of any permitted claims or
markings; do not infer it from a filled template or a processing-report status.
This repository does not enforce that manual decision.

Reopen affected assessments after changes to the product boundary, intended use,
market, hardware, power supply, radio, firmware/software, model, installation,
safety functions, applicable law or standard, or after an incident or failed
check. Identify affected units, configurations, customers and measurement
reports. Record the required corrective action and any withdrawal or replacement
of claims/results, linking replacements to the preserved originals. Coordinate
with metrological requalification and recalibration without treating either as
a substitute for the product-conformity review.
