/**
 * Display mapping for in-silico pathogenicity predictions.
 *
 * The backend stores the raw dbNSFP prediction codes (see
 * `backend/annotation/dbnsfp.py::_parse_dbnsfp_pred`, which keeps the first
 * non-missing per-transcript code). For PolyPhen-2 (`Polyphen2_HVAR_pred`) that
 * is a single character — `D` probably damaging, `P` possibly damaging,
 * `B` benign — NOT the full words. Matching the full words leaves every call in
 * the benign branch and prints the bare letter (issue #680). SIFT is already
 * handled inline as single-char `D`.
 */
export interface PredictionDisplay {
  label: string
  /** Tailwind text-colour classes encoding severity. */
  colorClass: string
}

const RED = "text-red-700 dark:text-red-400"
const AMBER = "text-amber-700 dark:text-amber-400"
const GREEN = "text-green-700 dark:text-green-400"
const NEUTRAL = "text-muted-foreground"

/**
 * Map a PolyPhen-2 prediction (`polyphen2_hsvar_pred`) to a readable label and
 * a severity colour. Accepts the single-char dbNSFP codes `D`/`P`/`B` (the real
 * stored values) and, defensively, their full-word aliases. An unrecognised
 * value is shown verbatim in a neutral colour — never silently coloured benign.
 */
export function polyphen2Display(pred: string): PredictionDisplay {
  switch (pred.trim().toUpperCase()) {
    case "D":
    case "PROBABLY_DAMAGING":
      return { label: "Probably Damaging", colorClass: RED }
    case "P":
    case "POSSIBLY_DAMAGING":
      return { label: "Possibly Damaging", colorClass: AMBER }
    case "B":
    case "BENIGN":
      return { label: "Benign", colorClass: GREEN }
    default:
      return { label: pred.trim().replace(/_/g, " "), colorClass: NEUTRAL }
  }
}
