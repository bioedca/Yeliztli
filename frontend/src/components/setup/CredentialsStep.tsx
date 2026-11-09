/** Step 4: External service credentials.
 *
 * P1-19e: PubMed email (required by NCBI TOS), optional NCBI API key,
 * optional OMIM API key for gene-phenotype enrichment.
 */

import { useCallback, useState } from 'react'
import { useCredentials, useSaveCredentials } from '@/api/setup'
import { cn } from '@/lib/utils'
import type { CredentialsData } from '@/types/setup'
import {
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Key,
  Mail,
} from 'lucide-react'

interface CredentialsStepProps {
  onNext: () => void
  onBack: () => void
}

export default function CredentialsStep({ onNext, onBack }: CredentialsStepProps) {
  const { data: credentials, isLoading } = useCredentials()

  if (isLoading || !credentials) {
    return (
      <div className="space-y-6">
        <CredentialsHeader />
        <div className="flex items-center justify-center py-8">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span className="ml-2 text-sm text-muted-foreground">
            Loading credentials...
          </span>
        </div>
        <ActionButtons onBack={onBack} disabled />
      </div>
    )
  }

  return (
    <CredentialsForm
      initialData={credentials}
      onNext={onNext}
      onBack={onBack}
    />
  )
}

function CredentialsHeader() {
  return (
    <div className="text-center space-y-2">
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
        <Key className="h-7 w-7 text-primary" />
      </div>
      <h2 className="text-xl font-semibold text-foreground">
        External Services
      </h2>
      <p className="text-sm text-muted-foreground">
        Configure credentials for external data services used by Yeliztli.
      </p>
    </div>
  )
}

function ActionButtons({
  onBack,
  disabled,
  isPending,
  onClick,
}: {
  onBack: () => void
  disabled?: boolean
  isPending?: boolean
  onClick?: () => void
}) {
  return (
    <div className="flex gap-3">
      <button
        type="button"
        onClick={onBack}
        className={cn(
          'rounded-lg border border-border px-5 py-2.5 text-sm font-medium',
          'text-foreground hover:bg-accent transition-colors',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
        )}
      >
        Back
      </button>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={cn(
          'flex-1 rounded-lg px-6 py-3 text-sm font-medium transition-all',
          'bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm',
          'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
          disabled && 'opacity-70 cursor-not-allowed',
        )}
      >
        {isPending ? (
          <span className="flex items-center justify-center gap-2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground border-t-transparent" />
            Saving...
          </span>
        ) : (
          <span className="flex items-center justify-center gap-2">
            <ArrowRight className="h-4 w-4" />
            Continue
          </span>
        )}
      </button>
    </div>
  )
}

/** Inner form — rendered only after credentials data is loaded. */
function CredentialsForm({
  initialData,
  onNext,
  onBack,
}: {
  initialData: CredentialsData
  onNext: () => void
  onBack: () => void
}) {
  const saveMutation = useSaveCredentials()

  const [pubmedEmail, setPubmedEmail] = useState(initialData.pubmed_email)
  const [ncbiApiKey, setNcbiApiKey] = useState(initialData.ncbi_api_key)
  const [omimApiKey, setOmimApiKey] = useState(initialData.omim_api_key)

  const isValidEmail = pubmedEmail.trim() !== '' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(pubmedEmail.trim())

  const handleSave = useCallback(async () => {
    try {
      await saveMutation.mutateAsync({
        pubmed_email: pubmedEmail.trim(),
        ncbi_api_key: ncbiApiKey.trim(),
        omim_api_key: omimApiKey.trim(),
      })
      onNext()
    } catch {
      // Error state handled by mutation
    }
  }, [saveMutation, pubmedEmail, ncbiApiKey, omimApiKey, onNext])

  return (
    <div className="space-y-6">
      <CredentialsHeader />

      <div className="space-y-5">
        {/* PubMed Email — required */}
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-primary flex-shrink-0" />
            <span className="text-sm font-medium text-foreground">
              PubMed / NCBI Email
            </span>
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              Required
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            NCBI requires an email address for Entrez API usage per their{' '}
            <a
              href="https://www.ncbi.nlm.nih.gov/home/about/policies/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-0.5"
            >
              Terms of Service
              <ExternalLink className="h-3 w-3" />
            </a>
            . This is used to fetch PubMed literature abstracts for variant evidence.
          </p>
          <input
            type="email"
            value={pubmedEmail}
            onChange={(e) => setPubmedEmail(e.target.value)}
            placeholder="your.email@example.com"
            className={cn(
              'w-full rounded-lg border bg-background py-2.5 px-3 text-sm',
              'text-foreground placeholder:text-muted-foreground',
              'focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent',
              pubmedEmail && !isValidEmail
                ? 'border-destructive'
                : 'border-border',
            )}
            aria-label="PubMed email address"
            aria-required="true"
          />
          {pubmedEmail && !isValidEmail && (
            <p className="text-xs text-destructive-text">
              Please enter a valid email address.
            </p>
          )}
        </div>

        {/* NCBI API Key — optional */}
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Key className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            <span className="text-sm font-medium text-foreground">
              NCBI API Key
            </span>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              Optional
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            An API key increases the NCBI rate limit from 3 to 10 requests per second.
            Get one from your{' '}
            <a
              href="https://www.ncbi.nlm.nih.gov/account/settings/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-0.5"
            >
              NCBI account settings
              <ExternalLink className="h-3 w-3" />
            </a>
            .
          </p>
          <input
            type="text"
            value={ncbiApiKey}
            onChange={(e) => setNcbiApiKey(e.target.value)}
            placeholder="NCBI API key (optional)"
            className={cn(
              'w-full rounded-lg border border-border bg-background py-2.5 px-3 text-sm font-mono',
              'text-foreground placeholder:text-muted-foreground',
              'focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent',
            )}
            aria-label="NCBI API key"
          />
        </div>

        {/* OMIM API Key — optional */}
        <div className="rounded-lg border bg-card p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Key className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            <span className="text-sm font-medium text-foreground">
              OMIM API Key
            </span>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
              Optional
            </span>
          </div>
          <p className="text-xs text-muted-foreground">
            Enables gene-phenotype enrichment beyond the default MONDO/HPO data.
            OMIM is a proprietary database — request an API key from{' '}
            <a
              href="https://www.omim.org/api"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-0.5"
            >
              omim.org
              <ExternalLink className="h-3 w-3" />
            </a>
            .
          </p>
          <input
            type="text"
            value={omimApiKey}
            onChange={(e) => setOmimApiKey(e.target.value)}
            placeholder="OMIM API key (optional)"
            className={cn(
              'w-full rounded-lg border border-border bg-background py-2.5 px-3 text-sm font-mono',
              'text-foreground placeholder:text-muted-foreground',
              'focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent',
            )}
            aria-label="OMIM API key"
          />
        </div>
      </div>

      {/* Error from save */}
      {saveMutation.isError && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/5 p-4 text-center">
          <AlertTriangle className="mx-auto h-5 w-5 text-destructive-text" />
          <p className="mt-2 text-sm text-destructive-text">
            {saveMutation.error instanceof Error
              ? saveMutation.error.message
              : 'Failed to save credentials.'}
          </p>
        </div>
      )}

      <ActionButtons
        onBack={onBack}
        disabled={!isValidEmail || saveMutation.isPending}
        isPending={saveMutation.isPending}
        onClick={handleSave}
      />
    </div>
  )
}
