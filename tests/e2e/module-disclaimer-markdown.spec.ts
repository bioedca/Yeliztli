import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

interface Resource {
  name: string
  href: string
}

interface ModuleCase {
  name: string
  path: string
  testId: string
  title: string
  resources: Resource[]
  mockData: (page: Page) => Promise<void>
}

const jsonRoute = (payload: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

const emptyCancerData = async (page: Page) => {
  await page.route('**/api/analysis/cancer/variants**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/cancer/prs**', (route) =>
    route.fulfill(
      jsonRoute({ items: [], total: 0, sufficient_count: 0, insufficient_traits: [] }),
    ),
  )
  await page.route('**/api/analysis/cancer/absolute-risk**', (route) =>
    route.fulfill(
      jsonRoute({
        consented: false,
        opt_in_required: true,
        opt_in_prompt: 'Show optional absolute-risk context.',
        disclaimer: 'For research and educational use only.',
      }),
    ),
  )
}

const emptyCardiovascularData = async (page: Page) => {
  await page.route('**/api/analysis/cardiovascular/variants**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0 })),
  )
  await page.route('**/api/analysis/cardiovascular/fh-status**', (route) =>
    route.fulfill(
      jsonRoute({
        status: 'Negative',
        summary_text: 'No FH-associated findings.',
        affected_genes: [],
        variant_count: 0,
        has_homozygous: false,
        highest_evidence_level: 0,
        variants: [],
      }),
    ),
  )
}

const emptyCarrierData = async (page: Page) => {
  await page.route('**/api/analysis/carrier/variants**', (route) =>
    route.fulfill(jsonRoute({ items: [], total: 0, genes_with_findings: [] })),
  )
}

const MODULES: ModuleCase[] = [
  {
    name: 'Cancer',
    path: '/cancer?sample_id=1',
    testId: 'cancer-disclaimer',
    title: 'About Cancer Predisposition Results',
    resources: [
      { name: 'National Cancer Institute', href: 'https://www.cancer.gov/about-cancer/genetics' },
      { name: 'Genetic counselor finder', href: 'https://example.org/find/' },
    ],
    mockData: emptyCancerData,
  },
  {
    name: 'Cardiovascular',
    path: '/cardiovascular?sample_id=1',
    testId: 'cardiovascular-disclaimer',
    title: 'About Cardiovascular Genetic Results',
    resources: [
      { name: 'Family Heart Foundation', href: 'https://familyheart.org/' },
      { name: 'Genetic counselor finder', href: 'https://example.org/find/' },
    ],
    mockData: emptyCardiovascularData,
  },
  {
    name: 'Carrier Status',
    path: '/carrier-status?sample_id=1',
    testId: 'carrier-disclaimer',
    title: 'About Carrier Status Results',
    resources: [
      { name: 'Carrier screening guidance', href: 'https://example.org/carrier/guidance' },
      { name: 'Genetic counselor finder', href: 'https://example.org/find/' },
    ],
    mockData: emptyCarrierData,
  },
]

function disclaimerText(resources: Resource[]): string {
  return [
    'Introductory disclaimer copy.',
    '**Please understand the following before reviewing:**',
    '1. **Important limitation.** Supporting explanation.',
    `**Resources:**\n${resources.map(({ name, href }) => `- ${name}: ${href}`).join('\n')}`,
  ].join('\n\n')
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

for (const moduleCase of MODULES) {
  test(`${moduleCase.name} renders formatted disclaimer text and resource links (#1782)`, async ({
    page,
  }) => {
    await moduleCase.mockData(page)
    await page.route(`**/api/analysis/${
      moduleCase.name === 'Carrier Status' ? 'carrier' : moduleCase.name.toLowerCase()
    }/disclaimer`, (route) =>
      route.fulfill(
        jsonRoute({
          title: moduleCase.title,
          text: disclaimerText(moduleCase.resources),
          ...(moduleCase.name === 'Carrier Status' ? { gene_notes: {} } : {}),
        }),
      ),
    )

    await page.goto(moduleCase.path)
    await waitForReactHydration(page)

    const panel = page.getByTestId(moduleCase.testId)
    const toggle = panel.getByRole('button', { name: moduleCase.title })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')

    await expect(panel.locator('strong').filter({ hasText: 'Important limitation.' })).toBeVisible()
    await expect(panel).not.toContainText('**')
    await expect(panel.getByRole('link')).toHaveCount(moduleCase.resources.length)

    for (const resource of moduleCase.resources) {
      const link = panel.getByRole('link', { name: resource.href })
      await expect(link).toHaveAttribute('href', resource.href)
      await expect(link).toHaveAttribute('target', '_blank')
      await expect(link).toHaveAttribute('rel', /\bnoopener\b/)
      await expect(link).toHaveAttribute('rel', /\bnoreferrer\b/)
    }

    const accessibility = await new AxeBuilder({ page })
      .include(`[data-testid="${moduleCase.testId}"]`)
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
}
