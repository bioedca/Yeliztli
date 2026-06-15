/** Sample types matching backend Pydantic models (P1-13, P1-16, P4-21f). */

export interface Sample {
  id: number
  name: string
  db_path: string
  file_format: string | null
  file_hash: string | null
  notes: string | null
  date_collected: string | null
  source: string | null
  extra: Record<string, unknown> | null
  created_at: string | null
  updated_at: string | null
}

export interface SampleUpdate {
  name?: string
  notes?: string
  date_collected?: string
  source?: string
  extra?: Record<string, unknown>
}

export interface IngestResult {
  sample_id: number
  job_id: string
  variant_count: number
  nocall_count: number
  file_format: string
}

/** Merged sample that lists another sample in its merge_provenance sources
 * (AncestryDNA Plan §10.8 / Step 66). Surfaced by
 * ``GET /api/samples/{id}/merged-children`` to drive the delete-cascade
 * confirmation. */
export interface MergedChild {
  id: number
  name: string
}
