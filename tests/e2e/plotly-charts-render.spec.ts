/**
 * Issue #2418 — every chart the app ships still draws after a Plotly major.
 *
 * plotly.js moved from 3.x to 4.x to clear a critical advisory in the
 * maplibre-gl it bundles. The six Plot.ts consumers that pages render are
 * driven here with synthetic data in a real browser, and each must produce
 * Plotly's SVG with the traces it was given. A future Plotly upgrade that
 * renders a blank or partial chart fails this spec rather than shipping.
 *
 * Every API call is stubbed: a port-anchored catch-all answers 404 first, so
 * nothing from a local data directory can reach the page. Set
 * PLOTLY_CHART_CAPTURE_DIR to also save one screenshot per chart, which is how
 * the upgrade was compared against main.
 */
import { expect, test, type Locator, type Page, type Route } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import path from 'node:path'
import { bypassSetup, waitForReactHydration } from './helpers'

const CAPTURE_DIR = process.env.PLOTLY_CHART_CAPTURE_DIR

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function stubEverything(page: Page) {
  // Registered first, so the specific stubs below take precedence over it.
  await page.route(/:\d+\/api\//, (route: Route) => route.fulfill(json({ detail: 'stubbed' }, 404)))
  await bypassSetup(page)
  await page.route(/:\d+\/api\/samples(\?.*)?$/, (route) =>
    route.fulfill(
      json([
        {
          id: 1,
          name: 'synthetic.txt',
          db_path: 'samples/sample_1.db',
          file_format: '23andme_v5',
          file_hash: 'hash1',
          notes: null,
          date_collected: null,
          source: null,
          extra: null,
          created_at: '2026-05-01T00:00:00',
          updated_at: null,
        },
      ]),
    ),
  )
  await page.route(/:\d+\/api\/individuals(\?.*)?$/, (route) => route.fulfill(json([])))
}

async function capture(chart: Locator, name: string) {
  if (!CAPTURE_DIR) return
  mkdirSync(CAPTURE_DIR, { recursive: true })
  await chart.screenshot({ path: path.join(CAPTURE_DIR, `${name}.png`), animations: 'disabled' })
}

/** The chart drew Plotly's SVG with at least ``traces`` traces of ``kind``. */
async function expectPlotlyTraces(chart: Locator, kind: 'scatter' | 'bar' | 'pie', traces: number) {
  await expect(chart.locator('.main-svg').first()).toBeVisible()
  const layer = { scatter: '.scatterlayer .trace', bar: '.barlayer .trace', pie: '.pielayer .trace' }[kind]
  await expect.poll(() => chart.locator(layer).count()).toBeGreaterThanOrEqual(traces)
}

const POPULATIONS = ['AFR', 'EUR', 'EAS', 'SAS', 'AMR']
const POPULATION_LABELS: Record<string, string> = {
  AFR: 'African',
  EUR: 'European',
  EAS: 'East Asian',
  SAS: 'South Asian',
  AMR: 'Admixed American',
}
const FRACTIONS: Record<string, number> = { AFR: 0.1, EUR: 0.55, EAS: 0.15, SAS: 0.12, AMR: 0.08 }

async function stubAncestry(page: Page) {
  await page.route('**/api/analysis/ancestry/findings**', (route) =>
    route.fulfill(
      json({
        top_population: 'EUR',
        pc_scores: [0.05, -0.02],
        population_distances: Object.fromEntries(POPULATIONS.map((p, i) => [p, 0.01 * (i + 1)])),
        admixture_fractions: FRACTIONS,
        population_ranking: POPULATIONS.map((population, i) => ({ population, distance: 0.01 * (i + 1) })),
        snps_used: 5000,
        snps_total: 5000,
        coverage_fraction: 1,
        projection_time_ms: 12,
        is_sufficient: true,
        classification_status: 'confident',
        quality_flags: [],
        evidence_level: 3,
        finding_text: 'Inferred ancestry: synthetic',
        confidence: 0.9,
        missing_aim_rate: 0,
        admixture_method: 'nnls',
        n_pcs_used: 2,
        nnls_fractions: FRACTIONS,
        knn_fractions: FRACTIONS,
        nnls_ci_low: Object.fromEntries(POPULATIONS.map((p) => [p, Math.max(0, FRACTIONS[p] - 0.02)])),
        nnls_ci_high: Object.fromEntries(POPULATIONS.map((p) => [p, Math.min(1, FRACTIONS[p] + 0.02)])),
      }),
    ),
  )
  await page.route('**/api/analysis/ancestry/pca-coordinates**', (route) =>
    route.fulfill(
      json({
        user: [0.05, -0.02],
        reference_samples: Object.fromEntries(
          POPULATIONS.map((p, i) => [
            p,
            [0, 1, 2, 3].map((j) => [0.1 * i + 0.01 * j, -0.05 * i + 0.01 * j]),
          ]),
        ),
        centroids: Object.fromEntries(POPULATIONS.map((p, i) => [p, [0.1 * i, -0.05 * i]])),
        population_labels: POPULATION_LABELS,
        n_components: 2,
        pc_labels: ['PC1', 'PC2'],
        top_population: 'EUR',
      }),
    ),
  )
  await page.route('**/api/analysis/ancestry/haplogroups**', (route) =>
    route.fulfill(json({ assignments: [] })),
  )
  // The pie is drawn in the local-ancestry (Tier 2) section, so it needs a
  // completed LAI result.
  await page.route('**/api/analysis/ancestry/lai/status**', (route) =>
    route.fulfill(
      json({ bundle_downloaded: true, java_available: true, lai_available: true, message: 'Ready' }),
    ),
  )
  await page.route('**/api/analysis/ancestry/lai/*/results**', (route) =>
    route.fulfill(
      json({
        global_ancestry: Object.fromEntries(
          POPULATIONS.map((p) => [
            p,
            {
              fraction: FRACTIONS[p],
              percentage: FRACTIONS[p] * 100,
              display_name: POPULATION_LABELS[p],
              color: null,
              confidence: 0.9,
            },
          ]),
        ),
        chromosome_painting: {
          chr1: [
            {
              start: 0,
              end: 248_956_422,
              n_snps: 1000,
              hap0: 'EUR',
              hap1: 'AFR',
              hap0_color: null,
              hap1_color: null,
            },
          ],
        },
        metadata: { windows: 1, source: 'plotly-charts-e2e' },
        created_at: '2026-07-13T00:00:00Z',
        coverage_telemetry: null,
      }),
    ),
  )
  await page.route('**/api/analysis/ancestry/lai/*/progress**', (route) =>
    route.fulfill(json(null)),
  )
}

test.beforeEach(async ({ page }) => {
  await stubEverything(page)
})

test.describe('Plotly charts render after the plotly.js 4 upgrade (#2418)', () => {
  test('ancestry: PCA scatter, admixture bar and pie', async ({ page }) => {
    await stubAncestry(page)
    await page.goto('/ancestry?sample_id=1')
    await waitForReactHydration(page)

    const pca = page.getByTestId('pca-scatter')
    await expectPlotlyTraces(pca, 'scatter', POPULATIONS.length)
    await capture(pca, 'pca-scatter')

    const admixture = page.getByTestId('admixture-bar')
    await expectPlotlyTraces(admixture, 'bar', 1)
    await capture(admixture, 'admixture-bar')

    const pie = page.getByTestId('ancestry-pie-chart')
    await expectPlotlyTraces(pie, 'pie', 1)
    await capture(pie, 'ancestry-pie-chart')
  })

  test('gene detail: population allele-frequency chart', async ({ page }) => {
    await page.route('**/api/genes/GENE1**', (route) =>
      route.fulfill(
        json({
          gene_symbol: 'GENE1',
          uniprot: null,
          uniprot_error: null,
          phenotypes: [],
          literature: [],
          literature_errors: [],
          variants: [],
          population_af: [
            {
              rsid: 'rs1000',
              hgvs_protein: null,
              gnomad_af_global: 0.42,
              gnomad_af_afr: 0.3,
              gnomad_af_amr: 0.4,
              gnomad_af_asj: 0.45,
              gnomad_af_eas: 0.1,
              gnomad_af_eur: 0.44,
              gnomad_af_fin: 0.38,
              gnomad_af_sas: 0.35,
            },
          ],
        }),
      ),
    )
    await page.goto('/genes/GENE1?sample_id=1')
    await waitForReactHydration(page)

    const chart = page.getByTestId('population-af-chart')
    await expectPlotlyTraces(chart, 'bar', 1)
    await capture(chart, 'population-af-chart')
  })

  test('dashboard: chromosome bar chart and heterozygosity histogram', async ({ page }) => {
    const chromosomes = ['1', '2', '3', 'X']
    await page.route(/:\d+\/api\/variants\/qc-stats/, (route) =>
      route.fulfill(
        json({
          total_variants: 4000,
          called_variants: 3900,
          nocall_variants: 100,
          het_count: 1300,
          hom_count: 2600,
          call_rate: 0.975,
          heterozygosity_rate: 0.33,
          per_chromosome: chromosomes.map((chrom, i) => ({
            chrom,
            total: 1000 - 100 * i,
            het_count: 320 - 30 * i,
            hom_count: 650 - 60 * i,
            nocall_count: 30 - 5 * i,
          })),
        }),
      ),
    )
    await page.route(/:\d+\/api\/analysis\/qc\/metrics/, (route) =>
      route.fulfill(
        json({
          computed: true,
          call_rate: 0.975,
          call_rate_pass: true,
          heterozygosity_rate: 0.33,
          ti_tv_ratio: 2.1,
          total_variants: 4000,
          called_variants: 3900,
          nocall_variants: 100,
          genetic_sex: null,
          recorded_sex: null,
          sex_check: null,
          het_outlier_z: 0.2,
          het_outlier_status: 'normal',
        }),
      ),
    )
    // The dashboard has no h1 once a sample is selected, so wait on the charts
    // themselves rather than waitForReactHydration.
    await page.goto('/?sample_id=1')
    // The QC charts are drawn only once the collapsed panel is opened.
    const qc = page.getByRole('region', { name: 'Sample quality control' })
    await qc.getByRole('button', { name: /Sample QC/ }).click()

    const charts = qc.locator('.js-plotly-plot')
    await expect.poll(() => charts.count()).toBeGreaterThanOrEqual(2)
    const bar = charts.nth(0)
    const histogram = charts.nth(1)
    await expectPlotlyTraces(bar, 'bar', 1)
    await expectPlotlyTraces(histogram, 'bar', 1)
    await capture(bar, 'chromosome-bar-chart')
    await capture(histogram, 'heterozygosity-histogram')
  })
})
