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
- FHIR export accepts at most **1,000 `Observation` resources**, and honours the
  same module checkboxes as the PDF. Selecting fewer modules reduces the bundle;
  selecting none disables the export, exactly as it disables Preview and
  Download PDF.

The page disables an action when its selection exceeds the applicable limit.
The API repeats the same check and returns HTTP 413 before rendering or building
resources, so direct requests cannot bypass the guard. FHIR export also stays
disabled when its size cannot be verified or the sample has no annotated variants.

!!! note "What a module-scoped FHIR bundle contains"
    The bundle is built from your annotated variants, while the report you curate
    is built from findings. rsID is what links them, so a scoped export contains
    the **carried variants that a finding in a selected module points at**.

    A finding that names no variant — a polygenic score, a haplogroup call —
    contributes no `Observation`, so a selection made only of those produces a
    valid but empty bundle, and the page says so before you download it.

!!! warning "Disclosure gates apply to FHIR export, including unscoped exports"
    A module whose disclosure gate you have not acknowledged is withheld from the
    FHIR bundle exactly as it is from the PDF — and that holds **whether or not
    you scope the export by module**, so requesting an unscoped bundle is not a
    way around it.

    A variant is withheld only when a gated module is its *sole* reason to
    appear. One that a non-gated module's finding also names is exported through
    that module, which is how the PDF path behaves too: it filters findings, not
    variants.

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
