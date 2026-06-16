/** Dashboard — main landing page with status bar, module cards, findings, and QC (P1-20).
 *
 * Layout: status bar (40px) → module cards grid → high-confidence findings → collapsible QC.
 * When no sample is active, shows upload prompt.
 */

import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQueries } from '@tanstack/react-query'
import FileUpload from '@/components/upload/FileUpload'
import StatusBar from '@/components/dashboard/StatusBar'
import AnnotationPanel from '@/components/dashboard/AnnotationPanel'
import ModuleCardsGrid from '@/components/dashboard/ModuleCardsGrid'
import FindingsPreview from '@/components/dashboard/FindingsPreview'
import QualityControl from '@/components/dashboard/QualityControl'
import StaleSampleGate from '@/components/layout/StaleSampleGate'
import AppUpdateBanner from '@/components/layout/AppUpdateBanner'
import { PostMergeRewatchModal } from '@/components/individuals/PostMergeRewatchModal'
import { useSamples } from '@/api/samples'
import { useIndividuals, individualsKeys } from '@/api/individuals'
import { useTotalVariantCount, useQCStats } from '@/api/variants'
import { useQCMetrics } from '@/api/qc'
import { parseSampleId } from '@/lib/format'
import { Upload, User } from 'lucide-react'
import PageLoading from '@/components/ui/PageLoading'
import type { IndividualDetail } from '@/types/individuals'

export default function Dashboard() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const activeSampleId = parseSampleId(searchParams.get('sample_id'))

  // Plan §10.7 redirect→modal hand-off. MergeWizard appends
  // `post_merge=1` (+ optional `job_id` for the SSE gate) when it
  // redirects to the new sample's dashboard; we render the modal here
  // and clear the params on dismiss so refresh doesn't re-open it.
  const postMergeFlag = searchParams.get('post_merge') === '1'
  const postMergeJobId = searchParams.get('job_id') || null
  const closePostMergeModal = () => {
    const next = new URLSearchParams(searchParams)
    next.delete('post_merge')
    next.delete('job_id')
    navigate({ search: next.toString() }, { replace: true })
  }

  const { data: samples, isLoading } = useSamples()
  const activeSample = samples?.find((s) => s.id === activeSampleId)

  const { data: variantCount } = useTotalVariantCount(activeSampleId)
  const { data: qcStats } = useQCStats(activeSampleId)
  const { data: qcMetrics } = useQCMetrics(activeSampleId)

  // ── Two-level context chip (Plan §9.5) ───────────────────
  // Walk loaded individual-detail caches to discover which individual
  // (if any) owns the active sample. Reuses individualsKeys.detail(id)
  // so this fans out exactly the same fetches as the top-nav selector.
  const { data: individuals } = useIndividuals()
  const detailQueries = useQueries({
    queries: (individuals ?? []).map((ind) => ({
      queryKey: individualsKeys.detail(ind.id),
      queryFn: async (): Promise<IndividualDetail> => {
        const res = await fetch(`/api/individuals/${ind.id}`)
        if (!res.ok) throw new Error(`Failed to fetch individual ${ind.id}`)
        return (await res.json()) as IndividualDetail
      },
      enabled: activeSampleId != null,
    })),
  })
  const owningIndividual =
    activeSampleId == null
      ? null
      : (detailQueries
          .map((q) => q.data as IndividualDetail | undefined)
          .find((d) => d?.linked_samples.some((s) => s.id === activeSampleId)) ??
          null)

  // ── Loading state: avoid flash of upload prompt ───────────

  if (isLoading && activeSampleId) {
    return <PageLoading message="Loading dashboard..." />
  }

  // ── No active sample: show upload prompt ──────────────────

  if (!activeSample) {
    return (
      <div className="p-6 max-w-4xl mx-auto space-y-6">
        <AppUpdateBanner />
        <div>
          <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
          <div className="mt-8 flex flex-col items-center text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
              <Upload className="h-7 w-7 text-primary" />
            </div>
            <h2 className="mt-4 text-lg font-semibold text-foreground">
              Get Started
            </h2>
            <p className="mt-2 text-sm text-muted-foreground max-w-md">
              Upload a 23andMe or AncestryDNA raw data file to begin exploring
              your genome.
            </p>
            <div className="mt-6 w-full max-w-md">
              <FileUpload />
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Active sample: full dashboard layout ──────────────────

  return (
    <StaleSampleGate>
      <div className="p-6 max-w-5xl mx-auto space-y-6">
        {/* App-update + LAI degraded-coverage advisories (Plan §6.7, Step 23/29) */}
        <AppUpdateBanner />

        {/* Two-level context chip (Plan §9.5) */}
        {owningIndividual && (
          <div
            className="flex items-center gap-1.5"
            aria-label="Active context"
            data-testid="dashboard-context-chip"
          >
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground">
              <User className="h-3 w-3" />
              <span>Viewing:</span>
              <Link
                to={`/individuals/${owningIndividual.id}`}
                className="font-medium text-foreground hover:text-primary hover:underline"
              >
                {owningIndividual.display_name}
              </Link>
              <span aria-hidden="true">/</span>
              <span className="font-medium text-foreground">
                {activeSample.name}
              </span>
            </span>
          </div>
        )}

        {/* Status bar */}
        <StatusBar
          sample={activeSample}
          variantCount={variantCount ?? null}
        />

        {/* Annotation panel */}
        <AnnotationPanel sampleId={activeSample.id} variantCount={variantCount ?? null} />

        {/* Module cards grid */}
        <ModuleCardsGrid sampleId={activeSample.id} />

        {/* High-confidence findings */}
        <FindingsPreview sampleId={activeSample.id} />

        {/* Collapsible QC */}
        <QualityControl
          variantCount={variantCount ?? null}
          qcStats={qcStats ?? null}
          qcMetrics={qcMetrics ?? null}
        />
      </div>

      {postMergeFlag && activeSampleId != null && (
        <PostMergeRewatchModal
          mergedSampleId={activeSampleId}
          jobId={postMergeJobId}
          onClose={closePostMergeModal}
        />
      )}
    </StaleSampleGate>
  )
}
