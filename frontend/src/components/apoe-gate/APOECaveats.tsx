/** Gated APOE source-discrepancy and array-reliability caveats. */

import { AlertTriangle, FlaskConical, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"
import type {
  APOEArrayReliability,
  APOEFinding,
  APOESourceDiscrepancy,
} from "@/types/apoe"

interface APOECaveatsProps {
  findings: APOEFinding[]
}

function formatDiplotype(diplotype: string | null): string {
  return diplotype?.replace(/e(\d)/g, "ε$1") ?? "indeterminate ε genotype"
}

function formatE4Status(e4Present: boolean | null): string {
  if (e4Present === true) {
    return "ε4 present"
  }
  if (e4Present === false) {
    return "ε4 absent"
  }
  return "ε4 status indeterminate"
}

function collectSourceDiscrepancies(findings: APOEFinding[]): APOESourceDiscrepancy[] {
  const seen = new Set<string>()
  const discrepancies: APOESourceDiscrepancy[] = []

  for (const finding of findings) {
    for (const discrepancy of finding.detail_json.source_discrepancies ?? []) {
      const key = `${discrepancy.rsid}|${discrepancy.kept_genotype ?? ""}|${JSON.stringify(discrepancy.calls)}`
      if (seen.has(key)) {
        continue
      }
      seen.add(key)
      discrepancies.push(discrepancy)
    }
  }

  return discrepancies
}

function findArrayReliability(findings: APOEFinding[]): APOEArrayReliability | undefined {
  return findings
    .map((finding) => finding.detail_json.array_reliability)
    .find((reliability) => reliability?.caveat)
}

export default function APOECaveats({ findings }: APOECaveatsProps) {
  const sourceDiscrepancies = collectSourceDiscrepancies(findings)
  const arrayReliability = findArrayReliability(findings)

  if (sourceDiscrepancies.length === 0 && !arrayReliability) {
    return null
  }

  return (
    <section
      aria-label="APOE source and array reliability caveats"
      className="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-5 text-amber-950 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-100"
      data-testid="apoe-caveats"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-900/60 dark:text-amber-200">
          <AlertTriangle className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1 space-y-5">
          <div>
            <h2 className="text-base font-semibold">APOE source and reliability caveats</h2>
            <p className="mt-1 text-sm text-amber-800 dark:text-amber-200">
              These notes qualify the APOE ε-status shown above and should travel with any
              APOE interpretation.
            </p>
          </div>

          {sourceDiscrepancies.length > 0 && (
            <div className="space-y-3" data-testid="apoe-source-discrepancies">
              <h3 className="text-sm font-semibold">Source discrepancy</h3>
              {sourceDiscrepancies.map((discrepancy) => (
                <div
                  key={`${discrepancy.rsid}-${discrepancy.kept_genotype ?? "unknown"}`}
                  className={cn(
                    "space-y-3 border-l-4 bg-white/70 p-4 dark:bg-black/20",
                    discrepancy.affects_e4_status
                      ? "border-red-500"
                      : "border-amber-500",
                  )}
                  data-testid={`apoe-source-discrepancy-${discrepancy.rsid}`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-semibold">{discrepancy.rsid}</span>
                    {discrepancy.affects_e4_status && (
                      <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800 dark:bg-red-900/50 dark:text-red-200">
                        Changes ε4 status
                      </span>
                    )}
                    {discrepancy.kept_genotype && (
                      <span className="text-xs text-amber-800 dark:text-amber-200">
                        Kept genotype: {discrepancy.kept_genotype}
                      </span>
                    )}
                  </div>

                  {discrepancy.note && (
                    <p className="text-sm leading-relaxed">{discrepancy.note}</p>
                  )}

                  <div className="grid gap-2 sm:grid-cols-2">
                    {discrepancy.calls.map((call) => (
                      <div
                        key={`${discrepancy.rsid}-${call.source}-${call.genotype}`}
                        className="rounded-md border border-amber-200 bg-amber-50/80 p-3 dark:border-amber-800 dark:bg-amber-950/40"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">
                            {call.source}
                          </span>
                          <span className="font-mono text-sm font-semibold">{call.genotype}</span>
                        </div>
                        <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
                          Implies {formatDiplotype(call.implied_diplotype)} (
                          {formatE4Status(call.e4_present)})
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {arrayReliability && (
            <div
              className="space-y-3 border-t border-amber-200 pt-4 dark:border-amber-800"
              data-testid="apoe-array-reliability"
            >
              <div className="flex flex-wrap items-center gap-2">
                <FlaskConical className="h-4 w-4 text-amber-700 dark:text-amber-300" />
                <h3 className="text-sm font-semibold">Array reliability</h3>
                {arrayReliability.confirm_in_clia_recommended && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/60 dark:text-amber-200">
                    <ShieldCheck className="h-3 w-3" />
                    CLIA confirmation recommended
                  </span>
                )}
              </div>

              <p className="text-sm leading-relaxed">{arrayReliability.caveat}</p>

              {arrayReliability.concordance_with_direct_genotyping && (
                <p className="text-xs text-amber-800 dark:text-amber-200">
                  Direct-genotyping concordance:{" "}
                  {arrayReliability.concordance_with_direct_genotyping}
                </p>
              )}

              {arrayReliability.pmids && arrayReliability.pmids.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {arrayReliability.pmids.map((pmid) => (
                    <a
                      key={pmid}
                      href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded-full bg-white/80 px-2 py-0.5 text-xs text-amber-800 underline-offset-2 hover:underline dark:bg-black/20 dark:text-amber-200"
                    >
                      PMID:{pmid}
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
