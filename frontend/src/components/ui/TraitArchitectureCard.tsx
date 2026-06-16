/** Polygenic trait-architecture education card (SW-A2 / roadmap #30).
 *
 * A static, collapsible explainer shown alongside polygenic scores so a
 * percentile is never read as a deterministic prediction:
 *   - twin > SNP > PRS heritability (most heritability is missing);
 *   - cross-ancestry portability falls with genetic distance (Ding 2023, r≈−0.95);
 *   - calibration is not accuracy.
 *
 * §12.4 — educational only; changes no score, percentile, CI, or evidence level.
 *
 * Canonical source of the three facts + citation: the backend
 * `PRS_TRAIT_ARCHITECTURE` block in `backend/analysis/prs.py` (attached to every
 * PRS finding's `detail_json`). This card is the **reader-facing register** of
 * that same content — the substantive claims (h²_twin > h²_SNP > h²_PRS; Ding
 * 2023 r≈−0.95; calibration ≠ accuracy) and the Ding citation are kept identical
 * to the backend block; only the surrounding prose is friendlier. The card is
 * section-level (not per-finding), so it intentionally renders the static copy
 * rather than threading per-finding detail_json through the API/types. The
 * citation line below embeds the canonical `PRS_TRAIT_ARCHITECTURE["citation"]`
 * string verbatim, and `tests/backend/test_trait_architecture_parity.py` is a
 * cross-stack guard that fails if this card drifts from the backend block (#574),
 * so the two copies can no longer silently diverge.
 */

import { BookOpen } from "lucide-react"

export default function TraitArchitectureCard() {
  return (
    <details
      className="mt-3 rounded-md border border-border/60 bg-muted/30 px-3 py-2"
      data-testid="trait-architecture-card"
    >
      <summary className="flex cursor-pointer items-center gap-2 text-xs font-medium text-foreground">
        <BookOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        How to read a polygenic score
      </summary>
      <div className="mt-2 space-y-2 text-xs text-muted-foreground">
        <p>
          <span className="font-medium text-foreground">Most heritability is missing.</span>{" "}
          Twin-study heritability is larger than SNP heritability, which is larger than the
          variance this score explains (h²_twin {">"} h²_SNP {">"} h²_PRS). A polygenic score
          captures only a fraction of even the heritable part of a trait.
        </p>
        <p>
          <span className="font-medium text-foreground">Accuracy drops across ancestries.</span>{" "}
          Polygenic-score accuracy falls roughly linearly with genetic distance from the
          score's training population (Ding et al., Nature 2023; Pearson r ≈ −0.95 across 84
          traits), so a score derived mainly in one population can be miscalibrated in another.
        </p>
        <p>
          <span className="font-medium text-foreground">Calibration is not accuracy.</span>{" "}
          Even a correctly ranked percentile does not predict your individual outcome — most
          trait variation is environmental and non-PRS genetic.
        </p>
        <p className="text-[11px] italic">
          Ding et al., Nature 618:774-781 (2023); doi:10.1038/s41586-023-06079-4 — educational
          context only.
        </p>
      </div>
    </details>
  )
}
