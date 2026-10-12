"""run_all must infer ancestry before any module that reads it (#2082).

Several ``run_all`` modules bake the stored ancestry call into what they persist:
cancer PRS (mismatch caveat and percentile calibration), APOL1 and gout (ancestry
context), nutrigenomics (the LCT caveat) and traits PRS. They all read it through
``ancestry._get_latest_ancestry_finding`` — the one helper behind
``get_inferred_ancestry``, ``get_top_ancestry_fraction`` and
``prs_calibration.get_ancestry_fractions``. When a module runs before
``ancestry``, a sample's first full run reads nothing, so the cancer card shows
"Ancestry inference has not been run" while the Ancestry page shows a result.

The guard instruments that helper on the live pipeline, so a newly added
ancestry consumer placed before ``ancestry`` fails here without a hand-kept list.
"""

from __future__ import annotations

import backend.analysis.ancestry as ancestry_mod
from backend.analysis import cancer_prs, run_all
from tests.backend.annotation_validation.conftest import with_xx_scaffold

_VARIANTS = with_xx_scaffold(
    [{"rsid": "rs_common", "chrom": "1", "pos": 1_000_000, "genotype": "CC"}]
)


def test_ancestry_consumers_read_a_stored_ancestry_call(build_live_run, monkeypatch) -> None:
    run = build_live_run(variants=_VARIANTS, run_analyses=False)

    state: dict[str, object] = {"module": None, "ancestry_done": False}
    reads: list[tuple[str, bool, str | None]] = []
    cancer_results: list[cancer_prs.CancerPRSResult] = []

    real_latest = ancestry_mod._get_latest_ancestry_finding

    def recording_latest(sample_engine):
        top, detail = real_latest(sample_engine)
        reads.append((state["module"], state["ancestry_done"], top))
        return top, detail

    real_run_ancestry = run_all._run_ancestry

    def tracked_run_ancestry(sample_engine, registry):
        try:
            return real_run_ancestry(sample_engine, registry)
        finally:
            state["ancestry_done"] = True

    real_run_cancer_prs = cancer_prs.run_cancer_prs

    def capturing_run_cancer_prs(*args, **kwargs):
        result = real_run_cancer_prs(*args, **kwargs)
        cancer_results.append(result)
        return result

    monkeypatch.setattr(ancestry_mod, "_get_latest_ancestry_finding", recording_latest)
    monkeypatch.setattr(run_all, "_run_ancestry", tracked_run_ancestry)
    monkeypatch.setattr(cancer_prs, "run_cancer_prs", capturing_run_cancer_prs)

    results = run_all.run_all_analyses(
        run.sample_engine,
        run.registry,
        progress_callback=lambda name, _index, _total: state.update(module=name),
    )

    # Ancestry really ran and stored a call (UNCERTAIN on this tiny sample), so a
    # consumer that reads nothing is reading too early, not reading an absence.
    assert results["ancestry"] != "error"
    assert ancestry_mod.get_inferred_ancestry(run.sample_engine) == ancestry_mod.UNCERTAIN

    consumer_reads = [read for read in reads if read[0] != "ancestry"]
    consumers = {module for module, _done, _top in consumer_reads}
    # The modules #2082 caught reading too early must still go through the
    # instrumented helper, or this guard would silently stop covering them.
    assert {"apol1", "cancer", "gout", "nutrigenomics", "traits"} <= consumers, consumers

    early = sorted({module for module, done, _top in consumer_reads if not done})
    assert early == [], f"modules read ancestry before run_all inferred it: {early}"
    blind = sorted({module for module, _done, top in consumer_reads if top is None})
    assert blind == [], f"modules saw no stored ancestry call: {blind}"

    # The user-visible symptom: the stored cancer PRS caveat must describe the
    # ancestry result, not claim inference never ran.
    assert len(cancer_results) == 1
    prs_results = cancer_results[0].results
    assert prs_results, "cancer PRS produced no results to check"
    for prs in prs_results:
        assert prs.ancestry_warning_text is not None, prs.trait
        assert "has not been run" not in prs.ancestry_warning_text, prs.trait
        assert prs.ancestry_warning_text.startswith(
            "Ancestry could not be confidently inferred"
        ), prs.ancestry_warning_text
