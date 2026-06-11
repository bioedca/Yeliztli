/** Opt-in breast absolute-risk overlay (SW-B8).
 *
 * Gated behind explicit consent because it quantifies absolute disease risk.
 * Pre-consent: an explainer + opt-in button (no figures). Post-consent: the SEER
 * population baseline, published carrier penetrance, and a CanRisk handoff. No
 * personalized PRS-derived absolute number is shown (the breast PRS percentile
 * is coverage-limited / withheld).
 */

import { ShieldQuestion, ExternalLink } from "lucide-react"
import { useAbsoluteRisk, useSetAbsoluteRiskConsent } from "@/api/cancer"

export default function AbsoluteRiskOverlay({ sampleId }: { sampleId: number }) {
  const query = useAbsoluteRisk(sampleId)
  const consent = useSetAbsoluteRiskConsent(sampleId)
  const data = query.data

  if (query.isLoading || !data) return null

  return (
    <section aria-label="Breast absolute-risk overlay" className="mt-8" data-testid="absolute-risk">
      <div className="flex items-center gap-2 mb-3">
        <ShieldQuestion className="h-5 w-5 text-primary" aria-hidden="true" />
        <h2 className="text-lg font-semibold">Absolute-risk context (optional)</h2>
      </div>

      {!data.consented ? (
        <div className="rounded-lg border bg-card p-4 max-w-2xl" data-testid="absolute-risk-optin">
          <p className="text-sm text-foreground mb-2">{data.opt_in_prompt}</p>
          <p className="text-xs text-muted-foreground mb-3">{data.disclaimer}</p>
          <button
            type="button"
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={() => consent.mutate(true)}
            disabled={consent.isPending}
            data-testid="absolute-risk-optin-button"
          >
            {consent.isPending ? "Enabling…" : "Show absolute-risk context"}
          </button>
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-4 max-w-2xl space-y-3" data-testid="absolute-risk-overlay">
          {data.population_baseline && (
            <div>
              <p className="text-sm text-foreground">
                Population lifetime risk:{" "}
                <span className="font-semibold">{data.population_baseline.lifetime_risk_pct}%</span>{" "}
                <span className="text-muted-foreground">({data.population_baseline.note})</span>
              </p>
              <p className="text-xs text-muted-foreground">
                Source: {data.population_baseline.source}
              </p>
            </div>
          )}

          {data.has_monogenic && data.monogenic && data.monogenic.length > 0 && (
            <div data-testid="absolute-risk-monogenic">
              <p className="text-sm font-medium text-foreground">Carrier penetrance</p>
              <ul className="text-sm text-foreground list-disc ml-5">
                {data.monogenic.map((m) => (
                  <li key={m.gene}>
                    {m.gene}:{" "}
                    {m.cumulative_risk_to_80_pct != null
                      ? `~${m.cumulative_risk_to_80_pct}% cumulative risk to age 80${m.ci ? ` (95% CI ${m.ci})` : ""}`
                      : (m.note ?? "moderate-to-high penetrance; see a genetics referral")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {data.prs_note && <p className="text-xs text-muted-foreground">{data.prs_note}</p>}

          {data.canrisk && (
            <p className="text-sm">
              <a
                href={data.canrisk.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary underline"
              >
                {data.canrisk.tool} <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>{" "}
              <span className="text-muted-foreground">— {data.canrisk.note}</span>
            </p>
          )}

          <p className="text-xs text-amber-700 dark:text-amber-400">{data.disclaimer}</p>

          <button
            type="button"
            className="text-xs text-muted-foreground underline"
            onClick={() => consent.mutate(false)}
            disabled={consent.isPending}
            data-testid="absolute-risk-optout-button"
          >
            Hide / opt out
          </button>
        </div>
      )}
    </section>
  )
}
