/** Shared PRS test fixtures (SW-B3).
 *
 * Single source of truth for the per-PGS provenance + monogenic-exclusion
 * defaults, imported by the cancer and traits PRS card tests.
 */

/** Provenance + monogenic-exclusion defaults — unset unless overridden. */
export const PRS_PROV_DEFAULTS = {
  pgs_id: null,
  pgs_license: null,
  development_method: null,
  genome_build: null,
  variants_number: null,
  source_url: null,
  monogenic_genes: [] as string[],
  monogenic_carrier_genes: [] as string[],
  monogenic_note: null,
}
