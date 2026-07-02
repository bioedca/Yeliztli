"""Raw imputed-HLA viewer/export (Wave D / SW-D5, roadmap #37).

Presents a sample's imputed classical-HLA calls (``hla_calls``, resolved via
:mod:`backend.analysis.hla_resolver`) as a raw per-locus allele list for viewing
and export, with the confidence attached to each call. This is the transparency
surface behind the SW-D2–D4 clinical interpretations — it shows the underlying
2-field genotypes, ordered by the classical-locus convention.

**Two guardrails travel with every response:**

- :data:`HLA_IMPUTED_CONFIRMATION_CAVEAT` — the calls are statistically imputed
  from SNP genotypes, not directly typed.
- :data:`HLA_TRANSPLANT_GUARD` — imputed HLA is **never** valid for transplant /
  organ / stem-cell donor matching; only accredited wet-lab high-resolution HLA
  typing is. This is the load-bearing safety statement for a raw HLA export.

This module only assembles + frames the stored calls; it never runs the classifier.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.analysis.hibag_runner import HLA_LOCI
from backend.analysis.hla_drug_hypersensitivity import HLA_IMPUTED_CONFIRMATION_CAVEAT
from backend.analysis.hla_resolver import ResolvedHLACall

# The load-bearing SW-D5 safety guard for a raw HLA allele list/export.
HLA_TRANSPLANT_GUARD = (
    "These HLA types are statistically imputed and must NEVER be used for transplant, "
    "organ, or haematopoietic stem-cell donor/recipient matching. Only accredited "
    "wet-lab high-resolution HLA typing is valid for donor matching or any clinical "
    "HLA decision."
)

# Canonical display order for the classical loci (HIBAG's locus set), with any
# other locus sorted after these, alphabetically.
_LOCUS_RANK = {locus.upper(): i for i, locus in enumerate(HLA_LOCI)}

_UNAVAILABLE_NOTE = (
    "No imputed HLA calls are available for this sample. HLA imputation requires an "
    "operator-installed HIBAG runtime and a per-ancestry model."
)


@dataclass(frozen=True)
class HlaAlleleView:
    """One per-locus imputed HLA genotype for the raw viewer/export."""

    locus: str
    allele1: str  # 2-field, e.g. "57:01"
    allele2: str
    prob: float | None  # HIBAG posterior call probability (the confidence)
    low_confidence: bool
    source: str
    ancestry_model: str | None


@dataclass
class HlaViewerReport:
    """Raw imputed-HLA viewer/export payload for a sample."""

    available: bool
    alleles: list[HlaAlleleView] = field(default_factory=list)
    caveat: str = HLA_IMPUTED_CONFIRMATION_CAVEAT
    transplant_guard: str = HLA_TRANSPLANT_GUARD
    unavailable_note: str | None = None


def _locus_sort_key(call: ResolvedHLACall) -> tuple[int, str]:
    up = call.locus.upper()
    return (_LOCUS_RANK.get(up, len(_LOCUS_RANK)), up)


def build_hla_viewer(calls: Sequence[ResolvedHLACall]) -> HlaViewerReport:
    """Assemble a sample's raw imputed-HLA calls, ordered by classical locus.

    With no calls the report is ``available=False`` with a note. The
    confirmatory-typing caveat and the never-for-transplant guard are always
    present so an export can never be detached from its safety framing.
    """
    if not calls:
        return HlaViewerReport(available=False, unavailable_note=_UNAVAILABLE_NOTE)
    ordered = sorted(calls, key=_locus_sort_key)
    return HlaViewerReport(
        available=True,
        alleles=[
            HlaAlleleView(
                locus=c.locus,
                allele1=c.allele1,
                allele2=c.allele2,
                prob=c.prob,
                low_confidence=c.low_confidence,
                source=c.source,
                ancestry_model=c.ancestry_model,
            )
            for c in ordered
        ],
    )
