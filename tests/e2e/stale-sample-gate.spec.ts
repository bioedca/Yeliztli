import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

const SAMPLE_ID = 1
const JOB_ID = 'stale-gate-job'

const STALE_PAYLOAD = {
  installed_version: '2026.06',
  required_version: '2026.07',
  update_url: 'https://example.invalid/bundles/2026.07',
  reannotate_url: `/api/annotation/${SAMPLE_ID}`,
}

const SAMPLE = {
  id: SAMPLE_ID,
  name: 'Re-annotation Fixture',
  db_path: '/tmp/reannotation-fixture.db',
  file_format: '23andme_v5',
  file_hash: 'abc123',
  notes: null,
  date_collected: null,
  source: null,
  individual_id: null,
  extra: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: null,
}

interface GateState {
  fresh: boolean
  active: boolean
  postCalls: number
  activeProbeCalls: number
  freshnessProbeCalls: number
  freshResponses: number
}

function gateState(overrides: Partial<GateState> = {}): GateState {
  return {
    fresh: false,
    active: false,
    postCalls: 0,
    activeProbeCalls: 0,
    freshnessProbeCalls: 0,
    freshResponses: 0,
    ...overrides,
  }
}

function jsonRoute(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

function activeJob(progressPct: number) {
  return {
    job_id: JOB_ID,
    sample_id: SAMPLE_ID,
    status: 'running',
    progress_pct: progressPct,
    message: 'Annotating stale sample',
  }
}

async function mockDashboard(page: Page, state: GateState): Promise<void> {
  await bypassSetup(page)

  // App shell and dashboard dependencies. Anchored patterns keep similarly
  // named endpoints (and Vite module requests) out of these fixtures.
  await page.route(/\/api\/updates\/app-update$/, (route) =>
    route.fulfill(
      jsonRoute({
        update_available: false,
        current_version: '0.2.0',
        latest_version: null,
        release_url: null,
        release_notes: null,
        error: null,
      }),
    ),
  )
  await page.route(/\/api\/analysis\/ancestry\/lai\/status$/, (route) =>
    route.fulfill(
      jsonRoute({ available: false, current_version: null, degraded_coverage: false }),
    ),
  )
  await page.route(/\/api\/updates\/prompts\?sample_id=1$/, (route) =>
    route.fulfill(jsonRoute([])),
  )
  await page.route(/\/api\/updates\/status$/, (route) => route.fulfill(jsonRoute([])))
  await page.route(/\/api\/updates\/check$/, (route) =>
    route.fulfill(
      jsonRoute({ available: [], up_to_date: [], errors: [], checked_at: null }),
    ),
  )
  await page.route(/\/api\/preferences\/update-check-interval$/, (route) =>
    route.fulfill(jsonRoute({ update_check_interval: 'off' })),
  )
  await page.route(/\/api\/databases$/, (route) =>
    route.fulfill(
      jsonRoute({ databases: [], total_size_bytes: 0, downloaded_count: 0, total_count: 0 }),
    ),
  )
  await page.route(/\/api\/samples$/, (route) => route.fulfill(jsonRoute([SAMPLE])))
  await page.route(/\/api\/individuals$/, (route) => route.fulfill(jsonRoute([])))

  // The 423/200 transition is the authoritative stale-sample contract. Tests
  // mutate `state.fresh`; the next application poll observes the new response.
  await page.route(/\/api\/variants\/count\?sample_id=1$/, (route) => {
    state.freshnessProbeCalls += 1
    if (!state.fresh) {
      return route.fulfill(jsonRoute({ detail: STALE_PAYLOAD }, 423))
    }
    state.freshResponses += 1
    return route.fulfill(jsonRoute({ total: 623_841 }))
  })

  await page.route(/\/api\/annotation\/active\/1$/, (route) => {
    state.activeProbeCalls += 1
    if (!state.active) return route.fulfill(jsonRoute({ detail: 'No active job' }, 404))
    return route.fulfill(jsonRoute(activeJob(24)))
  })

  await page.route(/\/api\/annotation\/1$/, (route) => {
    if (route.request().method() !== 'POST') {
      return route.fulfill(jsonRoute({ detail: 'Method not allowed' }, 405))
    }
    state.postCalls += 1
    state.active = true
    return route.fulfill(
      jsonRoute({ job_id: JOB_ID, sample_id: SAMPLE_ID, status: 'pending' }, 202),
    )
  })

  // Data loaded once the freshness gate lifts.
  await page.route(/\/api\/variants\/qc-stats\?sample_id=1$/, (route) =>
    route.fulfill(
      jsonRoute({
        total_variants: 623_841,
        called_variants: 610_000,
        nocall_variants: 13_841,
        het_count: 210_000,
        hom_count: 400_000,
        call_rate: 0.977817,
        heterozygosity_rate: 0.344262,
        per_chromosome: [],
      }),
    ),
  )
  await page.route(/\/api\/analysis\/qc\/metrics\?sample_id=1$/, (route) =>
    route.fulfill(
      jsonRoute({
        computed: false,
        call_rate: null,
        call_rate_pass: null,
        heterozygosity_rate: null,
        ti_tv_ratio: null,
        total_variants: 623_841,
        called_variants: 610_000,
        nocall_variants: 13_841,
        genetic_sex: null,
        recorded_sex: null,
        sex_check: null,
        het_outlier_z: null,
        het_outlier_status: null,
      }),
    ),
  )
  await page.route(/\/api\/analysis\/findings\/summary\?sample_id=1$/, (route) =>
    route.fulfill(
      jsonRoute({ total_findings: 0, modules: [], high_confidence_findings: [] }),
    ),
  )
  await page.route(/\/api\/analysis\/findings\?sample_id=1(?:&.*)?$/, (route) =>
    route.fulfill(jsonRoute([])),
  )
  await page.route(/\/api\/analysis\/modules\/summary\?sample_id=1$/, (route) =>
    route.fulfill(jsonRoute({ modules: [] })),
  )
}

test.describe('Stale sample re-annotation gate (#1973)', () => {
  test('lifts automatically after re-annotation makes the sample fresh', async ({ page }) => {
    const state = gateState()
    await mockDashboard(page, state)

    await page.goto(`/?sample_id=${SAMPLE_ID}`)

    const gate = page.getByTestId('stale-sample-gate')
    await expect(gate).toBeVisible()
    await gate.getByTestId('stale-reannotate-cta').click()

    const progress = gate.getByRole('progressbar')
    await expect(progress).toBeVisible()
    await expect(progress).toHaveAttribute('aria-valuenow', '24')
    expect(state.postCalls).toBe(1)

    // Simulate the backend committing fresh annotations. No navigation or
    // reload follows: the gate's own polling must observe the next 200.
    state.fresh = true

    await expect(gate).toHaveCount(0)
    await expect(
      page.getByRole('status', { name: 'Sample and database status' }),
    ).toContainText('Re-annotation Fixture')
    await expect(page.getByRole('region', { name: 'Analysis modules' })).toBeVisible()
    expect(page.url()).toContain(`?sample_id=${SAMPLE_ID}`)
    expect(state.freshResponses).toBeGreaterThanOrEqual(1)
    expect(state.postCalls).toBe(1)
  })

  test('reconnects to an active job after reload without a duplicate POST', async ({ page }) => {
    test.slow()
    const state = gateState({ active: true })
    await mockDashboard(page, state)

    await page.goto(`/?sample_id=${SAMPLE_ID}`)

    let gate = page.getByTestId('stale-sample-gate')
    await expect(gate.getByRole('progressbar')).toBeVisible()
    await expect(gate.getByTestId('stale-reannotate-cta')).toBeDisabled()
    const activeProbesBeforeReload = state.activeProbeCalls

    await page.reload()

    gate = page.getByTestId('stale-sample-gate')
    await expect(gate.getByRole('progressbar')).toBeVisible()
    await expect(gate.getByTestId('stale-reannotate-cta')).toBeDisabled()
    expect(state.activeProbeCalls).toBeGreaterThan(activeProbesBeforeReload)
    expect(state.postCalls).toBe(0)

    const accessibility = await new AxeBuilder({ page })
      .include('[data-testid="stale-sample-gate"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(
      accessibility.violations.map(({ id, impact, nodes }) => ({
        id,
        impact,
        targets: nodes.flatMap((node) => node.target),
      })),
    ).toEqual([])
  })

  test('gates a non-Dashboard sample route before it can render stale data', async ({ page }) => {
    const state = gateState()
    await mockDashboard(page, state)

    await page.goto(`/findings?sample_id=${SAMPLE_ID}`)

    const gate = page.getByTestId('stale-sample-gate')
    await expect(gate).toBeVisible()
    await expect(gate).toContainText('Sample requires re-annotation')
    await expect(gate).not.toContainText(JSON.stringify(STALE_PAYLOAD))
    expect(page.url()).toContain(`/findings?sample_id=${SAMPLE_ID}`)

    await gate.getByTestId('stale-reannotate-cta').click()
    await expect(gate.getByRole('progressbar')).toBeVisible()
    expect(state.postCalls).toBe(1)
  })

  test('keeps sample-specific content fenced when freshness cannot be verified', async ({ page }) => {
    const rawDiagnostic = 'sqlite:///private/data/sample.db: connection refused'
    const state = gateState({ fresh: true })
    await mockDashboard(page, state)
    await page.route(/\/api\/variants\/count\?sample_id=1$/, (route) =>
      route.fulfill(jsonRoute({ detail: rawDiagnostic }, 500)),
    )

    await page.goto(`/findings?sample_id=${SAMPLE_ID}`)

    const unavailable = page.getByTestId('staleness-probe-unavailable')
    await expect(unavailable).toBeVisible()
    await expect(unavailable).toContainText('Unable to verify sample freshness')
    await expect(unavailable).toContainText('Retry freshness check')
    await expect(unavailable).not.toContainText(rawDiagnostic)
    await expect(page.getByRole('region', { name: 'Analysis modules' })).toHaveCount(0)
  })

  test('uses the direct concordance sample instead of a conflicting query parameter', async ({ page }) => {
    const state = gateState()
    let wrongSampleProbed = false
    await mockDashboard(page, state)
    await page.route(/\/api\/variants\/count\?sample_id=99$/, (route) => {
      wrongSampleProbed = true
      return route.fulfill(jsonRoute({ total: 99 }))
    })

    await page.goto(`/samples/${SAMPLE_ID}/concordance?sample_id=99`)

    const gate = page.getByTestId('stale-sample-gate')
    await expect(gate).toBeVisible()
    await gate.getByTestId('stale-reannotate-cta').click()
    await expect(gate.getByRole('progressbar')).toBeVisible()
    expect(state.postCalls).toBe(1)
    expect(wrongSampleProbed).toBe(false)
  })
})
