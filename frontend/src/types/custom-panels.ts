/** Custom gene panel API types (P4-11). */

/** A saved custom gene panel. */
interface CustomPanel {
  id: number
  name: string
  description: string
  gene_symbols: string[]
  bed_regions: BedRegion[] | null
  source_type: "gene_list" | "bed"
  gene_count: number
  created_at: string | null
}

/** A BED region from a panel. */
interface BedRegion {
  chrom: string
  start: number
  end: number
  name: string | null
}

/** List of all saved custom panels. */
export interface CustomPanelListResponse {
  items: CustomPanel[]
  total: number
}

/** Response after uploading and saving a panel. */
export interface PanelUploadResponse {
  panel: CustomPanel
  warnings: string[]
}
