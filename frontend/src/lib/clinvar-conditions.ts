/** Format ClinVar `clinvar_conditions` (the raw `CLNDN` field) for display (#832).
 *
 * The backend serves ClinVar's `CLNDN` verbatim: a `|`-delimited list of disease
 * names that also contains ClinVar placeholders (`not provided`, `not specified`)
 * and non-disease pharmacogenomic entries (`… - Efficacy` / `… - Dosage` /
 * `… - Toxicity`). Rendered raw, a clinical-finding card showed the literal `|`
 * separators plus those misleading entries (e.g. CFTR rs78655421 carrier card
 * showing `not provided` and `ivacaftor response - Efficacy` as "conditions").
 *
 * `formatClinvarConditions` splits on `|`, trims, drops the placeholders and the
 * drug-response entries, and de-dupes (case-insensitive, first casing kept). The
 * raw value is left intact in the data layer for `/findings` consumers; this is
 * display-only cleanup. Use `formatClinvarConditionsText` for the comma-joined
 * string the finding cards / variant-detail rows render.
 */

const _PLACEHOLDERS = new Set(["not provided", "not specified"])

// Pharmacogenomic ClinVar entries (drug response), not disease conditions:
// "ivacaftor response - Efficacy", "<drug> - Dosage", "<drug> - Toxicity".
const _DRUG_RESPONSE = /\s-\s(efficacy|dosage|toxicity)$/i

export function formatClinvarConditions(raw: string | null | undefined): string[] {
  if (!raw) return []
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of raw.split("|")) {
    const condition = part.trim()
    if (!condition) continue
    if (_PLACEHOLDERS.has(condition.toLowerCase())) continue
    if (_DRUG_RESPONSE.test(condition)) continue
    const key = condition.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(condition)
  }
  return out
}

/** The cleaned conditions as a comma-joined string (empty when none remain — a
 *  truthy check on the result hides the row for a placeholder/drug-response-only
 *  value). */
export function formatClinvarConditionsText(raw: string | null | undefined): string {
  return formatClinvarConditions(raw).join(", ")
}
