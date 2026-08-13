"""Regression coverage for explicit aggregate response gates (#2019)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from backend.analysis.pharmacogenomics import (
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
)
from backend.api.routes import (
    apoe,
    cancer,
    cardiovascular,
    carrier,
    hemochromatosis,
    kinship,
    metabolic,
    parkinsons,
    rare_variants,
    risk_common,
)


class _Rows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows
        self._cursor = 0
        self.fetchmany_sizes: list[int] = []

    def fetchall(self) -> list[SimpleNamespace]:
        return self._rows

    def fetchmany(self, size: int) -> list[SimpleNamespace]:
        """Serve bounded batches, recording each requested size.

        Added for #2328: the TSV export must consume its result set in bounded
        batches rather than materialising it, and a double that only offers
        ``fetchall`` cannot tell the two apart.
        """
        self.fetchmany_sizes.append(size)
        batch = self._rows[self._cursor : self._cursor + size]
        self._cursor += len(batch)
        return batch

    def __iter__(self) -> Iterator[SimpleNamespace]:
        return iter(self._rows)


class _UnboundedFetchIsFatalRows(_Rows):
    """A result set that refuses to be materialised in one call."""

    def fetchall(self) -> list[SimpleNamespace]:
        raise AssertionError("the export must not call fetchall() on the result set")


class _Connection:
    def __init__(self, rows: list[SimpleNamespace], result_cls: type[_Rows] = _Rows) -> None:
        self._rows = rows
        self._result_cls = result_cls
        self.results: list[_Rows] = []
        self.closed = False

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def execute(self, _statement: object) -> _Rows:
        result = self._result_cls(self._rows)
        self.results.append(result)
        return result


class _Engine:
    def __init__(self, rows: list[SimpleNamespace], result_cls: type[_Rows] = _Rows) -> None:
        self._rows = rows
        self._result_cls = result_cls
        self.connections: list[_Connection] = []

    def connect(self) -> _Connection:
        connection = _Connection(self._rows, self._result_cls)
        self.connections.append(connection)
        return connection


def _finding_row(**overrides: Any) -> SimpleNamespace:
    """Make a complete, individually presentable stored-finding test row."""
    values: dict[str, Any] = {
        "id": 1,
        "module": "test",
        "category": "risk_genotype",
        "rsid": "rs_safe",
        "gene_symbol": "SAFE1",
        "drug": None,
        "genotype": None,
        "zygosity": "het",
        "clinvar_significance": "Pathogenic",
        "conditions": "Safe condition",
        "evidence_level": 1,
        "finding_text": "Safe finding",
        "detail_json": json.dumps({}),
        "provenance": None,
        "pmid_citations": None,
        "phenotype": None,
        "diplotype": None,
        "prs_percentile": None,
    }
    values.update(overrides)
    values["_mapping"] = dict(values)
    return SimpleNamespace(**values)


def _assert_individually_presentable(rows: list[SimpleNamespace]) -> None:
    assert all(is_patient_presentable_finding_payload(row._mapping) for row in rows)


def test_finding_row_helper_preserves_all_scalar_payload_evidence() -> None:
    """A held pair confined to an inspected scalar cannot disappear in a fixture."""
    row = _finding_row(phenotype="CYP2D6 tamoxifen dose guidance")

    assert not is_patient_presentable_finding_payload(row._mapping)


class _CapturedStreamingResponse:
    """Capture a route's iterator without starting Starlette's thread pool."""

    def __init__(self, content: Iterator[str], **_kwargs: object) -> None:
        self.chunks = list(content)
        self.content = "".join(self.chunks)


def test_variant_list_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate safe variant DTOs cannot recreate a held prescribing pair."""
    cancer_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "clinical_caveat": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in cancer_rows)
    monkeypatch.setattr(cancer, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(cancer, "_fetch_cancer_findings", lambda _engine: cancer_rows)

    cancer_response = cancer.list_cancer_variants(sample_id=1)

    assert cancer_response == cancer.CancerVariantsListResponse(items=[], total=0)

    cardiovascular_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "conditions": ["tamoxifen"],
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in cardiovascular_rows)
    monkeypatch.setattr(cardiovascular, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(
        cardiovascular,
        "_fetch_cardiovascular_findings",
        lambda _engine: cardiovascular_rows,
    )

    cardiovascular_response = cardiovascular.list_cardiovascular_variants(sample_id=1)

    assert cardiovascular_response == cardiovascular.CardiovascularVariantsListResponse(
        items=[], total=0
    )

    carrier_rows = [
        {"rsid": "rs1", "gene_symbol": "CYP2D6", "clinvar_significance": "Pathogenic"},
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "clinvar_significance": "Pathogenic",
            "notes": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in carrier_rows)
    monkeypatch.setattr(carrier, "_get_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(carrier, "_fetch_carrier_findings", lambda _engine: carrier_rows)

    carrier_response = carrier.list_carrier_variants(sample_id=1)

    assert carrier_response == carrier.CarrierVariantsListResponse(
        items=[], total=0, genes_with_findings=[]
    )


def test_prs_and_anchor_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final PRS and anchor DTOs are checked after their fields are decoded."""
    prs_rows = [
        _finding_row(id=1, category="prs", detail_json=json.dumps({"trait": "CYP2D6"})),
        _finding_row(id=2, category="prs", detail_json=json.dumps({"trait": "tamoxifen"})),
    ]
    _assert_individually_presentable(prs_rows)
    prs_engine = _Engine(prs_rows)
    monkeypatch.setattr(cancer, "_get_sample_engine", lambda _sample_id: prs_engine)
    monkeypatch.setattr(metabolic, "_get_sample_engine", lambda _sample_id: prs_engine)

    cancer_response = cancer.list_cancer_prs(sample_id=1)
    metabolic_response = metabolic.list_metabolic_prs(sample_id=1)

    assert cancer_response == cancer.CancerPRSListResponse(
        items=[], total=0, sufficient_count=0, insufficient_traits=[]
    )
    assert metabolic_response == metabolic.MetabolicPRSListResponse(items=[], total=0)

    anchor_rows = [
        _finding_row(
            id=3,
            category="anchor_snp",
            detail_json=json.dumps({"gene": "CYP2D6"}),
        ),
        _finding_row(
            id=4,
            category="anchor_snp",
            detail_json=json.dumps({"summary": "tamoxifen"}),
        ),
    ]
    _assert_individually_presentable(anchor_rows)
    monkeypatch.setattr(metabolic, "_get_sample_engine", lambda _sample_id: _Engine(anchor_rows))

    anchors_response = metabolic.list_metabolic_anchors(sample_id=1)

    assert anchors_response == metabolic.MetabolicAnchorListResponse(items=[], total=0)


def test_finding_list_aggregates_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named and generated findings routes use an explicit final response gate."""
    rows = [
        _finding_row(id=1, gene_symbol="CYP2D6", finding_text="Safe finding"),
        _finding_row(id=2, gene_symbol="SAFE1", finding_text="tamoxifen"),
    ]
    _assert_individually_presentable(rows)
    engine = _Engine(rows)

    monkeypatch.setattr(hemochromatosis, "_get_sample_engine", lambda _sample_id: engine)
    monkeypatch.setattr(
        hemochromatosis,
        "_fetch_findings",
        lambda _engine: [
            {
                "rsid": row.rsid,
                "gene_symbol": row.gene_symbol,
                "risk_classification": row.conditions,
                "finding_text": row.finding_text,
            }
            for row in rows
        ],
    )
    assert hemochromatosis.list_hemochromatosis_findings(sample_id=1) == (
        hemochromatosis.HemochromatosisFindingsListResponse(items=[], total=0)
    )

    text_rows = [
        _finding_row(id=3, finding_text="CYP2D6"),
        _finding_row(id=4, finding_text="tamoxifen"),
    ]
    _assert_individually_presentable(text_rows)
    text_engine = _Engine(text_rows)

    monkeypatch.setattr(apoe, "_get_sample_engine", lambda _sample_id: text_engine)
    monkeypatch.setattr(apoe, "_ensure_gate_acknowledged", lambda _engine: None)
    assert apoe.list_apoe_findings(sample_id=1) == apoe.APOEFindingsListResponse(items=[], total=0)

    monkeypatch.setattr(kinship, "resolve_sample_engine", lambda _sample_id: text_engine)
    assert kinship.list_findings(sample_id=1) == kinship.KinshipListResponse(items=[], total=0)

    risk_rows = [
        {
            "rsid": "rs1",
            "gene_symbol": "CYP2D6",
            "risk_classification": "safe",
            "finding_text": "Safe finding",
        },
        {
            "rsid": "rs2",
            "gene_symbol": "SAFE1",
            "risk_classification": "safe",
            "finding_text": "tamoxifen",
        },
    ]
    assert all(is_patient_presentable_response_payload(row) for row in risk_rows)
    monkeypatch.setattr(risk_common, "resolve_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(risk_common, "fetch_risk_findings", lambda _engine, _module: risk_rows)
    router = risk_common.make_risk_router(
        module="test",
        prefix="/test",
        tags=["test"],
        disclaimer_title="Test",
        disclaimer_text="Test",
        runner=lambda _engine: (0, []),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/test/findings")

    assert endpoint(sample_id=1) == risk_common.RiskFindingsListResponse(items=[], total=0)

    monkeypatch.setattr(parkinsons, "resolve_sample_engine", lambda _sample_id: object())
    monkeypatch.setattr(parkinsons, "_ensure_gate_acknowledged", lambda _engine: None)
    monkeypatch.setattr(parkinsons, "fetch_risk_findings", lambda _engine, _module: risk_rows)
    assert parkinsons.list_findings(sample_id=1) == risk_common.RiskFindingsListResponse(
        items=[], total=0
    )


def test_rare_findings_and_exports_withhold_cross_row_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON list and TSV/VCF exports collapse unsafe aggregate DTOs."""
    rows = [
        _finding_row(
            id=1,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="CYP2D6",
            detail_json=json.dumps({"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}),
        ),
        _finding_row(
            id=2,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="SAFE1",
            detail_json=json.dumps(
                {
                    "chrom": "1",
                    "pos": 200,
                    "ref": "A",
                    "alt": "T",
                    "consequence": "tamoxifen",
                }
            ),
        ),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    findings_response = rare_variants.list_rare_variant_findings(
        sample_id=1,
        limit=None,
        offset=0,
    )
    tsv_response = rare_variants.export_rare_variants_tsv(sample_id=1)
    vcf_response = rare_variants.export_rare_variants_vcf(sample_id=1)

    assert findings_response == rare_variants.RareVariantFindingsListResponse(items=[], total=0)
    tsv = tsv_response.content
    vcf = vcf_response.content
    assert tsv.count("\n") == 1
    assert "CYP2D6" not in tsv
    assert "tamoxifen" not in tsv
    assert "CYP2D6" not in vcf
    assert "tamoxifen" not in vcf
    assert "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n" in vcf


def test_rare_tsv_preflights_then_emits_exact_row_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendered privacy validation and streamed row bytes cannot drift."""
    rows = [
        _finding_row(
            id=1,
            module="rare_variants",
            rsid="rs_first",
            gene_symbol="SAFE1",
            detail_json=json.dumps({"consequence": None, "cadd_phred": 0}),
        ),
        _finding_row(
            id=2,
            module="rare_variants",
            rsid="rs_second",
            gene_symbol="SAFE2",
            detail_json=json.dumps({"consequence": "missense_variant"}),
        ),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    scanned_lines: list[str] = []
    rendered_gate = rare_variants.is_patient_presentable_rendered_text_chunks

    def capture_rendered_gate(lines: Iterator[str]) -> bool:
        scanned_lines.extend(lines)
        return rendered_gate(scanned_lines)

    monkeypatch.setattr(
        rare_variants,
        "is_patient_presentable_rendered_text_chunks",
        capture_rendered_gate,
    )

    response = rare_variants.export_rare_variants_tsv(sample_id=1)

    expected_header = (
        "rsid\tgene_symbol\tcategory\tevidence_level\tzygosity\t"
        "clinvar_significance\tconditions\tconsequence\tgnomad_af_global\t"
        "cadd_phred\trevel\tfinding_text\n"
    )
    assert response.chunks == [expected_header, *scanned_lines]
    assert len(scanned_lines) == 2
    assert [line.split("\t", maxsplit=1)[0] for line in scanned_lines] == [
        "rs_first",
        "rs_second",
    ]
    assert all(line.endswith("\n") for line in scanned_lines)


# ══════════════════════════════════════════════════════════════════════
# Bounded TSV export producer (#2328)
# ══════════════════════════════════════════════════════════════════════


def test_rare_tsv_export_never_materialises_the_result_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export consumes bounded batches, never one whole-result fetch.

    The double raises on ``fetchall``, so this fails on the pre-#2328 handler
    rather than merely observing that the new one happens to batch.
    """
    rows = [
        _finding_row(id=index, module="rare_variants", rsid=f"rs_{index}", gene_symbol="SAFE1")
        for index in range(1, 12)
    ]
    _assert_individually_presentable(rows)
    engine = _Engine(rows, result_cls=_UnboundedFetchIsFatalRows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: engine)
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    response = rare_variants.export_rare_variants_tsv(sample_id=1)

    assert len(response.chunks) == 1 + len(rows), "every safe row must still be emitted"
    connection = engine.connections[0]
    result = connection.results[0]
    assert result.fetchmany_sizes, "the result set must be read in batches"
    assert set(result.fetchmany_sizes) == {rare_variants._RARE_VARIANT_EXPORT_BATCH}
    assert connection.closed, "the read connection must not outlive the handler"


def test_rare_tsv_export_reaches_its_verdict_without_a_whole_response_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No all-row projection is built: the whole-list gate is never called.

    Fails on the pre-#2328 handler, which passes
    ``[_payload_gate_projection(row) for row in export_rows]`` to it.
    """
    rows = [
        _finding_row(id=1, module="rare_variants", rsid="rs_first", gene_symbol="SAFE1"),
        _finding_row(id=2, module="rare_variants", rsid="rs_second", gene_symbol="SAFE2"),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    def _refuse(_value: object) -> bool:
        raise AssertionError("the export must not assemble the whole response as a list")

    monkeypatch.setattr(rare_variants, "is_patient_presentable_response_payload", _refuse)

    response = rare_variants.export_rare_variants_tsv(sample_id=1)

    assert len(response.chunks) == 3
    assert "rs_first" in response.content
    assert "rs_second" in response.content


def test_rare_tsv_export_withholds_a_cross_row_pair_through_the_spill_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cross-record verdict survives the disk-overflow path.

    Same two rows as ``test_rare_findings_and_exports_withhold_cross_row_pair``,
    with the spool threshold forced to zero so every line goes through the
    temporary file. A fold that lost the free-text gene or drug across rows
    would emit both.
    """
    rows = [
        _finding_row(
            id=1,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="CYP2D6",
            detail_json=json.dumps({"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}),
        ),
        _finding_row(
            id=2,
            module="rare_variants",
            category="clinvar_pathogenic",
            gene_symbol="SAFE1",
            detail_json=json.dumps({"consequence": "tamoxifen"}),
        ),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)
    monkeypatch.setattr(rare_variants, "_RARE_VARIANT_TSV_SPOOL_MAX_CHARS", 0)

    response = rare_variants.export_rare_variants_tsv(sample_id=1)

    assert response.content.count("\n") == 1
    assert "CYP2D6" not in response.content
    assert "tamoxifen" not in response.content


def test_rare_tsv_export_replays_exact_chunks_through_the_spill_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spilling to disk must not re-chunk or alter a single byte.

    The framing is length-prefixed precisely so a cell containing a newline
    replays as one chunk; splitting the spill on newlines would pass a
    byte-equality check and still break chunk shape here.
    """
    rows = [
        _finding_row(
            id=1,
            module="rare_variants",
            rsid="rs_first",
            gene_symbol="SAFE1",
            finding_text="line one\nline two\rline three",
        ),
        _finding_row(
            id=2,
            module="rare_variants",
            rsid="rs_second",
            gene_symbol="SAFE2",
            detail_json=json.dumps({"consequence": "missense_variant"}),
        ),
    ]
    _assert_individually_presentable(rows)
    monkeypatch.setattr(rare_variants, "StreamingResponse", _CapturedStreamingResponse)

    def _export() -> _CapturedStreamingResponse:
        monkeypatch.setattr(rare_variants, "_get_sample_engine", lambda _sample_id: _Engine(rows))
        return rare_variants.export_rare_variants_tsv(sample_id=1)

    monkeypatch.setattr(rare_variants, "_RARE_VARIANT_TSV_SPOOL_MAX_CHARS", 4_000_000)
    in_memory = _export()
    monkeypatch.setattr(rare_variants, "_RARE_VARIANT_TSV_SPOOL_MAX_CHARS", 0)
    spilled = _export()

    assert in_memory.chunks == spilled.chunks
    assert len(in_memory.chunks) == 3
    assert "line one\nline two\rline three" in in_memory.content
