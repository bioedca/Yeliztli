"""Engine-agnostic imputed-VCF parsing shared by the Wave C imputation engines.

Houses the per-ALT imputation-result model (:class:`ImputedVariant`), the
chromosome-token guard, and a generic parser that maps an imputed VCF (Beagle,
GLIMPSE2, or IMPUTE5) into those records, so the SW-C3 firewall
(:mod:`backend.analysis.imputation_firewall`) / persist / findings pipeline
consumes any engine's output identically. The engines differ only in which INFO
keys carry the imputation-quality metric and the allele frequency, and whether
output markers carry an explicit "imputed" flag:

* **Beagle** (SW-C2, :mod:`backend.analysis.imputation_runner`): quality ``DR2``,
  frequency ``AF``, imputed marker flagged by ``IMP``.
* **GLIMPSE2** (SW-C7, :mod:`backend.analysis.glimpse_runner`): quality ``INFO``
  (the IMPUTE info score), frequency ``RAF`` (the *reference-panel* allele
  frequency — the population frequency the firewall's rarity gate needs; GLIMPSE's
  per-sample ``AF`` is degenerate for a single sample), and **every** output
  marker is an imputed posterior (low-coverage sequencing has no directly-typed
  hard calls), so there is no per-marker imputed flag.
* **IMPUTE5** (SW-C7, :mod:`backend.analysis.impute5_runner`): quality ``INFO``,
  frequency ``AF``; output markers are treated as imputed.

**The quality metric is stored in ``ImputedVariant.dr2`` for every engine, but it
is NOT literally Beagle's DR2 across engines** (the field keeps its historical
name as the firewall's quality slot). Beagle ``DR2`` is a *correlation-based*
dosage r² (the squared correlation between true and imputed dosage); the
GLIMPSE2 / IMPUTE5 ``INFO`` score is the *IMPUTE-family* per-variant info score,
an **information-theoretic** metric that is computed differently (Naj 2019,
DOI:10.1002/cphg.84, classifies it as distinct from the dosage-r²/Rsq/allelic-R²
family — info != DR2). They are **not identical and not strictly interchangeable**;
what they share is the *role* — a per-variant imputation-quality value in
(approximately) [0, 1] used as the QC gate on whether an imputed call is reliable
enough to act on (info origin: IMPUTE2, PMID:22384356; carried into GLIMPSE2,
PMID:37386250).

Because the role is the same and ``WELL_IMPUTED_DR2`` (0.8) is deliberately
*conservative* — well above the loose GWAS convention of ~0.3–0.5 and within the
0.7–0.9 stringent band (PMC12250051) — the firewall reuses the **same 0.8 cutoff
value** for the IMPUTE info score. This reuses the *threshold number*, not the
claim that 0.8-info equals 0.8-DR2. **Caveat (multi-ancestry):** these *estimated*
quality scores over-estimate true dosage r² for under-represented ancestries / low
MAF and under-estimate it for the majority ancestry (PMC10788679), so a single
fixed 0.8 does not guarantee equal real accuracy across ancestries; the firewall's
additional MAF (rarity) gate partly mitigates this, and an ancestry/MAF-aware
threshold is a later refinement. (accessed 2026-06-28)
"""

from __future__ import annotations

import gzip
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

# Accepted chromosome tokens (autosomes + X). Also guards path construction —
# a chrom token is interpolated into panel/output/region paths, so a token
# carrying a path separator or ``..`` must never reach those paths.
_CHROM_RE = re.compile(r"^(?:[1-9]|1[0-9]|2[0-2]|X)$")


def normalize_chrom(chrom: str) -> str:
    """Normalize + validate a chromosome token to ``1``..``22`` / ``X``.

    Raises:
        ValueError: the token is not a supported chromosome (also blocks a token
            carrying a path separator or ``..`` from reaching a file path or a
            subprocess ``--region`` argument).
    """
    c = chrom.strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    c = c.upper()
    if not _CHROM_RE.fullmatch(c):
        raise ValueError(f"unsupported chromosome token: {chrom!r}")
    return c


@dataclass(frozen=True)
class ImputedVariant:
    """One ALT allele of a marker in an imputed output VCF (engine-agnostic)."""

    chrom: str
    pos: int
    ref: str
    alt: str
    # Imputation-quality QC slot (~0-1): Beagle DR2 vs IMPUTE info — see module docstring.
    dr2: float | None
    af: float | None  # population (reference-panel) ALT allele frequency
    imputed: bool  # True = imputed posterior; False = directly typed (Beagle only)
    dosage: float | None = None  # estimated ALT dose (FORMAT DS, per-sample, 0-2)
    # ALT copies in the sample FORMAT GT best-guess genotype / MAP call.
    best_guess_copies: int | None = None


def _compile_info_value_re(key: str) -> re.Pattern[str]:
    """Regex capturing a ``KEY=<value>`` INFO entry's value (``;``-delimited)."""
    return re.compile(rf"(?:^|;){re.escape(key)}=([^;]+)")


def _compile_info_flag_re(key: str) -> re.Pattern[str]:
    """Regex matching a valueless ``KEY`` INFO flag (e.g. Beagle ``IMP``)."""
    return re.compile(rf"(?:^|;){re.escape(key)}(?:;|=|$)")


def _open_maybe_gzip(path: Path) -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rt", encoding="utf-8")
    return p.open("r", encoding="utf-8")


def _info_floats(info: str, regex: re.Pattern[str]) -> list[float | None]:
    """Parse a per-ALT (Number=A) comma-separated float INFO field → list.

    Drops non-finite and out-of-[0, 1] entries to ``None``: DR2, the IMPUTE INFO
    score, AF and RAF are all bounded in [0, 1], so a malformed entry (e.g.
    ``DR2=1.2``, ``AF=-0.1``) can't poison the quality summary or wrongly clear
    the well-imputed cutoff.
    """
    m = regex.search(info)
    if not m:
        return []
    out: list[float | None] = []
    for tok in m.group(1).split(","):
        tok = tok.strip()
        try:
            val = float(tok)
        except ValueError:
            out.append(None)
            continue
        out.append(val if math.isfinite(val) and 0.0 <= val <= 1.0 else None)
    return out


def _sample_dosages(format_field: str, sample_field: str) -> list[float | None]:
    """Parse the per-ALT ``DS`` (estimated ALT dose) from a sample's FORMAT column.

    Beagle / GLIMPSE2 / IMPUTE5 all emit ``DS`` (one dose per ALT). Returns the
    per-ALT doses, dropping any non-finite or out-of-[0, 2] value to ``None`` (a
    diploid ALT dose is bounded in [0, 2]). Returns ``[]`` when ``DS`` is absent.
    """
    keys = format_field.split(":")
    if "DS" not in keys:
        return []
    idx = keys.index("DS")
    values = sample_field.split(":")
    if idx >= len(values):
        return []
    out: list[float | None] = []
    for tok in values[idx].split(","):
        tok = tok.strip()
        try:
            val = float(tok)
        except ValueError:
            out.append(None)
            continue
        out.append(val if math.isfinite(val) and 0.0 <= val <= 2.0 else None)
    return out


def _sample_best_guess_copies(
    format_field: str, sample_field: str, *, n_alts: int
) -> list[int | None]:
    """Parse per-ALT copy counts from the sample's ``GT`` best-guess genotype.

    ``GT`` uses allele indexes where ``0`` is REF and ``1..n`` are the ALT alleles.
    Returns one ALT-copy count per ALT, ``[]`` when ``GT`` is absent, and ``None``
    for every ALT when the genotype is missing or malformed.
    """
    keys = format_field.split(":")
    if "GT" not in keys:
        return []
    idx = keys.index("GT")
    values = sample_field.split(":")
    if idx >= len(values):
        return []
    gt = values[idx].strip()
    if not gt:
        return [None] * n_alts

    alleles = re.split(r"[|/]", gt)
    if len(alleles) != 2:
        return [None] * n_alts

    counts = [0] * n_alts
    for tok in alleles:
        allele = tok.strip()
        if allele in {"", "."}:
            return [None] * n_alts
        try:
            allele_idx = int(allele)
        except ValueError:
            return [None] * n_alts
        if allele_idx < 0 or allele_idx > n_alts:
            return [None] * n_alts
        if allele_idx == 0:
            continue
        counts[allele_idx - 1] += 1

    return [copies if 0 <= copies <= 2 else None for copies in counts]


def parse_engine_vcf(
    vcf_path: Path,
    *,
    quality_key: str,
    af_key: str,
    imputed_flag_key: str | None = None,
) -> Iterator[ImputedVariant]:
    """Yield :class:`ImputedVariant` per ALT from an imputed VCF (gz or plain).

    Generic over the imputation engine's INFO conventions:

    * ``quality_key`` — the per-ALT (Number=A) imputation-quality INFO key
      (Beagle ``DR2`` / GLIMPSE2 + IMPUTE5 ``INFO``); mapped to ``dr2``.
    * ``af_key`` — the per-ALT (Number=A) allele-frequency INFO key (Beagle +
      IMPUTE5 ``AF`` / GLIMPSE2 ``RAF``); mapped to ``af``.
    * ``imputed_flag_key`` — a valueless INFO flag set only on imputed markers
      (Beagle ``IMP``). When ``None`` (GLIMPSE2 / IMPUTE5), **every** marker is
      treated as imputed.

    The sample's per-ALT ``DS`` dosage and ``GT`` best-guess ALT copy count are
    read from FORMAT when present. Multi-allelic markers yield one record per ALT,
    each paired with its aligned quality / AF / dosage / copy-count entry.
    """
    q_re = _compile_info_value_re(quality_key)
    af_re = _compile_info_value_re(af_key)
    flag_re = _compile_info_flag_re(imputed_flag_key) if imputed_flag_key else None
    with _open_maybe_gzip(vcf_path) as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos_raw, _id, ref, alt, _qual, _filter, info = parts[:8]
            try:
                pos = int(pos_raw)
            except ValueError:
                continue
            imputed = bool(flag_re.search(info)) if flag_re is not None else True
            quality = _info_floats(info, q_re)
            af = _info_floats(info, af_re)
            # Per-sample dosage (single-sample imputed VCF: FORMAT=parts[8], sample=parts[9]).
            # The imputation pipeline is single-sample by contract; a multi-sample row
            # would mis-associate dosages downstream, so reject it loudly rather than
            # silently taking only the first sample.
            if len(parts) > 10:
                raise ValueError(
                    f"expected a single-sample imputed VCF, found {len(parts) - 9} "
                    f"samples in {vcf_path}"
                )
            alts = alt.split(",")
            dosage = _sample_dosages(parts[8], parts[9]) if len(parts) == 10 else []
            best_guess = (
                _sample_best_guess_copies(parts[8], parts[9], n_alts=len(alts))
                if len(parts) == 10
                else []
            )
            ref_u = ref.strip().upper()
            for i, a in enumerate(alts):
                yield ImputedVariant(
                    chrom=chrom.strip(),
                    pos=pos,
                    ref=ref_u,
                    alt=a.strip().upper(),
                    dr2=quality[i] if i < len(quality) else None,
                    af=af[i] if i < len(af) else None,
                    dosage=dosage[i] if i < len(dosage) else None,
                    best_guess_copies=best_guess[i] if i < len(best_guess) else None,
                    imputed=imputed,
                )
