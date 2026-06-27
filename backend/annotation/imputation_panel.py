"""1000 Genomes Phase 3 (v5a) imputation reference panel (SW-C1 / roadmap #1).

Wave C foundation: makes the **1000 Genomes Phase 3 v5a** phased reference panel
(Beagle ``bref3`` format, native build-37) fetchable and verifiable, so the Wave C
imputation runtime (SW-C2, local Beagle 5.x phase+impute) has a reference to
impute against. This module only *ships and verifies* the panel — it does not
impute.

**Why this panel** (see ``docs/external-inputs-strategy.md``): 1000 Genomes
genotypes are fully open/public with no redistribution restriction, and the
pre-built Phase 3 v5a panel is **natively GRCh37**, matching the repo's coordinate
system (``EXPECTED_GENOME_BUILD``) — avoiding the phased-panel liftover an NYGC 30×
(GRCh38-only) panel would force. Access-gated panels (HRC, TOPMed) would be BYO,
not bundleable. The panel is the standard Beagle distribution
(http://bochet.gcc.biostat.washington.edu/beagle/1000_Genomes_phase3_v5a/).
Cite the 1000 Genomes Project Consortium (2015, Nature 526:68-74; PMID:26432245).

**Accuracy caveats (evidence-verified 2026-06-26, ≥2 agreeing peer-reviewed
sources):** 1000G Phase 3 v5a is a defensible *v1* panel, but imputation accuracy
is materially **lower for rare/low-frequency variants** and for **ancestries
under-represented in 1000 Genomes** (PMID:32002535; DOI:10.1002/humu.23247;
DOI:10.1371/journal.pgen.1008500). Access-gated panels (HRC, TOPMed) outperform it
for those cases and are the upgrade path once their controlled-access terms are
acceptable. The downstream firewall (SW-C3) must quarantine imputed rare variants
from carrier/monogenic calls accordingly.

**Opt-in, not auto-fetched.** The panel is 23 per-chromosome ``bref3`` files
(~8.5 GB total); it is **not** wired into the default setup-wizard download (it
would be wasteful for users who never impute). It is fetched on demand via
``scripts/fetch_imputation_panel.py`` (or, later, by the SW-C2 imputation path)
into ``settings.imputation_panel_dir``.

**Integrity & provenance.** Each file's expected SHA-256 + byte size live in
``bundles/manifest.json -> imputation_panel.files`` (the single source of truth);
:func:`fetch_panel` verifies every download against it and :func:`validate_panel`
re-checks an existing install. Provenance (version + build + source) is recorded in
``database_versions`` via :func:`record_panel_version`.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    import sqlalchemy as sa

logger = structlog.get_logger(__name__)

# Panel provenance: 1000 Genomes Phase 3 Consortium (Nature 2015; PMID:26432245).
# The citation also travels with the data in bundles/manifest.json::imputation_panel.
PANEL_VERSION = "1000G_phase3_v5a_b37"
PANEL_BUILD = "GRCh37"

# Autosomes + X (the Beagle b37.bref3 distribution; no Y / no MT).
PANEL_CHROMOSOMES: tuple[str, ...] = (*(str(i) for i in range(1, 23)), "X")

# The genetic map (PLINK format, GRCh37) is distributed as one zip of per-chromosome
# ``plink.chr{N}.GRCh37.map`` files; Beagle imputation needs it alongside the panel.
_MAP_KEY = "map"
_MAP_ZIP_NAME = "plink.GRCh37.map.zip"

# Repo-root manifest (same file the bundle/pipeline registry reads).
_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"


@dataclass(frozen=True)
class PanelFile:
    """One panel artifact (a per-chromosome ``bref3`` file, or the map zip)."""

    key: str  # "1".."22", "X", or "map"
    filename: str
    url: str
    sha256: str
    size_bytes: int


def _bref3_filename(chrom: str) -> str:
    return f"chr{chrom}.1kg.phase3.v5a.b37.bref3"


def load_panel_manifest(manifest_path: Path | None = None) -> dict:
    """Load the ``imputation_panel`` section of ``bundles/manifest.json``.

    Raises:
        FileNotFoundError: the manifest is missing.
        KeyError: the manifest has no ``imputation_panel`` section.
    """
    path = manifest_path or _MANIFEST_PATH
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "imputation_panel" not in data:
        raise KeyError("bundles/manifest.json has no 'imputation_panel' section")
    return data["imputation_panel"]


def panel_files(manifest_path: Path | None = None) -> list[PanelFile]:
    """Return the full list of panel artifacts (chromosomes + map) from the manifest.

    Each chromosome's ``{sha256, size_bytes}`` comes from the manifest's
    ``files`` table; the URL is built from ``base_url`` (chromosomes) / ``map_url``
    (the map zip).

    Raises:
        KeyError: a declared chromosome (or the map) is missing from ``files``.
    """
    m = load_panel_manifest(manifest_path)
    base_url = m["base_url"].rstrip("/")
    files_meta = m["files"]
    out: list[PanelFile] = []
    for chrom in PANEL_CHROMOSOMES:
        key = f"chr{chrom}"
        meta = files_meta[key]
        fname = _bref3_filename(chrom)
        out.append(
            PanelFile(
                key=chrom,
                filename=fname,
                url=f"{base_url}/{fname}",
                sha256=meta["sha256"],
                size_bytes=int(meta["size_bytes"]),
            )
        )
    map_meta = files_meta[_MAP_KEY]
    out.append(
        PanelFile(
            key=_MAP_KEY,
            filename=_MAP_ZIP_NAME,
            url=m["map_url"],
            sha256=map_meta["sha256"],
            size_bytes=int(map_meta["size_bytes"]),
        )
    )
    return out


def panel_bref3_path(dest_dir: Path, chrom: str) -> Path:
    """Path to a chromosome's installed ``bref3`` file (what SW-C2 imputes against)."""
    return Path(dest_dir) / _bref3_filename(chrom)


def panel_map_path(dest_dir: Path, chrom: str) -> Path:
    """Path to a chromosome's installed PLINK genetic-map file (unzipped)."""
    return Path(dest_dir) / f"plink.chr{chrom}.GRCh37.map"


def _sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_ok(path: Path, expected_sha256: str, expected_size: int) -> bool:
    """True iff ``path`` exists, is the expected size, and matches the SHA-256."""
    p = Path(path)
    if not p.exists() or p.stat().st_size != expected_size:
        return False
    return _sha256_file(p) == expected_sha256


def _extract_map_zip(zip_path: Path, dest_dir: Path) -> int:
    """Extract per-chromosome PLINK map files from the map zip (traversal-safe).

    Mirrors the tar extractors' safety guards (reject absolute / ``..`` members).
    Returns the number of ``.map`` files written.
    """
    written = 0
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if not base or not base.endswith(".map"):
                continue
            if name.startswith("/") or ".." in Path(name).parts:
                logger.warning("imputation_panel_skip_unsafe_zip_entry", name=name)
                continue
            target = Path(dest_dir) / base
            with zf.open(name) as src, target.open("wb") as dst:
                dst.write(src.read())
            written += 1
    return written


def validate_panel(
    dest_dir: Path,
    *,
    chromosomes: tuple[str, ...] | None = None,
    manifest_path: Path | None = None,
    check_sha256: bool = True,
) -> bool:
    """True iff every expected ``bref3`` file (+ per-chromosome map) is installed.

    With ``check_sha256`` (default), each ``bref3`` file is verified against the
    manifest SHA-256; the map is verified by the presence of every per-chromosome
    ``plink.chr{N}.GRCh37.map`` (the zip itself is not retained after extraction).
    """
    wanted = chromosomes or PANEL_CHROMOSOMES
    by_key = {pf.key: pf for pf in panel_files(manifest_path)}
    dest = Path(dest_dir)
    for chrom in wanted:
        pf = by_key.get(chrom)
        if pf is None:
            return False
        bref3 = dest / pf.filename
        if check_sha256:
            if not _file_ok(bref3, pf.sha256, pf.size_bytes):
                return False
        elif not bref3.exists():
            return False
        if not panel_map_path(dest, chrom).exists():
            return False
    return True


def fetch_panel(
    dest_dir: Path,
    *,
    chromosomes: tuple[str, ...] | None = None,
    manifest_path: Path | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    timeout: float = 7200.0,
) -> list[str]:
    """Download + verify the panel into ``dest_dir`` (resumable across runs).

    For each requested chromosome (default: all), downloads the ``bref3`` file via
    the shared :func:`~backend.annotation.http_download.stream_download` helper and
    verifies it against the manifest SHA-256, then downloads + extracts the genetic
    map. A file already present with the correct size+SHA-256 is **skipped** (so a
    re-run resumes a partial install). ``progress`` is called as
    ``(key, done_index, total)`` after each artifact.

    Returns the list of artifact keys actually downloaded (skipped ones excluded).

    Raises:
        ValueError: a downloaded file's SHA-256 does not match the manifest.
    """
    from backend.annotation.http_download import (
        clear_validator_sidecar,
        read_validator_sidecar,
        stream_download,
        write_validator_sidecar,
    )

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    wanted = chromosomes or PANEL_CHROMOSOMES
    by_key = {pf.key: pf for pf in panel_files(manifest_path)}

    # bref3 chromosome files, then the map zip.
    todo: list[PanelFile] = [by_key[c] for c in wanted if c in by_key]
    todo.append(by_key[_MAP_KEY])
    total = len(todo)
    downloaded: list[str] = []

    def _download_one(pf: PanelFile, final_path: Path) -> bool:
        """Download+verify one artifact to ``final_path``; True if fetched, False if skipped."""
        if _file_ok(final_path, pf.sha256, pf.size_bytes):
            return False  # already present + valid → resume-skip
        tmp = final_path.with_name(final_path.name + ".part")
        logger.info("imputation_panel_download_start", key=pf.key, url=pf.url)
        stream_download(
            pf.url,
            tmp,
            timeout=timeout,
            resumable=True,
            validator=read_validator_sidecar(tmp),
            on_validator=lambda v: write_validator_sidecar(tmp, v),
        )
        actual = _sha256_file(tmp)
        if actual != pf.sha256:
            tmp.unlink(missing_ok=True)
            clear_validator_sidecar(tmp)
            raise ValueError(
                f"{pf.filename}: SHA-256 mismatch (expected {pf.sha256}, got {actual}) "
                f"— refusing to install a corrupt/altered panel file."
            )
        tmp.rename(final_path)
        clear_validator_sidecar(tmp)
        return True

    for i, pf in enumerate(todo, start=1):
        if pf.key == _MAP_KEY:
            map_zip = dest / pf.filename
            # The map is "installed" iff every per-chromosome map is present; the zip
            # itself is transient (extracted then removed).
            maps_present = all(panel_map_path(dest, c).exists() for c in wanted)
            if not maps_present:
                if _download_one(pf, map_zip):
                    downloaded.append(pf.key)
                _extract_map_zip(map_zip, dest)
                map_zip.unlink(missing_ok=True)
        else:
            if _download_one(pf, dest / pf.filename):
                downloaded.append(pf.key)
        if progress is not None:
            progress(pf.key, i, total)

    logger.info(
        "imputation_panel_fetch_complete",
        dest=str(dest),
        downloaded=len(downloaded),
        total=total,
    )
    return downloaded


def record_panel_version(
    engine: sa.Engine,
    *,
    version: str = PANEL_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the imputation panel version in ``database_versions`` (GRCh37).

    Must be written to reference.db so the Update Manager / Database Stats can see
    it (mirrors the GTEx/AlphaMissense version-recording contract).
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="imputation_panel",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
        genome_build=PANEL_BUILD,
    )
