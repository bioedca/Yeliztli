"""Serve cross-module cards from the panel that is loaded, not the one that was.

A cross-module card's target and note are panel data, but they are baked into
``findings.finding_text`` / ``detail_json`` when the module is scored. Sample
staleness is tied to reference-bundle versions, so a panel edit never prompts a
re-analysis: without this, correcting a link fixes it only for samples analysed
afterwards, and everyone else keeps following the old one (#2021).

Resolving against the live panel at read time needs no migration and no
invalidation — ``rsid`` is stable metadata every cross-module finding already
carries, and it is what the panel keys its links on.
"""

from __future__ import annotations

from functools import cache
from importlib import import_module
from typing import Any

# The two category labels every module scorer shares; each module defines the
# same strings as its own STANDARD / INDETERMINATE.
STANDARD_CATEGORY = "Standard"
INDETERMINATE_CATEGORY = "Indeterminate"


def emits_cross_module_card(category: str, *, drug_safety_handoff: bool = False) -> bool:
    """Whether a called SNP's category may produce a cross-module card (#2310).

    A ``Standard`` call is a non-carrier, and an ``Indeterminate`` one is a call
    the scorer withheld as uninterpretable (a strand-ambiguous palindromic
    homozygote, or an unmodelled allele). Neither may be handed a carrier-specific
    destination: the card would assert the very call the module refused to make.
    ``called_snps`` filters on ``present_in_sample`` only, so every builder has to
    apply this itself, and all of them do through this one predicate.

    The exception is a drug-safety handoff. An unresolved call cannot exclude the
    risk allele, and treating it as a no-risk result is the error #1455 corrected
    for G6PD, so an ``Indeterminate`` drug-safety card is kept and its builder
    states that the call can neither confirm nor exclude the allele.
    """
    if category == STANDARD_CATEGORY:
        return False
    if is_withheld_call(category):
        return drug_safety_handoff
    return True


def is_withheld_call(category: str) -> bool:
    """Whether the scorer withheld this call as uninterpretable (#2310).

    A drug-safety card kept for such a call must say it can neither confirm nor
    exclude the allele, rather than read as a carrier call.
    """
    return category == INDETERMINATE_CATEGORY


def withheld_call_caveat(subject: str) -> str:
    """The words a kept drug-safety card for a withheld call puts before its note."""
    return f"indeterminate call: it can neither confirm nor exclude {subject}. "


def panel_cross_module_links(panel: Any) -> dict[str, dict]:
    """Map rsid -> the ``cross_module`` block the panel currently declares."""
    return {
        snp.rsid: snp.cross_module
        for pathway in panel.pathways
        for snp in pathway.snps
        if getattr(snp, "cross_module", None)
    }


def current_link(links: dict[str, dict], finding: dict[str, Any]) -> dict | None:
    """The live ``cross_module`` block for a stored finding, or None to drop it.

    ``None`` means the panel no longer declares this link at all — the celiac
    DQ2/DQ8 proxies, whose Nutrigenomics handoff was removed — so the card must
    not be rendered from the stored row.
    """
    return links.get(finding.get("rsid") or "")


def refreshed_finding_text(finding: dict[str, Any], link: dict) -> str:
    """Swap the stored trailing note for the one the panel gives now.

    Only the note is replaced, and only when the stored text actually ends with
    the note the finding recorded, so the sample-specific prefix (variant label,
    genotype, and any module-specific framing) is never rewritten. Anything
    unrecognised is returned untouched.
    """
    text = finding.get("finding_text") or ""
    stored_note = (finding.get("detail") or {}).get("cross_module_note")
    current_note = link.get("note")
    if not stored_note or not current_note or stored_note == current_note:
        return text
    if not text.endswith(str(stored_note)):
        return text
    return text[: -len(str(stored_note))] + str(current_note)


# ── Generic findings path ────────────────────────────────────────────────

# Source module -> its panel loader, as "module.path:callable". Lazy so importing
# this module does not pull in every analysis module, and cached because the
# generic findings endpoint resolves one row at a time.
# Only modules whose links are keyed per SNP belong here. Traits builds its
# cross-module findings from the panel-level ``cross_module_links`` list instead,
# keyed on ``to_module`` rather than on an rsid, so resolving its rows this way
# would drop every one of them.
_PANEL_LOADERS: dict[str, str] = {
    "allergy": "backend.analysis.allergy:load_allergy_panel",
    "gene_health": "backend.analysis.gene_health:load_gene_health_panel",
    "skin": "backend.analysis.skin:load_skin_panel",
    # Sleep declares no cross-module links since #2024 removed its CYP1A2 ->
    # Pharmacogenomics handoff. Registering it is what retires the rows that
    # were already stored: an unregistered module is passed through untouched.
    "sleep": "backend.analysis.sleep:load_sleep_panel",
}

CROSS_MODULE_CATEGORY = "cross_module"


@cache
def _links_for_module(module: str) -> dict[str, dict]:
    target = _PANEL_LOADERS.get(module)
    if target is None:
        return {}
    module_path, _, loader_name = target.partition(":")
    return panel_cross_module_links(getattr(import_module(module_path), loader_name)())


# ── Stored cards and the withheld-call rule (#2310) ──────────────────────

# A card is written when its module is scored, and a scorer change marks no
# sample stale, so a card stored before #2310 for a withheld call would keep
# being served. Every stored card recorded the genotype it was built from, so
# re-scoring that genotype against the live panel gives back its call, and the
# read path applies the same rule as the builders. Source module -> (its
# single-SNP scorer, whether that scorer also takes the panel).
_SNP_SCORERS: dict[str, tuple[str, bool]] = {
    "allergy": ("backend.analysis.allergy:_score_snp", False),
    "gene_health": ("backend.analysis.gene_health:_score_snp", False),
    "skin": ("backend.analysis.skin:_score_snp", False),
    "sleep": ("backend.analysis.sleep:_score_snp", True),
    "traits": ("backend.analysis.traits:_score_snp", False),
}
# Traits is left out of _PANEL_LOADERS for link resolution, but its scorer
# still needs its panel.
_SCORING_PANEL_LOADERS = {**_PANEL_LOADERS, "traits": "backend.analysis.traits:load_traits_panel"}


def _resolve_callable(target: str) -> Any:
    module_path, _, name = target.partition(":")
    return getattr(import_module(module_path), name)


@cache
def _scoring_panel(module: str) -> Any:
    return _resolve_callable(_SCORING_PANEL_LOADERS[module])()


def _panel_snp(module: str, rsid: str) -> Any:
    panel = _scoring_panel(module)
    return next(
        (snp for pathway in panel.pathways for snp in pathway.snps if snp.rsid == rsid), None
    )


def stored_card_call(module: str | None, finding: dict[str, Any]) -> str | None:
    """Re-derive the call a stored cross-module card was built from.

    ``None`` when it cannot be re-derived: a module without a scorer here, a
    row that recorded no genotype, or an rsid the panel no longer carries.
    Such a row is left to the link resolution it already had.
    """
    spec = _SNP_SCORERS.get(module or "")
    rsid = finding.get("rsid")
    genotype = (finding.get("detail") or {}).get("genotype")
    if spec is None or not rsid or not genotype:
        return None
    panel_snp = _panel_snp(module or "", rsid)
    if panel_snp is None:
        return None
    target, takes_panel = spec
    scorer = _resolve_callable(target)
    if takes_panel:
        return scorer(panel_snp, genotype, _scoring_panel(module or "")).category
    return scorer(panel_snp, genotype).category


def _stored_card_drug(finding: dict[str, Any], link: dict | None) -> Any:
    return (finding.get("detail") or {}).get("drug") or (link or {}).get("drug")


def stored_card_is_withheld(
    module: str | None, finding: dict[str, Any], link: dict | None = None
) -> bool:
    """Whether a stored card's call is one no card may be shown for (#2310)."""
    call = stored_card_call(module, finding)
    if call is None:
        return False
    drug_safety = _stored_card_drug(finding, link) is not None
    return not emits_cross_module_card(call, drug_safety_handoff=drug_safety)


def _with_withheld_caveat(module: str, finding: dict[str, Any], link: dict, text: str) -> str:
    """Give a kept drug-safety card for a withheld call its caveat, once."""
    if "indeterminate call:" in text:
        return text
    panel_snp = _panel_snp(module, finding.get("rsid") or "")
    hla_proxy = getattr(panel_snp, "hla_proxy", None) or {}
    subject = hla_proxy.get("hla_allele") or finding.get("gene_symbol") or "this allele"
    caveat = withheld_call_caveat(subject)
    note = str(link.get("note") or "")
    if note and text.endswith(note):
        return text[: -len(note)] + caveat + note
    return f"{text} {caveat.strip()}"


def resolve_stored_card(
    module: str, finding: dict[str, Any], links: dict[str, dict]
) -> tuple[dict, str] | None:
    """The live link and text for a stored cross-module card, or ``None`` to drop it.

    Applies the link the panel declares now (#2021), then the withheld-call
    rule (#2310): a card whose call is non-carrier or withheld is dropped, and
    a kept drug-safety card for a withheld call gets the caveat a newly built
    one carries.
    """
    link = current_link(links, finding)
    if link is None or stored_card_is_withheld(module, finding, link):
        return None
    text = refreshed_finding_text(finding, link)
    call = stored_card_call(module, finding)
    if call is not None and is_withheld_call(call):
        text = _with_withheld_caveat(module, finding, link, text)
    return link, text


def normalize_cross_module_row(
    module: str | None,
    category: str | None,
    rsid: str | None,
    finding_text: str | None,
    detail: dict[str, Any] | None,
) -> tuple[str | None, dict[str, Any] | None] | None:
    """Bring one stored cross-module row up to the panel that is loaded.

    Returns the corrected ``(finding_text, detail)``, or ``None`` when the panel
    no longer declares the link and the row must not be rendered at all. Rows
    from another category, or from a module that declares no links, pass through
    untouched.

    The dedicated pathway endpoints resolve their own rows, but the generic
    findings aggregator renders the same rows and is the surface that has
    repeatedly bypassed per-module gating (#2021).
    """
    if category != CROSS_MODULE_CATEGORY:
        return finding_text, detail
    finding = {"rsid": rsid, "finding_text": finding_text, "detail": detail}
    # The withheld-call rule (#2310) covers Traits too, whose links are not
    # resolved here.
    if (module or "") not in _PANEL_LOADERS:
        if stored_card_is_withheld(module, finding):
            return None
        return finding_text, detail
    # A *registered* module whose last link was removed retires every stored
    # row, so "no links" must not be read as "not our concern".
    resolved = resolve_stored_card(module or "", finding, _links_for_module(module or ""))
    if resolved is None:
        return None
    link, text = resolved

    corrected_detail = dict(detail or {})
    corrected_detail["target_module"] = link["module"]
    if link.get("note"):
        corrected_detail["cross_module_note"] = link["note"]
    return text, corrected_detail


@cache
def _recommendations_for_module(module: str) -> dict[str, str]:
    """Map rsid -> the per-SNP recommendation the panel currently gives."""
    target = _PANEL_LOADERS.get(module)
    if target is None:
        return {}
    module_path, _, loader_name = target.partition(":")
    panel = getattr(import_module(module_path), loader_name)()
    return {
        snp.rsid: snp.recommendation_text
        for pathway in panel.pathways
        for snp in pathway.snps
        if getattr(snp, "recommendation_text", None)
    }


def current_recommendation(module: str | None, rsid: str | None, stored: str | None) -> str | None:
    """The panel's current recommendation for a SNP, else what was stored.

    ``recommendation_text`` is panel prose persisted into each SNP finding, so a
    correction to it reaches an existing sample only on a re-score. The FTO row
    advertised Nutrigenomics content that does not exist, and its detail panel
    kept saying so even once the adjacent cross-module card had been retargeted
    (#2021).
    """
    # Refresh only what a stored finding already supplied. A called Standard
    # non-carrier deliberately gets no snp_finding row, so looking the panel up
    # unconditionally would hand a hom-ref user the carrier-specific advice the
    # storage layer withheld from them.
    if not rsid or not stored:
        return stored
    return _recommendations_for_module(module or "").get(rsid, stored)


# Categories whose stored ``detail`` carries the panel's per-SNP recommendation.
RECOMMENDATION_CATEGORIES = frozenset({"snp_finding", "carrier_context"})


def refreshed_detail_recommendation(
    module: str | None,
    category: str | None,
    rsid: str | None,
    detail: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return ``detail`` with its ``recommendation`` resolved against the panel.

    The generic findings aggregator hands back each stored ``detail`` blob as
    it is, so the dedicated route's refresh alone would leave the legacy text
    reachable through ``/api/analysis/findings`` and the summary's
    high-confidence preview (#2021). Gated the same way: a category that never
    stores a recommendation, or a row that has none, is left alone, so a
    non-carrier is never handed carrier-facing advice.
    """
    if category not in RECOMMENDATION_CATEGORIES or not isinstance(detail, dict):
        return detail
    stored = detail.get("recommendation")
    current = current_recommendation(module, rsid, stored)
    if current == stored:
        return detail
    return {**detail, "recommendation": current}


def live_cross_module_clause(columns: Any) -> Any:
    """SQL predicate excluding cross-module rows whose link the panel retired.

    Report generation bounds its selection with a COUNT before it sorts, so a
    Python-side filter applied after loading would let a retired row push a
    report over ``MAX_REPORT_FINDINGS`` and raise even though it would never be
    rendered (#2021). Expressing the rule in SQL keeps the preflight, the load
    and the rendered set counting the same rows.

    Rows from unregistered modules, and non-cross-module rows, always pass.
    """
    import sqlalchemy as sa

    retired = []
    for module in _PANEL_LOADERS:
        module_rows = sa.and_(
            columns.module == module,
            columns.category == CROSS_MODULE_CATEGORY,
        )
        live = _links_for_module(module)
        # A registered module with no links left retires all of its rows; one
        # with links retires only the rsids it no longer declares. A NULL rsid
        # can never match a declared one, so it is retired either way.
        retired.append(
            module_rows
            if not live
            else sa.and_(
                module_rows,
                sa.or_(columns.rsid.is_(None), columns.rsid.notin_(sorted(live))),
            )
        )
    return sa.not_(sa.or_(*retired)) if retired else sa.true()
