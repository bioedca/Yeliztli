"""Gnomix local ancestry inference engine using re-exported models.

Implements the Gnomix inference pipeline without requiring the original
gnomix Python package or scikit-learn.  Uses numpy for the base model
(logistic regression per window) and xgboost for the CRF smoother.

Re-exported model format (per chromosome directory):
  - metadata.npz: snp_pos, snp_ref, snp_alt, population_order,
    scalars A (num pops), C (num SNPs), M (SNPs/window), W (num windows),
    S (smoother context), context, n_features
  - base_coefs.npz: coefs (W x A x max_features), intercepts (W x A),
    window_n_features (W,)
  - smoother.json: XGBoost native booster format

Population order in models: [CSA, AFR, OCE, EUR, MID, AMR, EAS]
Canonical order: [AFR, AMR, CSA, EAS, EUR, MID, OCE]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Canonical population order used throughout Yeliztli
CANONICAL_POPULATIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")


@dataclass
class GnomixModel:
    """Loaded Gnomix model for a single chromosome."""

    chrom: int
    n_snps: int  # C
    n_pops: int  # A
    n_windows: int  # W
    window_size: int  # M (SNPs per window)
    smoother_context: int  # S
    context: int  # context SNPs at window edges
    snp_pos: np.ndarray  # (C,) GRCh38 positions
    snp_ref: np.ndarray  # (C,) reference alleles
    snp_alt: np.ndarray  # (C,) alternate alleles
    population_order: list[str]  # model's population order
    coefs: np.ndarray  # (W, A, max_features)
    intercepts: np.ndarray  # (W, A)
    window_n_features: np.ndarray  # (W,) actual feature count per window
    smoother_path: Path  # path to smoother.json
    pop_remap: np.ndarray = field(init=False)  # index mapping model → canonical

    def __post_init__(self) -> None:
        model_to_canonical = {pop: i for i, pop in enumerate(CANONICAL_POPULATIONS)}
        self.pop_remap = np.array(
            [model_to_canonical[pop] for pop in self.population_order],
            dtype=np.int32,
        )


@dataclass
class ChromosomeResult:
    """LAI result for a single chromosome."""

    chrom: int
    n_windows: int
    hap0_ancestry: np.ndarray  # (W,) canonical population indices
    hap1_ancestry: np.ndarray  # (W,) canonical population indices
    hap0_probs: np.ndarray  # (W, 7) probabilities in canonical order
    hap1_probs: np.ndarray  # (W, 7) probabilities in canonical order
    window_positions: list[tuple[int, int]]  # (start_pos, end_pos) per window


def load_gnomix_model(model_dir: Path) -> GnomixModel:
    """Load a re-exported Gnomix model from a chromosome directory.

    Args:
        model_dir: Path to chromosome model directory containing
            metadata.npz, base_coefs.npz, and smoother.json.

    Returns:
        Loaded GnomixModel ready for inference.
    """
    meta = np.load(model_dir / "metadata.npz", allow_pickle=False)
    base = np.load(model_dir / "base_coefs.npz", allow_pickle=False)

    population_order = [str(p) for p in meta["population_order"]]

    return GnomixModel(
        chrom=int(str(model_dir.name).replace("chr", "")),
        n_snps=int(meta["C"]),
        n_pops=int(meta["A"]),
        n_windows=int(meta["W"]),
        window_size=int(meta["M"]),
        smoother_context=int(meta["S"]),
        context=int(meta["context"]),
        snp_pos=meta["snp_pos"],
        snp_ref=meta["snp_ref"].astype(str),
        snp_alt=meta["snp_alt"].astype(str),
        population_order=population_order,
        coefs=base["coefs"].astype(np.float64),
        intercepts=base["intercepts"].astype(np.float64),
        window_n_features=base["window_n_features"].astype(np.int32),
        smoother_path=model_dir / "smoother.json",
    )


def run_inference(
    model: GnomixModel,
    hap0: np.ndarray,
    hap1: np.ndarray,
) -> ChromosomeResult:
    """Run Gnomix inference on two phased haplotype vectors.

    Args:
        model: Loaded GnomixModel for this chromosome.
        hap0: First haplotype vector, shape (C,), values 0 or 1.
        hap1: Second haplotype vector, shape (C,), values 0 or 1.

    Returns:
        ChromosomeResult with per-window ancestry calls per haplotype.
    """
    import xgboost as xgb

    haps = np.stack([hap0, hap1], axis=0)  # (2, C)

    # Step 1: Mirror-reflect context SNPs at chromosome edges
    if model.context > 0:
        haps = _pad_mirror(haps, model.context, axis=1)

    # Step 2: Base model — per-window logistic regression
    n_haps = 2
    base_probs = np.zeros((n_haps, model.n_windows, model.n_pops), dtype=np.float64)

    for w in range(model.n_windows):
        nf = int(model.window_n_features[w])
        start = w * model.window_size
        end = start + nf
        X = haps[:, start:end].astype(np.float64)  # (2, nf)

        coef = model.coefs[w, :, :nf]  # (A, nf)
        intercept = model.intercepts[w, :]  # (A,)

        logits = X @ coef.T + intercept  # (2, A)
        base_probs[:, w, :] = _softmax(logits)

    # Step 3: Smoother — XGBoost CRF
    S = model.smoother_context
    pad = (S + 1) // 2

    smoother_probs = np.zeros_like(base_probs)
    booster = xgb.Booster()
    booster.load_model(str(model.smoother_path))

    for h in range(n_haps):
        probs = base_probs[h]  # (W, A)
        padded = _pad_mirror(probs, pad, axis=0)  # (W + 2*pad, A)
        features = _build_smoother_features(padded, S, model.n_windows)  # (W, S*A)
        dmat = xgb.DMatrix(features)
        preds = booster.predict(dmat)  # (W * A,) or (W, A)
        if preds.ndim == 1:
            preds = preds.reshape(model.n_windows, model.n_pops)
        smoother_probs[h] = _softmax(preds)

    # Step 4: Remap population indices to canonical order
    canonical_probs = np.zeros(
        (n_haps, model.n_windows, len(CANONICAL_POPULATIONS)), dtype=np.float64
    )
    for model_idx, canonical_idx in enumerate(model.pop_remap):
        canonical_probs[:, :, canonical_idx] = smoother_probs[:, :, model_idx]

    hap0_ancestry = np.argmax(canonical_probs[0], axis=1)
    hap1_ancestry = np.argmax(canonical_probs[1], axis=1)

    window_positions = _model_window_positions(model)

    return ChromosomeResult(
        chrom=model.chrom,
        n_windows=model.n_windows,
        hap0_ancestry=hap0_ancestry,
        hap1_ancestry=hap1_ancestry,
        hap0_probs=canonical_probs[0],
        hap1_probs=canonical_probs[1],
        window_positions=window_positions,
    )


def _model_window_positions(model: GnomixModel) -> list[tuple[int, int]]:
    """Return Gnomix's inclusive marker-span coordinates for every window.

    Gnomix defines ``W = C // M``. Regular windows contain ``M`` markers, while
    the final window consumes every marker from ``(W - 1) * M`` through
    ``C - 1``. The latter therefore contains ``M + (C % M)`` markers when the
    chromosome has a remainder.
    """
    positions: list[tuple[int, int]] = []
    for window in range(model.n_windows):
        start_snp = window * model.window_size
        end_snp = (
            model.n_snps - 1
            if window == model.n_windows - 1
            else (window + 1) * model.window_size - 1
        )
        start_pos = int(model.snp_pos.item(start_snp))
        end_pos = int(model.snp_pos.item(end_snp))
        positions.append((start_pos, end_pos))
    return positions


def _softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax along the last axis."""
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


def _pad_mirror(arr: np.ndarray, pad: int, axis: int) -> np.ndarray:
    """Mirror-reflect padding along the given axis.

    Matches Gnomix's ``np.flip`` padding at chromosome edges.
    """
    if pad <= 0:
        return arr

    slices_left: list[slice | int] = [slice(None)] * arr.ndim
    slices_right: list[slice | int] = [slice(None)] * arr.ndim
    slices_left[axis] = slice(pad - 1, None, -1)
    slices_right[axis] = slice(-2, -pad - 2, -1)

    left = arr[tuple(slices_left)]
    right = arr[tuple(slices_right)]
    return np.concatenate([left, arr, right], axis=axis)


def _build_smoother_features(
    padded_probs: np.ndarray,
    S: int,
    n_windows: int,
) -> np.ndarray:
    """Build smoother input features by concatenating S neighboring windows.

    Args:
        padded_probs: Mirror-padded probability array, shape (W + 2*pad, A).
        S: Smoother context window size.
        n_windows: Number of actual windows (W).

    Returns:
        Feature matrix of shape (W, S * A).
    """
    pad = (S + 1) // 2
    A = padded_probs.shape[1]
    features = np.zeros((n_windows, S * A), dtype=np.float64)

    for w in range(n_windows):
        center = w + pad
        start = center - S // 2
        end = start + S
        window_slice = padded_probs[start:end, :]  # (S, A)
        features[w] = window_slice.ravel()

    return features
