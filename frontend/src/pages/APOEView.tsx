/** APOE dedicated page with genotype card, findings, and opt-in gate (P3-22d).
 *
 * Layout:
 * 1. Page header (Brain icon, title, subtitle)
 * 2. Gate component (if not acknowledged) OR genotype card + finding cards
 *    (if acknowledged)
 *
 * The genotype card is rendered only after the gate is acknowledged: APOE ε4
 * status is itself the sensitive Alzheimer-risk disclosure, so it must not be
 * shown ahead of the gate (issue #46). The backend likewise withholds the ε4
 * fields until acknowledgment.
 *
 * The gate is non-dismissible: user must actively choose "Show Results"
 * or "Skip". Declining navigates back to the dashboard.
 *
 * PRD P3-22d: APOE UI — dedicated page + gate component.
 */

import { useSearchParams, useNavigate } from "react-router-dom"
import { Brain, Loader2, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { parseSampleId } from "@/lib/format"
import PageLoading from "@/components/ui/PageLoading"
import PageError from "@/components/ui/PageError"
import PageEmpty from "@/components/ui/PageEmpty"
import {
  useAPOEDisclaimer,
  useAPOEGateStatus,
  useAcknowledgeAPOEGate,
  useAPOEGenotype,
  useAPOEFindings,
} from "@/api/apoe"
import APOEGate from "@/components/apoe-gate/APOEGate"
import APOEGenotypeCard from "@/components/apoe-gate/APOEGenotypeCard"
import APOEFindingCard from "@/components/apoe-gate/APOEFindingCard"
import APOECaveats from "@/components/apoe-gate/APOECaveats"

export default function APOEView() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const sampleId = parseSampleId(searchParams.get("sample_id"))

  const disclaimerQuery = useAPOEDisclaimer()
  const gateStatusQuery = useAPOEGateStatus(sampleId)
  const genotypeQuery = useAPOEGenotype(sampleId)
  const acknowledgeMutation = useAcknowledgeAPOEGate()

  const gateAcknowledged = gateStatusQuery.data?.acknowledged === true
  const findingsQuery = useAPOEFindings(sampleId, gateAcknowledged)

  // No sample selected
  if (sampleId == null) {
    return (
      <div className="p-6">
        <h1 className="text-2xl font-bold mb-4">APOE</h1>
        <PageEmpty icon={Brain} title="Select a sample to view APOE results." />
      </div>
    )
  }

  const isLoading = genotypeQuery.isLoading || gateStatusQuery.isLoading
  const hasError = genotypeQuery.isError || gateStatusQuery.isError

  const handleAccept = () => {
    acknowledgeMutation.mutate(sampleId, {
      onError: (error) => {
        console.error("Failed to acknowledge APOE gate:", error)
      },
    })
  }

  const handleDecline = () => {
    navigate(`/?sample_id=${sampleId}`)
  }

  return (
    <div className="p-6">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <div
          className={cn(
            "flex h-10 w-10 items-center justify-center rounded-lg",
            "bg-primary/10 text-primary",
          )}
        >
          <Brain className="h-5 w-5" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">APOE</h1>
          <p className="text-sm text-muted-foreground">
            APOE genotype with cardiovascular, Alzheimer's, and lipid/dietary findings
          </p>
        </div>
      </div>

      {/* Loading state */}
      {isLoading && <PageLoading message="Loading APOE data..." />}

      {/* Error state */}
      {hasError && !isLoading && (
        <PageError
          message={
            genotypeQuery.error instanceof Error
              ? genotypeQuery.error.message
              : gateStatusQuery.error instanceof Error
                ? gateStatusQuery.error.message
                : "An unexpected error occurred."
          }
          onRetry={() => {
            genotypeQuery.refetch()
            gateStatusQuery.refetch()
          }}
        />
      )}

      {/* Main content */}
      {!isLoading && !hasError && (
        <div className="space-y-6">
          {/* Gate (not acknowledged) OR genotype card + findings (acknowledged).
              The genotype card reveals ε4 status, so it is gated too (issue #46). */}
          {gateAcknowledged ? (
            <>
              {/* ── Genotype card (gate acknowledged) ── */}
              {genotypeQuery.data && (
                <section aria-label="APOE genotype">
                  <APOEGenotypeCard genotype={genotypeQuery.data} />
                </section>
              )}

              {/* ── Findings (gate acknowledged) ── */}
              <section aria-label="APOE findings">
                <h2 className="text-lg font-semibold mb-3">Findings</h2>
                <p className="text-sm text-muted-foreground mb-4">
                  Detailed APOE-associated risk assessments based on your genotype
                </p>

                {findingsQuery.isLoading && (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                )}

                {findingsQuery.isError && (
                  <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6">
                    <div className="flex items-start gap-3">
                      <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                      <div>
                        <p className="font-medium text-destructive">Failed to load findings</p>
                        <p className="text-sm text-muted-foreground mt-1">
                          {findingsQuery.error instanceof Error
                            ? findingsQuery.error.message
                            : "An unexpected error occurred."}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {findingsQuery.data && findingsQuery.data.items.length > 0 && (
                  <APOECaveats findings={findingsQuery.data.items} />
                )}

                {findingsQuery.data && findingsQuery.data.items.length > 0 ? (
                  <div className="space-y-4" data-testid="apoe-findings-list">
                    {findingsQuery.data.items.map((finding) => (
                      <APOEFindingCard key={finding.category} finding={finding} />
                    ))}
                  </div>
                ) : findingsQuery.data && findingsQuery.data.items.length === 0 ? (
                  <PageEmpty
                    icon={Brain}
                    title="No APOE findings available."
                    description="Run the APOE analysis first."
                  />
                ) : null}
              </section>
            </>
          ) : (
            /* ── Gate (not yet acknowledged) ── */
            <section aria-label="APOE disclosure gate">
              {disclaimerQuery.data && (
                <APOEGate
                  disclaimer={disclaimerQuery.data}
                  onAccept={handleAccept}
                  onDecline={handleDecline}
                  isAcknowledging={acknowledgeMutation.isPending}
                />
              )}

              {disclaimerQuery.isLoading && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}

              {disclaimerQuery.isError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-6">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                    <div>
                      <p className="font-medium text-destructive">Failed to load disclaimer</p>
                      <p className="text-sm text-muted-foreground mt-1">
                        {disclaimerQuery.error instanceof Error
                          ? disclaimerQuery.error.message
                          : "An unexpected error occurred."}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {acknowledgeMutation.isError && (
                <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 mt-4">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="h-5 w-5 text-destructive mt-0.5 shrink-0" />
                    <p className="text-sm text-destructive">
                      Failed to acknowledge gate. Please try again.
                    </p>
                  </div>
                </div>
              )}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
