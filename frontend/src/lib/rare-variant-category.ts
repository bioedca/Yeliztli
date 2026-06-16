/** Category pill styling + human label for the Rare Variant Finder "Previous
 *  Findings" table (RareVariantsView).
 *
 *  Every category the backend emits (`rare_variant_finder.py`) maps to a friendly
 *  label and a tone, so:
 *    - no raw snake_case dev key ever surfaces (the old `category.replace(/_/g," ")`
 *      printed e.g. "clinvar pathogenic low confidence"), and
 *    - a carried 0★ ClinVar P/LP variant (`clinvar_pathogenic_low_confidence`) gets
 *      its own cautionary amber tone instead of the neutral gray fallback, which made
 *      it visually identical to a low-priority `rare` row (#919).
 */

export interface RareVariantCategoryMeta {
  /** User-facing label for the category pill. */
  label: string
  /** Tailwind background/text classes for the pill. */
  className: string
}

const _MUTED = "bg-muted text-muted-foreground"

const _CATEGORY_META: Record<string, RareVariantCategoryMeta> = {
  clinvar_pathogenic: {
    label: "ClinVar Pathogenic",
    className: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300",
  },
  // 0★ P/LP record (no assertion criteria): real but down-ranked → cautionary amber,
  // distinct from both the bold-red high-confidence tier and the gray `rare` tier.
  clinvar_pathogenic_low_confidence: {
    label: "Pathogenic (low confidence)",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300",
  },
  ensemble_pathogenic: {
    label: "Predicted Pathogenic",
    className: "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300",
  },
  novel: {
    label: "Novel",
    className: "bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300",
  },
  rare: {
    label: "Rare",
    className: _MUTED,
  },
}

/** Title-case a snake_case key as a last resort so an unforeseen future category
 *  still never renders as a raw developer string. */
function _humanize(category: string): string {
  return category
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export function getRareVariantCategoryMeta(category: string): RareVariantCategoryMeta {
  return _CATEGORY_META[category] ?? { label: _humanize(category), className: _MUTED }
}
