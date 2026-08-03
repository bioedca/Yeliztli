/**
 * Issue #1988 — Query Builder must not present locus annotations as personal
 * findings when the sample is homozygous reference or carriage is unresolved.
 */

import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const rawVcfExportDiagnostic =
  'This VCF export would omit 1 annotated position with unresolved zygosity. Export CSV, TSV, or JSON, or filter to positions with resolved zygosity.'

const baseRow = {
  chrom: '17',
  ref: 'A',
  alt: 'G',
  gene_symbol: 'BRCA1',
  transcript_id: null,
  consequence: 'missense_variant',
  hgvs_coding: null,
  hgvs_protein: null,
  clinvar_accession: null,
  clinvar_conditions: null,
  gnomad_af_global: 0.001,
  gnomad_af_afr: null,
  gnomad_af_amr: null,
  gnomad_af_asj: null,
  gnomad_af_eas: null,
  gnomad_af_eur: null,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  rare_flag: true,
  ultra_rare_flag: false,
  cadd_phred: 25,
  sift_score: null,
  sift_pred: null,
  polyphen2_hsvar_score: null,
  polyphen2_hsvar_pred: null,
  revel: 0.7,
  annotation_coverage: 63,
  evidence_conflict: false,
  ensemble_pathogenic: false,
  disease_name: null,
  inheritance_pattern: null,
}

const carriedRow = {
  ...baseRow,
  rsid: 'rs_carried',
  pos: 100,
  genotype: 'AG',
  zygosity: 'het',
  carriage_status: 'carried',
  clinvar_significance: 'Benign',
  clinvar_review_stars: 2,
}

const homRefRow = {
  ...baseRow,
  rsid: 'rs_hom_ref_pathogenic',
  pos: 200,
  genotype: 'AA',
  zygosity: 'hom_ref',
  carriage_status: 'not_carried',
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 3,
}

const homAltRow = {
  ...baseRow,
  rsid: 'rs_hom_alt',
  pos: 300,
  genotype: 'GG',
  zygosity: 'hom_alt',
  carriage_status: 'carried',
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 3,
}

const unresolvedRow = {
  ...baseRow,
  rsid: 'rs_unresolved_pathogenic',
  pos: 400,
  genotype: 'II',
  zygosity: null,
  carriage_status: 'unresolved',
  clinvar_significance: 'Pathogenic',
  clinvar_review_stars: 3,
}

type QueryBody = {
  sample_id?: number
  format?: string
  include_all_positions?: boolean
  cursor_chrom?: string
  cursor_pos?: number
}

type QueryReply = {
  payload: unknown
  status?: number
}

type QueryResponder = (
  body: QueryBody,
  requestIndex: number,
) => QueryReply | Promise<QueryReply>

type ExportResponder = (
  body: QueryBody,
  requestIndex: number,
) => QueryReply | Promise<QueryReply>

function queryPage(
  items: unknown[],
  options: {
    total?: number | null
    hasMore?: boolean
    nextCursorChrom?: string | null
    nextCursorPos?: number | null
  } = {},
) {
  return {
    items,
    total_matching: options.total === undefined ? items.length : options.total,
    next_cursor_chrom: options.nextCursorChrom ?? null,
    next_cursor_pos: options.nextCursorPos ?? null,
    has_more: options.hasMore ?? false,
    limit: 50,
  }
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

async function settleReact(page: Page) {
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  )
}

async function stubQueryBuilder(
  page: Page,
  responder?: QueryResponder,
  exportResponder?: ExportResponder,
) {
  const queryBodies: QueryBody[] = []
  const exportBodies: QueryBody[] = []

  await page.route(/\/api\/query\/fields$/, (route) =>
    route.fulfill(
      jsonRoute({
        fields: [{ name: 'gene_symbol', type: 'text', label: 'Gene Symbol' }],
        operators: ['='],
      }),
    ),
  )
  await page.route(/\/api\/saved-queries(\?|$)/, (route) =>
    route.fulfill(jsonRoute({ queries: [] })),
  )
  await page.route(/\/api\/preferences\/theme$/, (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    const { theme } = route.request().postDataJSON() as { theme: string }
    return route.fulfill(jsonRoute({ theme }))
  })
  await page.route(/\/api\/query$/, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as QueryBody
    queryBodies.push(body)
    const requestIndex = queryBodies.length - 1
    const reply = responder
      ? await responder(body, requestIndex)
      : {
          payload: queryPage(
            body.include_all_positions === true
              ? [carriedRow, homRefRow, homAltRow, unresolvedRow]
              : [carriedRow, homAltRow],
          ),
        }
    return route.fulfill(jsonRoute(reply.payload, reply.status))
  })
  await page.route(/\/api\/export\/query$/, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as QueryBody
    exportBodies.push(body)
    if (exportResponder) {
      const reply = await exportResponder(body, exportBodies.length - 1)
      return route.fulfill(jsonRoute(reply.payload, reply.status))
    }
    if (body.format === 'vcf' && body.include_all_positions) {
      return route.fulfill(
        jsonRoute(
          {
            detail: rawVcfExportDiagnostic,
          },
          422,
        ),
      )
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'content-disposition': 'attachment; filename=query.json' },
      body: '[]',
    })
  })

  return { queryBodies, exportBodies }
}

async function exportJson(page: Page) {
  await page.getByTestId('export-btn').click()
  await page.getByTestId('export-json').click()
}

async function exportVcf(page: Page) {
  await page.getByTestId('export-btn').click()
  await page.getByTestId('export-vcf').click()
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route(/\/api\/variants\/count\?sample_id=\d+$/, (route) =>
    route.fulfill(jsonRoute({ count: 1 })),
  )
  await page.route(/\/api\/annotation\/active\/\d+$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'No active job' }, 404)),
  )
})

test('carried-only is safe by default and all-position opt-in is explicit (#1988)', async ({
  page,
}) => {
  const requests = await stubQueryBuilder(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)

  const includeAll = page.getByRole('checkbox', { name: 'Include all annotated positions' })
  await expect(includeAll).not.toBeChecked()
  await expect(includeAll).toHaveAccessibleName('Include all annotated positions')
  await expect(includeAll).toHaveAccessibleDescription(
    /includes homozygous-reference and unresolved positions/i,
  )
  await expect(includeAll).toHaveAttribute('aria-describedby', 'include-all-positions-help')
  await expect(page.locator('#include-all-positions-help')).toContainText(
    /locus metadata, not as carried findings/i,
  )

  await page.getByRole('button', { name: '+ Rule' }).click()
  await page.getByTestId('run-query-btn').click()

  await expect.poll(() => requests.queryBodies.length).toBe(1)
  expect(requests.queryBodies.at(-1)?.include_all_positions).toBe(false)
  await expect(page.getByText('rs_carried')).toBeVisible()
  await expect(page.getByText('rs_hom_alt')).toBeVisible()
  await expect(page.getByText('rs_hom_ref_pathogenic')).toHaveCount(0)
  await expect(page.getByText('Carried · homozygous alternate')).toBeVisible()
  await expect(page.getByText(/matching carried variants/i)).toBeVisible()

  await exportJson(page)
  await expect.poll(() => requests.exportBodies.length).toBe(1)
  expect(requests.exportBodies[0].include_all_positions).toBe(false)

  await includeAll.check()
  await expect(page.getByTestId('query-results-table')).toHaveCount(0)
  await page.getByTestId('run-query-btn').click()

  await expect.poll(() => requests.queryBodies.length).toBe(2)
  expect(requests.queryBodies.at(-1)?.include_all_positions).toBe(true)
  await expect(page.getByText('rs_hom_ref_pathogenic')).toBeVisible()
  await expect(page.getByText('rs_unresolved_pathogenic')).toBeVisible()
  await expect(page.getByText('Not carried · homozygous reference')).toBeVisible()
  await expect(page.getByText('Unresolved', { exact: true })).toBeVisible()
  const resultSummary = page.getByText(/matching annotated positions/i)
  await expect(resultSummary).toBeVisible()

  const nonCarriedRow = page.locator('[data-carriage-status="not_carried"]')
  const nonCarriedClinvar = nonCarriedRow
    .getByTestId('query-clinvar_significance-cell')
    .getByText('Pathogenic', { exact: true })
  await expect(nonCarriedClinvar).toHaveClass(/text-muted-foreground/)
  await expect(nonCarriedClinvar).not.toHaveClass(/rounded-full/)
  await expect(nonCarriedRow.getByRole('img')).toHaveAccessibleName(
    /sample does not carry this alternate allele/i,
  )

  const unresolved = page.locator('[data-carriage-status="unresolved"]')
  const unresolvedClinvar = unresolved
    .getByTestId('query-clinvar_significance-cell')
    .getByText('Pathogenic', { exact: true })
  await expect(unresolvedClinvar).toHaveClass(/text-muted-foreground/)
  await expect(unresolvedClinvar).not.toHaveClass(/rounded-full/)
  await expect(unresolved.getByRole('img')).toHaveAccessibleName(
    /alternate-allele carriage is unresolved/i,
  )

  const themeToggle = page.getByTestId('theme-toggle')
  await expect(themeToggle).toHaveAccessibleName('Theme: system')
  for (const theme of ['light', 'dark'] as const) {
    await themeToggle.click()
    await expect(themeToggle).toHaveAccessibleName(`Theme: ${theme}`)
    const expectedColors =
      theme === 'dark'
        ? { foreground: 'rgb(248, 250, 252)', mutedForeground: 'rgb(148, 163, 184)' }
        : { foreground: 'rgb(2, 8, 23)', mutedForeground: 'rgb(79, 91, 109)' }

    // Drive the stateful ThemeContext path instead of racing it with a direct
    // root-class mutation. Wait for both semantic tokens before axe samples
    // contrast so WebKit cannot observe a mixed-theme frame.
    await expect(resultSummary).toHaveCSS('color', expectedColors.foreground)
    await expect(nonCarriedRow.getByTestId('query-rsid-cell')).toHaveCSS(
      'color',
      expectedColors.mutedForeground,
    )
    const accessibility = await new AxeBuilder({ page })
      .include('[data-testid="include-all-positions"]')
      .include('[aria-label="Query results"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    expect(
      accessibility.violations.map(({ id, impact, nodes }) => ({
        id,
        impact,
        nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
      })),
      `${theme} mode accessibility violations`,
    ).toEqual([])
  }

  await exportVcf(page)
  await expect.poll(() => requests.exportBodies.length).toBe(2)
  expect(requests.exportBodies[1]).toMatchObject({
    format: 'vcf',
    include_all_positions: true,
  })
  const exportError = page.getByRole('alert')
  const exportMessage = exportError.getByText('Export failed. Please try again.', { exact: true })
  await expect(exportMessage).toHaveText('Export failed. Please try again.')
  await expect(exportError).not.toContainText(rawVcfExportDiagnostic)
  const alertAccessibility = await new AxeBuilder({ page })
    .include('[role="alert"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(
    alertAccessibility.violations.map(({ id, impact, nodes }) => ({
      id,
      impact,
      nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
    })),
    'export alert accessibility violations',
  ).toEqual([])

  await exportJson(page)
  await expect.poll(() => requests.exportBodies.length).toBe(3)
  expect(requests.exportBodies[2]).toMatchObject({
    format: 'json',
    include_all_positions: true,
  })
  await expect(exportError).toHaveCount(0)
})

test('obsolete export failure cannot cross an all-position mode edit (#1988)', async ({
  page,
}) => {
  const pendingExport = deferred()
  const requests = await stubQueryBuilder(page, undefined, async () => {
    await pendingExport.promise
    return { payload: { detail: 'obsolete export failure' }, status: 422 }
  })

  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)
  await page.getByRole('button', { name: '+ Rule' }).click()
  await page.getByTestId('run-query-btn').click()
  await expect(page.getByText('rs_carried')).toBeVisible()

  await exportVcf(page)
  await expect.poll(() => requests.exportBodies.length).toBe(1)
  await page.getByTestId('include-all-positions').check()

  const staleResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/export/query') && response.request().method() === 'POST',
  )
  pendingExport.resolve()
  await staleResponse
  await settleReact(page)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('obsolete run success and failure cannot cross a mode or filter edit (#1988)', async ({
  page,
}) => {
  const firstRun = deferred()
  const secondRun = deferred()
  const oldSampleRun = deferred()
  const sample2StalenessProbe = deferred()
  let sample2ProbeStarted = false
  await page.route(/\/api\/variants\/count\?sample_id=\d+$/, async (route) => {
    if (route.request().url().endsWith('sample_id=2')) {
      sample2ProbeStarted = true
      await sample2StalenessProbe.promise
    }
    await route.fulfill(jsonRoute({ count: 1 }))
  })
  const requests = await stubQueryBuilder(page, async (body, requestIndex) => {
    if (requestIndex === 0) {
      await firstRun.promise
      return { payload: queryPage([carriedRow, homAltRow]) }
    }
    if (requestIndex === 1) {
      await secondRun.promise
      return { payload: { detail: 'obsolete failure' }, status: 500 }
    }
    if (requestIndex === 3) {
      await oldSampleRun.promise
      return { payload: queryPage([carriedRow, homAltRow]) }
    }
    const items = body.include_all_positions
      ? [carriedRow, homRefRow, homAltRow, unresolvedRow]
      : [carriedRow, homAltRow]
    return { payload: queryPage(items) }
  })

  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)
  await page.getByRole('button', { name: '+ Rule' }).click()

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(1)
  await page.getByTestId('include-all-positions').check()
  const firstResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/query') && response.request().method() === 'POST',
  )
  firstRun.resolve()
  await firstResponse
  await settleReact(page)
  await expect(page.getByTestId('query-results-table')).toHaveCount(0)

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(2)
  await page.getByRole('textbox', { name: 'Value' }).fill('BRCA1')
  const secondResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/query') && response.request().method() === 'POST',
  )
  secondRun.resolve()
  await secondResponse
  await settleReact(page)
  await expect(page.getByText('Query failed')).toHaveCount(0)
  await expect(page.getByTestId('run-query-btn')).toBeEnabled()

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(3)
  await expect(page.getByText('rs_hom_ref_pathogenic')).toBeVisible()

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(4)
  await page.evaluate(() => {
    history.pushState({}, '', '/query-builder?sample_id=2')
    window.dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page).toHaveURL(/sample_id=2/)
  await expect.poll(() => sample2ProbeStarted).toBe(true)
  await expect(page.getByTestId('run-query-btn')).toHaveCount(0)
  await expect(page.getByTestId('query-results-table')).toHaveCount(0)
  await expect(page.getByText('Query failed')).toHaveCount(0)
  sample2StalenessProbe.resolve()
  await expect(page.getByTestId('run-query-btn')).toBeEnabled()
  const oldSampleResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/query') && response.request().method() === 'POST',
  )
  oldSampleRun.resolve()
  await oldSampleResponse
  await settleReact(page)
  await expect(page.getByTestId('query-results-table')).toHaveCount(0)
  await expect(page.getByText('Query failed')).toHaveCount(0)

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(5)
  expect(requests.queryBodies[4]).toMatchObject({
    sample_id: 2,
    include_all_positions: false,
    filter: {
      combinator: 'and',
      rules: [{ field: 'gene_symbol', operator: '=', value: 'BRCA1' }],
    },
  })
  await expect(page.getByText('rs_carried')).toBeVisible()
  await exportJson(page)
  await expect.poll(() => requests.exportBodies.length).toBe(1)
  expect(requests.exportBodies[0]).toMatchObject({
    sample_id: 2,
    include_all_positions: false,
  })
})

test('rerun supersedes a pending load-more page and resets its loading state (#1988)', async ({
  page,
}) => {
  const loadMore = deferred()
  const rerunRow = { ...carriedRow, rsid: 'rs_current_rerun', pos: 500 }
  const requests = await stubQueryBuilder(page, async (_body, requestIndex) => {
    if (requestIndex === 0) {
      return {
        payload: queryPage([carriedRow], {
          total: 2,
          hasMore: true,
          nextCursorChrom: '17',
          nextCursorPos: 100,
        }),
      }
    }
    if (requestIndex === 1) {
      await loadMore.promise
      return { payload: queryPage([homAltRow], { total: null }) }
    }
    return {
      payload: queryPage([rerunRow], {
        total: 2,
        hasMore: true,
        nextCursorChrom: '17',
        nextCursorPos: 500,
      }),
    }
  })

  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)
  await page.getByRole('button', { name: '+ Rule' }).click()
  await page.getByTestId('run-query-btn').click()
  await expect(page.getByText('rs_carried')).toBeVisible()

  await page.getByRole('button', { name: 'Load More' }).click()
  await expect.poll(() => requests.queryBodies.length).toBe(2)
  expect(requests.queryBodies[1].cursor_pos).toBe(100)

  await page.getByTestId('run-query-btn').click()
  await expect.poll(() => requests.queryBodies.length).toBe(3)
  await expect(page.getByText('rs_current_rerun')).toBeVisible()
  const loadMoreButton = page.getByRole('button', { name: 'Load More' })
  await expect(loadMoreButton).toBeEnabled()

  const staleResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/query') && response.request().method() === 'POST',
  )
  loadMore.resolve()
  await staleResponse
  await settleReact(page)
  await expect(page.getByText('rs_hom_alt')).toHaveCount(0)
  await expect(page.getByText('rs_current_rerun')).toBeVisible()
  await expect(loadMoreButton).toBeEnabled()
})
