# Export

--8<-- "health-disclaimer.md"

Export your data in several formats from the variant table or from
[Query Builder](query-builder.md) results:

| Format | Description |
|--------|-------------|
| **VCF 4.2** | Standard variant call format |
| **TSV** | Tab-separated, with all annotation columns |
| **JSON** | Structured JSON with nested annotations |
| **CSV** | Comma-separated, for spreadsheets |
| **FHIR R4** | DiagnosticReport Bundle (JSON) in the FHIR R4 genomics-reporting format, for interoperability with research/genomics tooling — **not a clinical diagnostic report** (see below). Nuclear variant coordinates are exported in the GRCh37/hg19 reference frame; mitochondrial coordinates use rCRS. |

Exports reflect whatever filters or query you have applied, so you can export a focused
subset rather than your whole genome.

## Report Builder size safeguards

Report Builder keeps interactive exports within fixed resource limits:

- HTML preview and PDF generation accept at most **1,000 reportable findings**
  across the selected report modules.
- FHIR export uses its separate, full-sample carried-variant selection and accepts
  at most **1,000 `Observation` resources**. The PDF module checkboxes do not
  change that FHIR selection.

The page disables an action when its selection exceeds the applicable limit.
The API repeats the same check and returns HTTP 413 before rendering or building
resources, so direct requests cannot bypass the guard. FHIR export also stays
disabled when its size cannot be verified or the sample has no annotated variants.

!!! note "FHIR export is currently disabled on a fully annotated sample"
    Because the FHIR selection covers every carried variant in the sample rather
    than the modules you selected, a fully annotated consumer array exceeds the
    1,000-`Observation` limit by a wide margin, and no module selection reduces
    it. **Export FHIR R4 is therefore disabled on such samples**, and a direct
    request returns HTTP 413.

    This is the guard working as designed on a selection that has no way to be
    made smaller. Scoping the FHIR bundle to the selected report modules — which
    is what makes the limit reachable — is tracked separately; until that lands,
    use **Download PDF** with a reduced module selection.

!!! warning "The FHIR export is not a clinical diagnostic report"
    The **FHIR R4** export produces a `DiagnosticReport` resource using the standard
    genomics-reporting format, purely for interoperability with research/genomics
    tooling. It is **research/educational, array-derived, and not clinically validated** —
    it is **not** a clinical diagnostic report and must **not** be filed as a clinical
    result or used to drive clinical decisions. To make this unambiguous to any receiving
    system, the bundle is marked `status: "preliminary"` and carries the research-use
    caveat in the `DiagnosticReport.conclusion` field. Confirm any finding with an
    accredited clinical laboratory.

    Variant `Observation` resources use 1-based genomic coordinates and include a
    LOINC-coded genomic reference-sequence component for the GRCh37/hg19 nuclear
    build or the rCRS mitochondrial reference, so chromosome positions are not
    exported as reference-ambiguous values.
