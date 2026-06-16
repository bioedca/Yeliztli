export type HetOutlierStatus =
  | 'within_range'
  | 'outlier'
  | 'insufficient_samples'
  | 'insufficient_comparable_samples'

export type SexCheckStatus = 'concordant' | 'discordant' | 'indeterminate'

export interface QCMetrics {
  computed: boolean
  call_rate: number | null
  call_rate_pass: boolean | null
  heterozygosity_rate: number | null
  ti_tv_ratio: number | null
  total_variants: number | null
  called_variants: number | null
  nocall_variants: number | null
  genetic_sex: string | null
  recorded_sex: string | null
  sex_check: SexCheckStatus | null
  het_outlier_z: number | null
  het_outlier_status: HetOutlierStatus | null
}
