import { act } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from './test-utils'
import SetupWizard from '@/pages/SetupWizard'
import CredentialsStep from '@/components/setup/CredentialsStep'
import DatabasesStep from '@/components/setup/DatabasesStep'
import DisclaimerStep from '@/components/setup/DisclaimerStep'
import ImportBackupStep from '@/components/setup/ImportBackupStep'
import StorageStep from '@/components/setup/StorageStep'
import UploadStep from '@/components/setup/UploadStep'
import WizardStepper from '@/components/setup/WizardStepper'

const { toastInfoMock } = vi.hoisted(() => ({ toastInfoMock: vi.fn() }))

vi.mock('sonner', () => ({
  toast: { info: toastInfoMock },
  Toaster: () => null,
}))

const mockFetch = vi.fn()
globalThis.fetch = mockFetch

beforeEach(() => {
  mockFetch.mockReset()
  toastInfoMock.mockReset()
})

// ─── Helper to mock API responses ───────────────────────────────────

/**
 * Route DatabasesStep fetches by URL instead of FIFO. DatabasesStep now also
 * fires GET /api/databases/health on mount (resume/partial observability), so
 * strict mockResolvedValueOnce queues desync. This routes by URL and returns an
 * empty health list by default. Check the more-specific paths before the bare
 * /api/databases list path. `download` may set ok:false to simulate a failure.
 */
function routeDatabasesFetch(opts: {
  list: unknown
  download?: { ok?: boolean; status?: number; body?: unknown }
  resume?: { ok?: boolean; status?: number; body?: unknown }
  health?: unknown
  setup?: Record<string, unknown>
}) {
  mockFetch.mockImplementation((url: string) => {
    const u = typeof url === 'string' ? url : String(url)
    if (u.includes('/api/setup/status')) {
      // DatabasesStep now reads the backend's health-verified gate
      // (required_dbs_ready) to enable Continue. Default to not-ready so
      // unrelated download/resume tests keep Continue disabled.
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(opts.setup ?? { required_dbs_ready: false }),
      })
    }
    if (u.includes('/api/databases/health')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(opts.health ?? { databases: [] }),
      })
    }
    if (u.includes('/api/databases/resume')) {
      const r = opts.resume ?? { ok: true, body: {} }
      return Promise.resolve({
        ok: r.ok ?? true,
        status: r.status ?? (r.ok === false ? 409 : 202),
        json: () => Promise.resolve(r.body ?? {}),
      })
    }
    if (u.includes('/api/databases/download')) {
      const d = opts.download ?? { ok: true, body: {} }
      return Promise.resolve({
        ok: d.ok ?? true,
        status: d.status ?? (d.ok === false ? 500 : 200),
        json: () => Promise.resolve(d.body ?? {}),
      })
    }
    if (u.includes('/api/databases')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(opts.list) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function mockSetupStatus(overrides: Record<string, unknown> = {}) {
  return {
    needs_setup: true,
    disclaimer_accepted: false,
    has_databases: false,
    has_samples: false,
    data_dir: '/tmp/test',
    ...overrides,
  }
}

function mockDisclaimer() {
  return {
    title: 'Important Information About Yeliztli',
    text: 'Yeliztli is an educational and research tool.\n\nPlease read carefully.\n\n**Not a diagnostic tool.** This is for education only.',
    accept_label: 'I Understand and Accept',
  }
}

// ─── WizardStepper tests ────────────────────────────────────────────

describe('WizardStepper', () => {
  const steps = [
    { id: 'disclaimer', label: 'Welcome' },
    { id: 'storage', label: 'Storage' },
    { id: 'databases', label: 'Databases' },
  ]

  it('renders all step labels', () => {
    render(<WizardStepper steps={steps} currentStep={0} />)
    expect(screen.getByText('Welcome')).toBeInTheDocument()
    expect(screen.getByText('Storage')).toBeInTheDocument()
    expect(screen.getByText('Databases')).toBeInTheDocument()
  })

  it('marks current step with aria-current', () => {
    render(<WizardStepper steps={steps} currentStep={1} />)
    // Step 2 should have aria-current="step"
    const currentStepEl = document.querySelector('[aria-current="step"]')
    expect(currentStepEl).not.toBeNull()
  })

  it('shows check icon for completed steps', () => {
    render(<WizardStepper steps={steps} currentStep={2} />)
    // Steps 1 and 2 should be completed (have check icons)
    // Step 3 should be current
    const currentStepEl = document.querySelector('[aria-current="step"]')
    expect(currentStepEl).not.toBeNull()
    expect(currentStepEl?.textContent).toBe('3')
  })
})

// ─── DisclaimerStep tests ───────────────────────────────────────────

describe('DisclaimerStep', () => {
  it('renders disclaimer text', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDisclaimer()),
    })

    const onAccepted = vi.fn()
    render(<DisclaimerStep onAccepted={onAccepted} />)

    await waitFor(() => {
      expect(screen.getByText('Important Information About Yeliztli')).toBeInTheDocument()
    })
  })

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {})) // Never resolves
    render(<DisclaimerStep onAccepted={vi.fn()} />)
    // Should show a spinner (the animated div)
    expect(document.querySelector('.animate-spin')).not.toBeNull()
  })

  it('shows error state on fetch failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    })

    render(<DisclaimerStep onAccepted={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/failed to load disclaimer/i)).toBeInTheDocument()
    })
  })

  it('disables checkbox until user scrolls to bottom', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDisclaimer()),
    })

    render(<DisclaimerStep onAccepted={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Important Information About Yeliztli')).toBeInTheDocument()
    })

    const checkbox = screen.getByRole('checkbox')
    expect(checkbox).toBeDisabled()
  })

  it('accept button is disabled when checkbox is unchecked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDisclaimer()),
    })

    render(<DisclaimerStep onAccepted={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Important Information About Yeliztli')).toBeInTheDocument()
    })

    const acceptButton = screen.getByRole('button', { name: /i understand and accept/i })
    expect(acceptButton).toBeDisabled()
  })

  it('renders markdown bold text correctly', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDisclaimer()),
    })

    render(<DisclaimerStep onAccepted={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Not a diagnostic tool.')).toBeInTheDocument()
    })

    // The bold text should be in a <strong> element
    const strongEl = screen.getByText('Not a diagnostic tool.')
    expect(strongEl.tagName).toBe('STRONG')
  })
})

// ─── ImportBackupStep tests ──────────────────────────────────────

function mockDetectExisting(overrides: Record<string, unknown> = {}) {
  return {
    existing_found: false,
    has_config: false,
    has_samples: false,
    has_databases: false,
    data_dir: '/tmp/test',
    ...overrides,
  }
}

describe('ImportBackupStep', () => {
  it('shows import UI when no existing installation', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDetectExisting()),
    })

    const onNext = vi.fn()
    const onBack = vi.fn()
    render(<ImportBackupStep onNext={onNext} onBack={onBack} />)

    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    // Should show drop zone
    expect(screen.getByText(/drop a .tar.gz backup file/i)).toBeInTheDocument()
    // Should show skip button
    expect(screen.getByText(/skip — start fresh/i)).toBeInTheDocument()
  })

  it('shows existing installation when detected', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockDetectExisting({
            existing_found: true,
            has_config: true,
            has_samples: true,
            has_databases: true,
          }),
        ),
    })

    const onNext = vi.fn()
    const onBack = vi.fn()
    const onSkipToEnd = vi.fn()
    render(
      <ImportBackupStep
        onNext={onNext}
        onBack={onBack}
        onSkipToEnd={onSkipToEnd}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Existing Installation Detected')).toBeInTheDocument()
    })

    // Should show detection details
    expect(screen.getByText('Configuration')).toBeInTheDocument()
    expect(screen.getByText('Sample databases')).toBeInTheDocument()
    expect(screen.getByText('Reference databases')).toBeInTheDocument()
  })

  it('shows Go to Dashboard when full installation found', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockDetectExisting({
            existing_found: true,
            has_config: true,
            has_samples: true,
            has_databases: true,
          }),
        ),
    })

    const onSkipToEnd = vi.fn()
    render(
      <ImportBackupStep
        onNext={vi.fn()}
        onBack={vi.fn()}
        onSkipToEnd={onSkipToEnd}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Go to Dashboard')).toBeInTheDocument()
    })
  })

  it('does not show Go to Dashboard when DBs are missing', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockDetectExisting({
            existing_found: true,
            has_config: true,
            has_samples: false,
            has_databases: false,
          }),
        ),
    })

    render(
      <ImportBackupStep
        onNext={vi.fn()}
        onBack={vi.fn()}
        onSkipToEnd={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Existing Installation Detected')).toBeInTheDocument()
    })

    expect(screen.queryByText('Go to Dashboard')).not.toBeInTheDocument()
  })

  it('calls onBack when Back button is clicked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDetectExisting()),
    })

    const onBack = vi.fn()
    render(<ImportBackupStep onNext={vi.fn()} onBack={onBack} />)

    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Back'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('calls onNext when Skip is clicked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDetectExisting()),
    })

    const onNext = vi.fn()
    render(<ImportBackupStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText(/skip — start fresh/i))
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('shows loading state while detecting', () => {
    mockFetch.mockReturnValue(new Promise(() => {})) // Never resolves
    render(<ImportBackupStep onNext={vi.fn()} onBack={vi.fn()} />)

    expect(
      screen.getByText(/checking for existing installation/i),
    ).toBeInTheDocument()
  })

  it('has accessible drop zone', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDetectExisting()),
    })

    render(<ImportBackupStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Import from Backup')).toBeInTheDocument()
    })

    const dropZone = screen.getByRole('button', {
      name: /select backup archive/i,
    })
    expect(dropZone).toBeInTheDocument()
    expect(dropZone).toHaveAttribute('tabindex', '0')
  })
})

// ─── SetupWizard integration tests ──────────────────────────────────

describe('SetupWizard', () => {
  it('shows loading state while checking setup status', () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    render(<SetupWizard />)
    expect(screen.getByText(/checking setup status/i)).toBeInTheDocument()
  })

  it('shows wizard with stepper and disclaimer step', async () => {
    // Mock setup status
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockSetupStatus()),
      })
      // Mock disclaimer
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockDisclaimer()),
      })

    render(<SetupWizard />)

    await waitFor(() => {
      expect(screen.getByText('Yeliztli')).toBeInTheDocument()
      expect(screen.getByText('Setup Wizard')).toBeInTheDocument()
    })

    // Stepper should show all steps
    expect(screen.getByText('Welcome')).toBeInTheDocument()
    expect(screen.getByText('Import')).toBeInTheDocument()
    expect(screen.getByText('Storage')).toBeInTheDocument()
  })

  it('shows all 6 wizard step labels in stepper', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockSetupStatus()),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockDisclaimer()),
      })

    render(<SetupWizard />)

    await waitFor(() => {
      expect(screen.getByText('Welcome')).toBeInTheDocument()
    })

    expect(screen.getByText('Import')).toBeInTheDocument()
    expect(screen.getByText('Storage')).toBeInTheDocument()
    expect(screen.getByText('Services')).toBeInTheDocument()
    expect(screen.getByText('Databases')).toBeInTheDocument()
    expect(screen.getByText('Upload')).toBeInTheDocument()
  })
})

// ─── StorageStep tests ────────────────────────────────────────────

function mockStorageInfo(overrides: Record<string, unknown> = {}) {
  return {
    data_dir: '/home/test/.yeliztli',
    free_space_bytes: 50 * 1024 * 1024 * 1024,
    free_space_gb: 50,
    total_space_bytes: 100 * 1024 * 1024 * 1024,
    total_space_gb: 100,
    status: 'ok',
    message: '50.0 GB free — sufficient for Yeliztli.',
    path_exists: true,
    path_writable: true,
    ...overrides,
  }
}

describe('StorageStep', () => {
  it('renders storage location heading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Storage Location')).toBeInTheDocument()
    })
  })

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)
    expect(screen.getByText(/checking storage/i)).toBeInTheDocument()
  })

  it('shows disk space info when loaded', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Disk Space OK')).toBeInTheDocument()
    })
    expect(screen.getByText('50 GB')).toBeInTheDocument()
    expect(screen.getByText('100 GB')).toBeInTheDocument()
  })

  it('shows warning state for low disk space', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockStorageInfo({
            status: 'warning',
            free_space_gb: 7,
            message: 'Low disk space (7.0 GB free).',
          }),
        ),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Low Disk Space')).toBeInTheDocument()
    })
  })

  it('shows blocked state and disables continue', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockStorageInfo({
            status: 'blocked',
            free_space_gb: 2,
            message:
              'Insufficient disk space. Yeliztli requires at least 5 GB free. Current: 2.0 GB.',
          }),
        ),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Insufficient Disk Space')).toBeInTheDocument()
    })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    expect(continueBtn).toBeDisabled()
  })

  it('shows default location with data_dir', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Default location')).toBeInTheDocument()
    })
    expect(
      screen.getByText('/home/test/.yeliztli'),
    ).toBeInTheDocument()
  })

  it('shows custom path input when selected', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Custom location')).toBeInTheDocument()
    })

    // Click custom option
    fireEvent.click(screen.getByText('Custom location'))

    // Input should appear
    expect(
      screen.getByLabelText('Custom storage path'),
    ).toBeInTheDocument()
  })

  it('calls onBack when Back button is clicked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo()),
    })

    const onBack = vi.fn()
    render(<StorageStep onNext={vi.fn()} onBack={onBack} />)

    await waitFor(() => {
      expect(screen.getByText('Storage Location')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Back'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('shows path writable status', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockStorageInfo({ path_writable: true })),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Path writable')).toBeInTheDocument()
    })
    expect(screen.getByText('Yes')).toBeInTheDocument()
  })

  it('shows path not writable when false', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(mockStorageInfo({ path_writable: false })),
    })

    render(<StorageStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Path writable')).toBeInTheDocument()
    })
    expect(screen.getByText('No')).toBeInTheDocument()
  })
})

// ─── CredentialsStep tests ────────────────────────────────────────

function mockCredentials(overrides: Record<string, unknown> = {}) {
  return {
    pubmed_email: '',
    ncbi_api_key: '',
    omim_api_key: '',
    ...overrides,
  }
}

describe('CredentialsStep', () => {
  it('renders external services heading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('External Services')).toBeInTheDocument()
    })
  })

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)
    expect(screen.getByText(/loading credentials/i)).toBeInTheDocument()
  })

  it('shows PubMed email as required', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Required')).toBeInTheDocument()
    })
    expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
  })

  it('shows NCBI API key as optional', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('NCBI API Key')).toBeInTheDocument()
    })
    expect(screen.getAllByText('Optional')).toHaveLength(2)
  })

  it('shows OMIM API key as optional', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('OMIM API Key')).toBeInTheDocument()
    })
  })

  it('disables continue when email is empty', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    expect(continueBtn).toBeDisabled()
  })

  it('enables continue when valid email is entered', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const emailInput = screen.getByLabelText('PubMed email address')
    fireEvent.change(emailInput, { target: { value: 'test@example.com' } })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    expect(continueBtn).not.toBeDisabled()
  })

  it('shows validation error for invalid email', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const emailInput = screen.getByLabelText('PubMed email address')
    fireEvent.change(emailInput, { target: { value: 'invalid-email' } })

    expect(screen.getByText('Please enter a valid email address.')).toBeInTheDocument()
  })

  it('pre-fills fields from existing config', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve(
          mockCredentials({
            pubmed_email: 'existing@test.com',
            ncbi_api_key: 'abc123',
            omim_api_key: 'xyz789',
          }),
        ),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByLabelText('PubMed email address')).toHaveValue('existing@test.com')
    })
    expect(screen.getByLabelText('NCBI API key')).toHaveValue('abc123')
    expect(screen.getByLabelText('OMIM API key')).toHaveValue('xyz789')
  })

  it('calls onBack when Back button is clicked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    const onBack = vi.fn()
    render(<CredentialsStep onNext={vi.fn()} onBack={onBack} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Back'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('calls onNext after successful save', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockCredentials()),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({ success: true, message: 'Credentials saved successfully.' }),
      })

    const onNext = vi.fn()
    render(<CredentialsStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const emailInput = screen.getByLabelText('PubMed email address')
    fireEvent.change(emailInput, { target: { value: 'test@example.com' } })

    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => {
      expect(onNext).toHaveBeenCalledOnce()
    })
  })

  it('shows error on save failure', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockCredentials()),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: 'Config write failed' }),
      })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const emailInput = screen.getByLabelText('PubMed email address')
    fireEvent.change(emailInput, { target: { value: 'test@example.com' } })

    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    await waitFor(() => {
      expect(screen.getByText('Config write failed')).toBeInTheDocument()
    })
  })

  it('has external links with noopener noreferrer', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockCredentials()),
    })

    render(<CredentialsStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('PubMed / NCBI Email')).toBeInTheDocument()
    })

    const links = document.querySelectorAll('a[target="_blank"]')
    links.forEach((link) => {
      expect(link.getAttribute('rel')).toContain('noopener')
      expect(link.getAttribute('rel')).toContain('noreferrer')
    })
  })
})

// ─── DatabasesStep tests ────────────────────────────────────────

function mockDatabaseList(overrides: Record<string, unknown> = {}) {
  return {
    databases: [
      {
        name: 'clinvar',
        display_name: 'ClinVar',
        description: 'Clinical variant interpretations from NCBI ClinVar',
        filename: 'clinvar.db',
        expected_size_bytes: 250_000_000,
        required: true,
        phase: 1,
        downloaded: false,
        file_size_bytes: null,
      },
      {
        name: 'vep_bundle',
        display_name: 'VEP Bundle',
        description: 'Pre-computed variant effect predictions',
        filename: 'vep_bundle.db',
        expected_size_bytes: 500_000_000,
        required: true,
        phase: 2,
        downloaded: false,
        file_size_bytes: null,
      },
      {
        name: 'ancestry_pca',
        display_name: 'Ancestry PCA Bundle',
        description: 'Pre-computed PCA loadings',
        filename: 'ancestry_pca_bundle.npz',
        expected_size_bytes: 414_432,
        required: false,
        phase: 3,
        downloaded: false,
        file_size_bytes: null,
      },
    ],
    total_size_bytes: 800_000_000,
    downloaded_count: 0,
    total_count: 3,
    ...overrides,
  }
}

describe('DatabasesStep', () => {
  it('renders reference databases heading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Reference Databases')).toBeInTheDocument()
    })
  })

  it('shows loading state initially', () => {
    mockFetch.mockReturnValue(new Promise(() => {}))
    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)
    expect(screen.getByText(/loading database information/i)).toBeInTheDocument()
  })

  it('shows error state on fetch failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/failed to load database information/i)).toBeInTheDocument()
    })
  })

  it('lists all databases with their names', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('ClinVar')).toBeInTheDocument()
    })
    expect(screen.getByText('VEP Bundle')).toBeInTheDocument()
    expect(screen.getByText('Ancestry PCA Bundle')).toBeInTheDocument()
  })

  it('shows required badge for required databases', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('ClinVar')).toBeInTheDocument()
    })

    const requiredBadges = screen.getAllByText('Required')
    expect(requiredBadges.length).toBe(2) // ClinVar + VEP Bundle
  })

  it('shows optional badge for optional databases', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Ancestry PCA Bundle')).toBeInTheDocument()
    })

    expect(screen.getByText('Optional')).toBeInTheDocument()
  })

  it('shows total size and download count', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/800\.0 MB/)).toBeInTheDocument()
    })
    expect(screen.getByText(/0 of 3 downloaded/)).toBeInTheDocument()
  })

  it('shows Download Selected button when databases need downloading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Download Selected')).toBeInTheDocument()
    })
  })

  it('disables Continue when required databases are not ready', async () => {
    // Backend reports the health gate is not satisfied.
    routeDatabasesFetch({ list: mockDatabaseList(), setup: { required_dbs_ready: false } })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Reference Databases')).toBeInTheDocument()
    })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    await waitFor(() => expect(continueBtn).toBeDisabled())
  })

  it('enables Continue only when the backend reports required_dbs_ready', async () => {
    // Continue keys on the backend's health-verified gate, NOT on db.downloaded.
    routeDatabasesFetch({ list: mockDatabaseList(), setup: { required_dbs_ready: true } })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Reference Databases')).toBeInTheDocument()
    })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    await waitFor(() => expect(continueBtn).not.toBeDisabled())
  })

  it('keeps Continue disabled when a required DB is downloaded but not healthy', async () => {
    // Regression: the old gate enabled Continue on db.downloaded=true. A DB that
    // downloaded but is empty/partial/corrupt leaves required_dbs_ready=false,
    // so Continue must stay disabled (no silent path to a broken dashboard).
    const downloadedButUnhealthy = mockDatabaseList({
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variant interpretations',
          filename: 'clinvar.db',
          expected_size_bytes: 250_000_000,
          required: true,
          phase: 1,
          downloaded: true,
          file_size_bytes: 248_000_000,
        },
      ],
      downloaded_count: 1,
      total_count: 1,
    })
    routeDatabasesFetch({
      list: downloadedButUnhealthy,
      setup: { required_dbs_ready: false },
      health: { databases: [{ name: 'clinvar', state: 'corrupt', resumable: false }] },
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Reference Databases')).toBeInTheDocument()
    })

    const continueBtn = screen.getByRole('button', { name: /continue/i })
    await waitFor(() => expect(continueBtn).toBeDisabled())
  })

  it('shows Downloaded status for completed databases', async () => {
    const withDownloaded = mockDatabaseList({
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variant interpretations',
          filename: 'clinvar.db',
          expected_size_bytes: 250_000_000,
          required: true,
          phase: 1,
          downloaded: true,
          file_size_bytes: 248_000_000,
        },
      ],
      downloaded_count: 1,
      total_count: 1,
    })

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(withDownloaded),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/Downloaded \(248\.0 MB\)/)).toBeInTheDocument()
    })
  })

  it('calls onBack when Back button is clicked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    const onBack = vi.fn()
    render(<DatabasesStep onNext={vi.fn()} onBack={onBack} />)

    await waitFor(() => {
      expect(screen.getByText('Reference Databases')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Back'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('hides Download Selected when no databases need downloading', async () => {
    const allDone = mockDatabaseList({
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variant interpretations',
          filename: 'clinvar.db',
          expected_size_bytes: 250_000_000,
          required: true,
          phase: 1,
          downloaded: true,
          file_size_bytes: 248_000_000,
        },
      ],
      downloaded_count: 1,
      total_count: 1,
    })

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(allDone),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('ClinVar')).toBeInTheDocument()
    })

    expect(screen.queryByText('Download Selected')).not.toBeInTheDocument()
  })

  it('shows database descriptions', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(
        screen.getByText('Clinical variant interpretations from NCBI ClinVar'),
      ).toBeInTheDocument()
    })
  })

  it('shows database sizes in human-readable format', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseList()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('250.0 MB')).toBeInTheDocument()
    })
    expect(screen.getByText('500.0 MB')).toBeInTheDocument()
    expect(screen.getByText('414.4 KB')).toBeInTheDocument()
  })

  it('triggers download on Download Selected click', async () => {
    routeDatabasesFetch({
      list: mockDatabaseList(),
      download: {
        body: {
          session_id: 'dbdl-test123',
          downloads: [
            { db_name: 'clinvar', job_id: 'dbdl-clinvar-abc' },
            { db_name: 'vep_bundle', job_id: 'dbdl-vep-def' },
            { db_name: 'ancestry_pca', job_id: 'dbdl-pca-ghi' },
          ],
        },
      },
    })

    // Mock EventSource with event simulation
    let progressHandler: ((event: MessageEvent) => void) | null = null
    const closeFn = vi.fn()
    class MockEventSource {
      addEventListener(event: string, handler: (event: MessageEvent) => void) {
        if (event === 'progress') progressHandler = handler
      }
      close() {
        closeFn()
      }
    }
    vi.stubGlobal('EventSource', MockEventSource)

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Download Selected')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Download Selected'))

    await waitFor(() => {
      expect(screen.getByText('Downloading...')).toBeInTheDocument()
    })

    // Simulate SSE progress completing all downloads
    expect(progressHandler).not.toBeNull()
    act(() => {
      progressHandler!(
        new MessageEvent('progress', {
          data: JSON.stringify({
            session_id: 'dbdl-test123',
            databases: [
              { db_name: 'clinvar', job_id: 'dbdl-clinvar-abc', status: 'complete', progress_pct: 100, message: 'Done', error: null },
              { db_name: 'vep_bundle', job_id: 'dbdl-vep-def', status: 'complete', progress_pct: 100, message: 'Done', error: null },
              { db_name: 'ancestry_pca', job_id: 'dbdl-pca-ghi', status: 'complete', progress_pct: 100, message: 'Done', error: null },
            ],
          }),
        }),
      )
    })

    // After completion, EventSource should be closed
    await waitFor(() => {
      expect(closeFn).toHaveBeenCalled()
    })

    vi.unstubAllGlobals()
  })

  it('shows download error when trigger fails', async () => {
    routeDatabasesFetch({
      list: mockDatabaseList(),
      download: { ok: false, status: 500, body: { detail: 'Download service unavailable' } },
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Download Selected')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Download Selected'))

    await waitFor(() => {
      expect(screen.getByText('Download service unavailable')).toBeInTheDocument()
    })
  })

  it('shows a Resume button for a resumable partial and resumes on click', async () => {
    // Simulates a crash/disconnect mid-setup: health reports a resumable partial
    // for lai_bundle, so the wizard offers an explicit Resume that continues the
    // interrupted download instead of restarting it.
    routeDatabasesFetch({
      list: mockDatabaseListWithModes(),
      health: {
        databases: [
          {
            name: 'lai_bundle',
            state: 'partial',
            resumable: true,
            can_resume: true,
            progress_pct: 42,
          },
        ],
      },
      resume: {
        body: {
          session_id: 'dbdl-resume1',
          downloads: [{ db_name: 'lai_bundle', job_id: 'j-lai-resume' }],
        },
      },
    })

    class MockEventSource {
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal('EventSource', MockEventSource)

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    const resumeBtn = await screen.findByTestId('db-resume-lai_bundle')
    expect(resumeBtn).toBeInTheDocument()

    fireEvent.click(resumeBtn)

    await waitFor(() => {
      expect(
        mockFetch.mock.calls.some((c) => c[0] === '/api/databases/resume'),
      ).toBe(true)
    })
    const resumeCall = mockFetch.mock.calls.find(
      (c) => c[0] === '/api/databases/resume',
    )!
    expect(JSON.parse(resumeCall[1].body).db_name).toBe('lai_bundle')

    vi.unstubAllGlobals()
  })

  // ─── Step 14: Per-DB checkbox + running total ──────────────────

  function mockDatabaseListWithModes() {
    return {
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variants',
          filename: 'clinvar.db',
          expected_size_bytes: 100_000_000,
          required: true,
          phase: 1,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'pipeline',
        },
        {
          name: 'vep_bundle',
          display_name: 'VEP Bundle',
          description: 'VEP predictions',
          filename: 'vep_bundle.db',
          expected_size_bytes: 500_000_000,
          required: true,
          phase: 2,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'bundled',
        },
        {
          name: 'lai_bundle',
          display_name: 'LAI Bundle',
          description: 'Local ancestry inference',
          filename: 'lai_bundle.tar.gz',
          expected_size_bytes: 500_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
        {
          name: 'encode_ccres',
          display_name: 'ENCODE cCREs',
          description: 'Regulatory regions',
          filename: 'encode_ccres.db',
          expected_size_bytes: 10_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
        {
          name: 'other_optional',
          display_name: 'Other Optional',
          description: 'Some other optional DB',
          filename: 'other.db',
          expected_size_bytes: 1_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
      ],
      total_size_bytes: 1_111_000_000,
      downloaded_count: 0,
      total_count: 5,
    }
  }

  it('seeds required + lai + encode checkboxes checked; other optional unchecked', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseListWithModes()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    const clinvarBox = screen.getByTestId('db-checkbox-clinvar') as HTMLInputElement
    const laiBox = screen.getByTestId('db-checkbox-lai_bundle') as HTMLInputElement
    const encodeBox = screen.getByTestId('db-checkbox-encode_ccres') as HTMLInputElement
    const otherBox = screen.getByTestId('db-checkbox-other_optional') as HTMLInputElement

    expect(clinvarBox.checked).toBe(true)
    expect(clinvarBox.disabled).toBe(true) // required → locked
    expect(laiBox.checked).toBe(true)
    expect(laiBox.disabled).toBe(false)
    expect(encodeBox.checked).toBe(true)
    expect(encodeBox.disabled).toBe(false)
    expect(otherBox.checked).toBe(false)
    expect(otherBox.disabled).toBe(false)
  })

  it('does not render a checkbox for bundled databases', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseListWithModes()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('VEP Bundle')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('db-checkbox-vep_bundle')).not.toBeInTheDocument()
  })

  it('updates the running total when a checkbox is toggled', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseListWithModes()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    // Initial selection: clinvar (100 MB) + lai_bundle (500 MB) + encode_ccres (10 MB) = 610 MB
    expect(screen.getByTestId('selected-total')).toHaveTextContent('610.0 MB selected')

    // Uncheck LAI → 110 MB
    fireEvent.click(screen.getByTestId('db-checkbox-lai_bundle'))
    expect(screen.getByTestId('selected-total')).toHaveTextContent('110.0 MB selected')

    // Check the other optional → 111 MB
    fireEvent.click(screen.getByTestId('db-checkbox-other_optional'))
    expect(screen.getByTestId('selected-total')).toHaveTextContent('111.0 MB selected')
  })

  it('re-toggling a default-on optional returns it to the selected set', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockDatabaseListWithModes()),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    const laiBox = screen.getByTestId('db-checkbox-lai_bundle') as HTMLInputElement
    expect(laiBox.checked).toBe(true)
    expect(screen.getByTestId('selected-total')).toHaveTextContent('610.0 MB selected')

    // Toggle off → total drops, box unchecks
    fireEvent.click(laiBox)
    expect(laiBox.checked).toBe(false)
    expect(screen.getByTestId('selected-total')).toHaveTextContent('110.0 MB selected')

    // Toggle on again → box re-checks, total returns
    fireEvent.click(laiBox)
    expect(laiBox.checked).toBe(true)
    expect(screen.getByTestId('selected-total')).toHaveTextContent('610.0 MB selected')
  })

  it('hides the running total when no databases need downloading', async () => {
    const allDone = mockDatabaseList({
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variants',
          filename: 'clinvar.db',
          expected_size_bytes: 250_000_000,
          required: true,
          phase: 1,
          downloaded: true,
          file_size_bytes: 248_000_000,
        },
      ],
      downloaded_count: 1,
      total_count: 1,
    })

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(allDone),
    })

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('ClinVar')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('selected-total')).not.toBeInTheDocument()
  })

  it('disables selectable checkboxes while a download is in flight', async () => {
    routeDatabasesFetch({
      list: mockDatabaseListWithModes(),
      download: {
        body: {
          session_id: 'dbdl-disable',
          downloads: [
            { db_name: 'clinvar', job_id: 'j-clinvar' },
            { db_name: 'lai_bundle', job_id: 'j-lai' },
            { db_name: 'encode_ccres', job_id: 'j-encode' },
          ],
        },
      },
    })

    class MockEventSource {
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal('EventSource', MockEventSource)

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Download Selected')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Download Selected'))

    await waitFor(() => {
      expect(screen.getByText('Downloading...')).toBeInTheDocument()
    })

    const laiBox = screen.getByTestId('db-checkbox-lai_bundle') as HTMLInputElement
    const encodeBox = screen.getByTestId('db-checkbox-encode_ccres') as HTMLInputElement
    const otherBox = screen.getByTestId('db-checkbox-other_optional') as HTMLInputElement

    expect(laiBox.disabled).toBe(true)
    expect(encodeBox.disabled).toBe(true)
    expect(otherBox.disabled).toBe(true)

    vi.unstubAllGlobals()
  })

  it('Download Selected sends only the selected subset', async () => {
    routeDatabasesFetch({
      list: mockDatabaseListWithModes(),
      download: {
        body: {
          session_id: 'dbdl-step14',
          downloads: [
            { db_name: 'clinvar', job_id: 'j-clinvar' },
            { db_name: 'encode_ccres', job_id: 'j-encode' },
          ],
        },
      },
    })

    class MockEventSource {
      addEventListener() {}
      close() {}
    }
    vi.stubGlobal('EventSource', MockEventSource)

    render(<DatabasesStep onNext={vi.fn()} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Download Selected')).toBeInTheDocument()
    })

    // Uncheck LAI (default-on) — leave clinvar (required) + encode_ccres selected
    fireEvent.click(screen.getByTestId('db-checkbox-lai_bundle'))

    fireEvent.click(screen.getByText('Download Selected'))

    // Find the download POST by URL (the component also fires GET list + health).
    await waitFor(() => {
      expect(
        mockFetch.mock.calls.some((c) => c[0] === '/api/databases/download'),
      ).toBe(true)
    })

    const downloadCall = mockFetch.mock.calls.find(
      (c) => c[0] === '/api/databases/download',
    )!
    const body = JSON.parse(downloadCall[1].body)
    expect(new Set(body.databases)).toEqual(new Set(['clinvar', 'encode_ccres']))

    vi.unstubAllGlobals()
  })

  // ─── Step 15: Soft skip reminder toast ──────────────────────────

  function mockDatabaseListReadyToContinue() {
    return {
      databases: [
        {
          name: 'clinvar',
          display_name: 'ClinVar',
          description: 'Clinical variants',
          filename: 'clinvar.db',
          expected_size_bytes: 100_000_000,
          required: true,
          phase: 1,
          downloaded: true,
          file_size_bytes: 99_000_000,
          build_mode: 'pipeline',
        },
        {
          name: 'vep_bundle',
          display_name: 'VEP Bundle',
          description: 'VEP predictions',
          filename: 'vep_bundle.db',
          expected_size_bytes: 500_000_000,
          required: true,
          phase: 2,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'bundled',
        },
        {
          name: 'lai_bundle',
          display_name: 'LAI Bundle',
          description: 'Local ancestry inference',
          filename: 'lai_bundle.tar.gz',
          expected_size_bytes: 500_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
        {
          name: 'encode_ccres',
          display_name: 'ENCODE cCREs',
          description: 'Regulatory regions',
          filename: 'encode_ccres.db',
          expected_size_bytes: 10_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
        {
          name: 'other_optional',
          display_name: 'Other Optional',
          description: 'Some other optional DB',
          filename: 'other.db',
          expected_size_bytes: 1_000_000,
          required: false,
          phase: 3,
          downloaded: false,
          file_size_bytes: null,
          build_mode: 'download',
        },
      ],
      total_size_bytes: 1_111_000_000,
      downloaded_count: 1,
      total_count: 5,
    }
  }

  it('shows a Sonner toast naming the skipped optional DBs when Continue is clicked', async () => {
    routeDatabasesFetch({
      list: mockDatabaseListReadyToContinue(),
      setup: { required_dbs_ready: true },
    })

    const onNext = vi.fn()
    render(<DatabasesStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    // Uncheck both default-on optional bundles, leave `other_optional` already off.
    fireEvent.click(screen.getByTestId('db-checkbox-lai_bundle'))
    fireEvent.click(screen.getByTestId('db-checkbox-encode_ccres'))

    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(toastInfoMock).toHaveBeenCalledTimes(1)
    const [message, options] = toastInfoMock.mock.calls[0]
    expect(message).toContain('LAI Bundle')
    expect(message).toContain('ENCODE cCREs')
    expect(message).toContain('Other Optional')
    expect(options?.description).toMatch(/Settings > Update Manager/i)
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('shows a toast that lists only the unchecked optional DBs', async () => {
    routeDatabasesFetch({
      list: mockDatabaseListReadyToContinue(),
      setup: { required_dbs_ready: true },
    })

    const onNext = vi.fn()
    render(<DatabasesStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    // Leave LAI + ENCODE checked (defaults). Only `other_optional` stays unchecked.
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(toastInfoMock).toHaveBeenCalledTimes(1)
    const [message] = toastInfoMock.mock.calls[0]
    expect(message).toContain('Other Optional')
    expect(message).not.toContain('LAI Bundle')
    expect(message).not.toContain('ENCODE cCREs')
    expect(onNext).toHaveBeenCalledOnce()
  })

  it('does not show a toast when every optional DB is selected', async () => {
    routeDatabasesFetch({
      list: mockDatabaseListReadyToContinue(),
      setup: { required_dbs_ready: true },
    })

    const onNext = vi.fn()
    render(<DatabasesStep onNext={onNext} onBack={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('LAI Bundle')).toBeInTheDocument()
    })

    // Tick the remaining default-off optional so nothing is skipped.
    fireEvent.click(screen.getByTestId('db-checkbox-other_optional'))

    fireEvent.click(screen.getByRole('button', { name: /continue/i }))

    expect(toastInfoMock).not.toHaveBeenCalled()
    expect(onNext).toHaveBeenCalledOnce()
  })
})

// ─── UploadStep tests ─────────────────────────────────────────────

describe('UploadStep', () => {
  it('renders upload sample heading', () => {
    render(<UploadStep onBack={vi.fn()} />)
    expect(screen.getByText('Upload Sample')).toBeInTheDocument()
  })

  it('shows drop zone with instructions', () => {
    render(<UploadStep onBack={vi.fn()} />)
    expect(
      screen.getByText('Drop a 23andMe or AncestryDNA raw data file here'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('or click to browse (.txt, .csv, .tsv)'),
    ).toBeInTheDocument()
  })

  it('has accessible drop zone', () => {
    render(<UploadStep onBack={vi.fn()} />)
    const dropZone = screen.getByRole('button', {
      name: /select 23andme or ancestrydna raw data file/i,
    })
    expect(dropZone).toBeInTheDocument()
    expect(dropZone).toHaveAttribute('tabindex', '0')
  })

  it('shows skip button to go to dashboard', () => {
    render(<UploadStep onBack={vi.fn()} />)
    expect(
      screen.getByText(/skip — go to dashboard/i),
    ).toBeInTheDocument()
  })

  it('calls onBack when Back button is clicked', () => {
    const onBack = vi.fn()
    render(<UploadStep onBack={onBack} />)

    fireEvent.click(screen.getByText('Back'))
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('shows file name after selection', () => {
    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByText('genome_data.txt')).toBeInTheDocument()
  })

  it('shows Upload & Parse button after file selection', () => {
    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByText('Upload & Parse')).toBeInTheDocument()
  })

  it('shows error for invalid file extension', () => {
    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test'], 'image.png', { type: 'image/png' })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(
      screen.getByText(
        'Please select a 23andMe or AncestryDNA raw data file (.txt, .csv, or .tsv)',
      ),
    ).toBeInTheDocument()
  })

  it('shows success state after upload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          sample_id: 1,
          job_id: 'test-job-123',
          variant_count: 600123,
          nocall_count: 1234,
          file_format: '23andme_v5',
        }),
    })

    render(<UploadStep onBack={vi.fn()} />)

    // Select file
    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    // Click upload
    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(screen.getByText('Sample Uploaded')).toBeInTheDocument()
    })

    // Should show variant stats
    expect(screen.getByText('600,123')).toBeInTheDocument()
    expect(screen.getByText('1,234')).toBeInTheDocument()
    expect(screen.getByText('23andme_v5')).toBeInTheDocument()
  })

  it('shows Go to Dashboard after successful upload', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          sample_id: 1,
          job_id: 'test-job-123',
          variant_count: 600123,
          nocall_count: 1234,
          file_format: '23andme_v5',
        }),
    })

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(screen.getByText('Go to Dashboard')).toBeInTheDocument()
    })
  })

  it('shows error state on upload failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: () =>
        Promise.resolve({
          detail: 'Not a valid 23andMe file',
        }),
    })

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['bad content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(
        screen.getByText('Not a valid 23andMe file'),
      ).toBeInTheDocument()
    })
  })

  it('shows parsing state while uploading', async () => {
    mockFetch.mockReturnValueOnce(new Promise(() => {})) // Never resolves

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(screen.getByText('Parsing file...')).toBeInTheDocument()
    })
  })

  it('disables Back button while uploading', async () => {
    mockFetch.mockReturnValueOnce(new Promise(() => {}))

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['test content'], 'genome_data.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(screen.getByText('Parsing file...')).toBeInTheDocument()
    })

    expect(screen.getByText('Back')).toBeDisabled()
  })

  // ── Step 45 / ADNA-12 — AncestryDNA ingest happy path ──────────

  it('renders ancestrydna_v2.0 success card after AncestryDNA upload (ADNA-12)', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          sample_id: 9,
          job_id: 'job-ancestrydna-happy',
          variant_count: 712_345,
          nocall_count: 1_234,
          file_format: 'ancestrydna_v2.0',
        }),
    })

    render(<UploadStep onBack={vi.fn()} />)

    const file = new File(['#AncestryDNA raw data'], 'AncestryDNA.txt', {
      type: 'text/plain',
    })
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(screen.getByText('AncestryDNA.txt')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Upload & Parse'))

    await waitFor(() => {
      expect(screen.getByText('Sample Uploaded')).toBeInTheDocument()
    })

    expect(screen.getByText('712,345')).toBeInTheDocument()
    expect(screen.getByText('1,234')).toBeInTheDocument()
    expect(screen.getByText('ancestrydna_v2.0')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /go to dashboard/i }),
    ).toBeInTheDocument()
  })
})

/** Step 45 — Phase 1 frontend closure (ADNA-12, Plan §13.1).
 *
 * SetupWizard-level contract: the wizard's stepper exposes the Upload step
 * (terminal AncestryDNA entry-point) even when the disclaimer is already
 * accepted. The full-wizard walk + AncestryDNA upload is exercised by
 * Playwright in `setup-wizard-ancestrydna.spec.ts` (step 44); here we lock
 * the unit-level chrome contract.
 */
describe('SetupWizard — Step 45 / ADNA-12 (AncestryDNA wizard chrome)', () => {
  it('keeps the Upload step in the stepper after the disclaimer is accepted', async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === 'string' && url === '/api/setup/status') {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve(
              mockSetupStatus({
                disclaimer_accepted: true,
              }),
            ),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      })
    })

    render(<SetupWizard />)

    await waitFor(() => {
      expect(screen.getByText('Setup Wizard')).toBeInTheDocument()
    })

    // Stepper labels must include Upload (the terminal AncestryDNA entry
    // point) — guards against regressions that drop the Upload step when
    // disclaimer_accepted state advances past step 0.
    expect(screen.getByText('Welcome')).toBeInTheDocument()
    expect(screen.getByText('Upload')).toBeInTheDocument()
  })
})
