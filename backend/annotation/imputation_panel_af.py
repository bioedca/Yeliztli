"""Reference-panel allele-frequency index for Beagle imputation output.

Beagle's imputed VCF ``AF`` INFO field is the target-sample ALT frequency, not a
population/reference-panel frequency. The imputation firewall therefore needs a
separate source derived from the same 1000G Phase 3 v5a panel used for imputation.

This module loads a compact per-chromosome TSV index when present, and can build
that index from the Beagle-distributed 1000G b37 VCF files:

``chrom  pos  ref  alt  af``

Rows are keyed by normalized ``chrom/pos/ref/alt``. Duplicate exact keys are
treated as ambiguous and return ``None`` at lookup time, preserving the firewall's
fail-closed behavior.
"""

from __future__ import annotations

import gzip
import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import structlog

from backend.analysis.imputation_vcf import normalize_chrom

logger = structlog.get_logger(__name__)

_AF_FILENAME_RE = re.compile(r"^chr(.+)\.1kg\.phase3\.v5a\.b37\.af\.tsv(?:\.gz)?$")


@dataclass(frozen=True, order=True)
class PanelAfKey:
    """Exact normalized key for one reference-panel ALT allele."""

    chrom: str
    pos: int
    ref: str
    alt: str


def panel_af_path(dest_dir: Path, chrom: str) -> Path:
    """Canonical compressed AF TSV path for one 1000G Phase 3 v5a chromosome."""
    c = normalize_chrom(chrom)
    return Path(dest_dir) / f"chr{c}.1kg.phase3.v5a.b37.af.tsv.gz"


def panel_vcf_path(vcf_dir: Path, chrom: str) -> Path:
    """Beagle-distributed b37 VCF path used to build the AF TSV."""
    c = normalize_chrom(chrom)
    return Path(vcf_dir) / f"chr{c}.1kg.phase3.v5a.vcf.gz"


def _normalize_key(chrom: str, pos: int | str, ref: str, alt: str) -> PanelAfKey | None:
    """Normalize a panel allele key; return ``None`` for unsupported rows."""
    try:
        c = normalize_chrom(chrom)
        p = int(pos)
    except (TypeError, ValueError):
        return None
    ref_u = ref.strip().upper()
    alt_u = alt.strip().upper()
    if (
        p <= 0
        or not ref_u
        or not alt_u
        or "," in alt_u
        or _is_symbolic_allele(ref_u)
        or _is_symbolic_allele(alt_u)
    ):
        return None
    return PanelAfKey(c, p, ref_u, alt_u)


def _valid_af(raw: str | float) -> float | None:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isfinite(val) and 0.0 <= val <= 1.0:
        return val
    return None


def _is_symbolic_allele(allele: str) -> bool:
    return allele in {".", "*"} or allele.startswith("<") or "[" in allele or "]" in allele


def _open_text(path: Path, mode: str = "rt") -> IO[str]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, mode, encoding="utf-8")
    return p.open(mode, encoding="utf-8")


@dataclass
class _ChromAfIndex:
    values: dict[PanelAfKey, float]
    ambiguous: set[PanelAfKey]

    @classmethod
    def from_tsv(cls, path: Path) -> _ChromAfIndex:
        values: dict[PanelAfKey, float] = {}
        ambiguous: set[PanelAfKey] = set()
        with _open_text(path) as fh:
            for line in fh:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                parts = raw.split("\t")
                if len(parts) < 5:
                    continue
                if parts[:5] == ["chrom", "pos", "ref", "alt", "af"]:
                    continue
                key = _normalize_key(parts[0], parts[1], parts[2], parts[3])
                af = _valid_af(parts[4])
                if key is None or af is None:
                    continue
                if key in values or key in ambiguous:
                    values.pop(key, None)
                    ambiguous.add(key)
                    continue
                values[key] = af
        return cls(values=values, ambiguous=ambiguous)

    def lookup(self, key: PanelAfKey) -> float | None:
        if key in self.ambiguous:
            return None
        return self.values.get(key)


class PanelAfLookup:
    """Lazy per-chromosome 1000G panel AF lookup.

    Only one chromosome's TSV is held in memory at a time. Missing chromosome TSVs
    are cached as unavailable and return ``None`` so Beagle variants continue to
    fail closed as ``missing_af``.
    """

    def __init__(self, panel_dir: Path) -> None:
        self.panel_dir = Path(panel_dir)
        self._loaded_chrom: str | None = None
        self._loaded_index: _ChromAfIndex | None = None
        self._missing_chroms: set[str] = set()

    def lookup(self, chrom: str, pos: int, ref: str, alt: str) -> float | None:
        key = _normalize_key(chrom, pos, ref, alt)
        if key is None:
            return None
        index = self._index_for_chrom(key.chrom)
        if index is None:
            return None
        return index.lookup(key)

    def _index_for_chrom(self, chrom: str) -> _ChromAfIndex | None:
        if chrom == self._loaded_chrom:
            return self._loaded_index
        if chrom in self._missing_chroms:
            return None

        path = _existing_panel_af_path(self.panel_dir, chrom)
        if path is None:
            self._loaded_chrom = chrom
            self._loaded_index = None
            self._missing_chroms.add(chrom)
            logger.info("imputation_panel_af_missing", chrom=chrom, panel_dir=str(self.panel_dir))
            return None

        index = _ChromAfIndex.from_tsv(path)
        self._loaded_chrom = chrom
        self._loaded_index = index
        logger.info(
            "imputation_panel_af_loaded",
            chrom=chrom,
            path=str(path),
            n_records=len(index.values),
            n_ambiguous=len(index.ambiguous),
        )
        return index


def _existing_panel_af_path(panel_dir: Path, chrom: str) -> Path | None:
    gz_path = panel_af_path(panel_dir, chrom)
    if gz_path.exists():
        return gz_path
    plain = gz_path.with_suffix("")
    if plain.exists():
        return plain
    return None


def build_panel_af_index(vcf_path: Path, out_path: Path) -> int:
    """Build a per-ALT AF TSV from a 1000G panel VCF.

    The preferred source is genotype counts from FORMAT/GT sample columns. If a
    VCF has no sample columns, bounded INFO/AF is accepted as a fallback. Returns
    the number of per-ALT rows written.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with _open_text(Path(vcf_path)) as src, _open_text(out, "wt") as dst:
        dst.write("# source=1000G_phase3_v5a_b37_panel_vcf\n")
        dst.write("chrom\tpos\tref\talt\taf\n")
        for line in src:
            if not line or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            chrom, pos_raw, _id, ref, alt_raw, _qual, _filter, info = parts[:8]
            alts = [alt.strip().upper() for alt in alt_raw.split(",")]
            key_seed = _normalize_key(chrom, pos_raw, ref, alts[0] if alts else "")
            if key_seed is None:
                continue
            afs = (
                _afs_from_genotypes(parts[8], parts[9:], n_alts=len(alts))
                if len(parts) >= 10
                else _afs_from_info(info, n_alts=len(alts))
            )
            if not afs:
                continue
            for i, alt in enumerate(alts):
                af = afs[i] if i < len(afs) else None
                key = _normalize_key(chrom, pos_raw, ref, alt)
                if key is None or af is None:
                    continue
                dst.write(f"{key.chrom}\t{key.pos}\t{key.ref}\t{key.alt}\t{af:.12g}\n")
                n_written += 1
    logger.info("imputation_panel_af_built", source=str(vcf_path), out=str(out), n=n_written)
    return n_written


def _afs_from_info(info: str, *, n_alts: int) -> list[float | None]:
    values = _info_values(info, "AF")
    if not values:
        return []
    out = [_valid_af(tok) for tok in values[:n_alts]]
    return out if any(v is not None for v in out) else []


def _info_values(info: str, key: str) -> list[str]:
    prefix = f"{key}="
    for entry in info.split(";"):
        if entry.startswith(prefix):
            return entry[len(prefix) :].split(",")
    return []


def _afs_from_genotypes(
    format_field: str, sample_fields: Sequence[str], *, n_alts: int
) -> list[float | None]:
    keys = format_field.split(":")
    if "GT" not in keys:
        return []
    gt_idx = keys.index("GT")
    alt_counts = [0] * n_alts
    allele_total = 0

    for sample in sample_fields:
        values = sample.split(":")
        if gt_idx >= len(values):
            continue
        for allele_idx in _gt_alleles(values[gt_idx], n_alts=n_alts):
            if allele_idx is None:
                continue
            allele_total += 1
            if allele_idx > 0:
                alt_counts[allele_idx - 1] += 1

    if allele_total == 0:
        return []
    return [count / allele_total for count in alt_counts]


def _gt_alleles(gt: str, *, n_alts: int) -> Iterator[int | None]:
    for tok in re.split(r"[|/]", gt.strip()):
        allele = tok.strip()
        if allele in {"", "."}:
            yield None
            continue
        try:
            idx = int(allele)
        except ValueError:
            yield None
            continue
        yield idx if 0 <= idx <= n_alts else None


def infer_panel_af_chrom(path: Path) -> str | None:
    """Return the chromosome encoded in a canonical AF TSV filename, if present."""
    m = _AF_FILENAME_RE.match(Path(path).name)
    if not m:
        return None
    try:
        return normalize_chrom(m.group(1))
    except ValueError:
        return None
