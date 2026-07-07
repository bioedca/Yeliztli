/** In-app tooltip copy for the CADD / REVEL in-silico scores (#1662).
 *
 * CADD and REVEL are shown on the variant surfaces as bare numbers, while
 * SIFT/PolyPhen carry a plain-language label — so a user can't tell from the UI
 * alone whether higher is worse or what the red highlight means. These strings
 * put the direction, scale, and the display threshold on a hover tooltip so the
 * meaning travels with the number, mirroring the published interpretation
 * reference (docs/modules/interpretation-reference.md → "In-silico pathogenicity
 * scores", #1589/#1661: CADD Rentzsch 2019 PMID:30371827; REVEL Ioannidis 2016
 * PMID:27666373). The thresholds are display/attention heuristics, not diagnoses.
 */

export const CADD_TOOLTIP =
  "CADD (Combined Annotation-Dependent Depletion): a phred-scaled deleteriousness " +
  "score — higher = more deleterious (scale ~0–99; 10/20/30 ≈ the top 10%/1%/0.1% " +
  "most deleterious variants). CADD ≥ 20 is highlighted as an attention heuristic, " +
  "not a diagnosis."

export const REVEL_TOOLTIP =
  "REVEL: a missense-specific pathogenicity score from 0 to 1 — higher = more " +
  "likely pathogenic. REVEL ≥ 0.5 is highlighted as an attention heuristic, not a " +
  "diagnosis."

/** Shared affordance classes: signal the label is hoverable (native title tooltip). */
export const SCORE_TOOLTIP_AFFORDANCE =
  "cursor-help underline decoration-dotted decoration-muted-foreground/40 underline-offset-2"
