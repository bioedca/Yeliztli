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
| **FHIR R4** | DiagnosticReport Bundle (JSON) in the FHIR R4 genomics-reporting format, for interoperability with research/genomics tooling — **not a clinical diagnostic report** (see below) |

Exports reflect whatever filters or query you have applied, so you can export a focused
subset rather than your whole genome.

!!! warning "The FHIR export is not a clinical diagnostic report"
    The **FHIR R4** export produces a `DiagnosticReport` resource using the standard
    genomics-reporting format, purely for interoperability with research/genomics
    tooling. It is **research/educational, array-derived, and not clinically validated** —
    it is **not** a clinical diagnostic report and must **not** be filed as a clinical
    result or used to drive clinical decisions. To make this unambiguous to any receiving
    system, the bundle is marked `status: "preliminary"` and carries the research-use
    caveat in the `DiagnosticReport.conclusion` field. Confirm any finding with an
    accredited clinical laboratory.
