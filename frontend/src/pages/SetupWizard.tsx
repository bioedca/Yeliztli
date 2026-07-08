/** Setup wizard — multi-step first-run configuration.
 *
 * P1-19a: Wizard shell + Step 1 (global disclaimer).
 * P1-19b: Step 2 (import from backup).
 * P1-19c: Step 3 (storage path + disk space).
 * P1-19e: Step 4 (external service credentials).
 * P1-19f: Step 5 (download databases).
 * P1-19g: Step 6 (upload sample + redirect to dashboard).
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSetupStatus } from '@/api/setup'
import DisclaimerStep from '@/components/setup/DisclaimerStep'
import ImportBackupStep from '@/components/setup/ImportBackupStep'
import CredentialsStep from '@/components/setup/CredentialsStep'
import DatabasesStep from '@/components/setup/DatabasesStep'
import StorageStep from '@/components/setup/StorageStep'
import UploadStep from '@/components/setup/UploadStep'
import WizardStepper, { type WizardStep } from '@/components/setup/WizardStepper'
import Logo from '@/components/layout/Logo'

/** All wizard steps (P1-19a through P1-19g). */
const WIZARD_STEPS: WizardStep[] = [
  { id: 'disclaimer', label: 'Welcome' },
  { id: 'backup', label: 'Import' },
  { id: 'storage', label: 'Storage' },
  { id: 'credentials', label: 'Services' },
  { id: 'databases', label: 'Databases' },
  { id: 'upload', label: 'Upload' },
]

/** Index of the Databases step — the recovery destination when the dashboard is
 *  blocked because a required, downloadable database is not integrity-ready. */
const DATABASES_STEP_INDEX = WIZARD_STEPS.findIndex((s) => s.id === 'databases')

const MAX_STEP_INDEX = WIZARD_STEPS.length - 1
/** sessionStorage key holding the current wizard step, so a same-session reload
 *  resumes where the user was instead of bouncing back to the first step. */
const STEP_STORAGE_KEY = 'yeliztli.setupWizard.step'

function readStoredStep(): number {
  try {
    const raw = sessionStorage.getItem(STEP_STORAGE_KEY)
    const n = raw == null ? Number.NaN : Number.parseInt(raw, 10)
    return Number.isInteger(n) && n >= 0 && n <= MAX_STEP_INDEX ? n : 0
  } catch {
    return 0
  }
}

export default function SetupWizard() {
  const navigate = useNavigate()
  const { data: status, isLoading } = useSetupStatus()
  // Resume at the step the user last reached this session (survives a reload).
  const [currentStep, setCurrentStep] = useState(readStoredStep)

  // Persist the current step so a same-session reload resumes here.
  useEffect(() => {
    try {
      sessionStorage.setItem(STEP_STORAGE_KEY, String(currentStep))
    } catch {
      /* sessionStorage unavailable (e.g. private mode) — resume is best-effort. */
    }
  }, [currentStep])

  // If setup is already complete, clear the resume hint and go to the dashboard.
  useEffect(() => {
    if (status && !status.needs_setup) {
      try {
        sessionStorage.removeItem(STEP_STORAGE_KEY)
      } catch {
        /* ignore */
      }
      navigate('/', { replace: true })
    }
  }, [status, navigate])

  // Keep the step consistent with status: never sit on the disclaimer once it's
  // accepted, and never show a post-disclaimer step (e.g. a stale resumed one)
  // while the disclaimer is still unaccepted.
  useEffect(() => {
    // Once setup is complete the redirect effect owns the transition (and clears
    // the resume hint); clamping here would setCurrentStep and let the persist
    // effect re-write the key right after it was cleared.
    if (!status || !status.needs_setup) return
    if (!status.disclaimer_accepted && currentStep !== 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCurrentStep(0)
    } else if (status.disclaimer_accepted && currentStep === 0) {
      setCurrentStep(1)
    }
  }, [status, currentStep])

  const handleDisclaimerAccepted = useCallback(() => {
    setCurrentStep(1)
  }, [])

  const handleNext = useCallback(() => {
    setCurrentStep((prev) => Math.min(WIZARD_STEPS.length - 1, prev + 1))
  }, [])

  const handleBack = useCallback(() => {
    setCurrentStep((prev) => Math.max(0, prev - 1))
  }, [])

  // Dashboard hand-off used by the Import "Go to Dashboard" and the Upload
  // step. Never lands on a broken dashboard: only navigates to / when every
  // required, downloadable DB is integrity-ready (the same backend gate that
  // drives needs_setup); otherwise it routes the user to the Databases
  // recovery step instead of silently going to a non-functional dashboard.
  const goToDashboardOrRecover = useCallback(() => {
    if (status?.required_dbs_ready) {
      navigate('/', { replace: true })
    } else {
      setCurrentStep(DATABASES_STEP_INDEX)
    }
  }, [status?.required_dbs_ready, navigate])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="flex flex-col items-center gap-4">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent" />
          <p className="text-sm text-muted-foreground">Checking setup status...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary">
              <Logo decorative className="h-5 w-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-foreground">Yeliztli</h1>
              <p className="text-xs text-muted-foreground">Setup Wizard</p>
            </div>
          </div>
        </div>
      </header>

      {/* Stepper */}
      <div className="mx-auto max-w-3xl px-6 py-6">
        <WizardStepper steps={WIZARD_STEPS} currentStep={currentStep} />
      </div>

      {/* Step content */}
      <main className="mx-auto max-w-xl px-6 pb-16">
        {currentStep === 0 && (
          <DisclaimerStep onAccepted={handleDisclaimerAccepted} />
        )}

        {currentStep === 1 && (
          <ImportBackupStep
            onNext={handleNext}
            onBack={handleBack}
            onSkipToEnd={goToDashboardOrRecover}
          />
        )}

        {currentStep === 2 && (
          <StorageStep onNext={handleNext} onBack={handleBack} />
        )}

        {currentStep === 3 && (
          <CredentialsStep onNext={handleNext} onBack={handleBack} />
        )}

        {currentStep === 4 && (
          <DatabasesStep onNext={handleNext} onBack={handleBack} />
        )}

        {currentStep === 5 && (
          <UploadStep onBack={handleBack} onComplete={goToDashboardOrRecover} />
        )}
      </main>
    </div>
  )
}
