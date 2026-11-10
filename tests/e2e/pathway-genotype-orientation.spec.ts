/** #2441: synthetic pathway rows exercise the shared note through real pages. */
import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

const modules = ['allergy', 'fitness', 'gene-health', 'methylation', 'nutrigenomics', 'skin', 'sleep', 'traits']
const noteStart = /Genotypes are shown as your array reported them/
const summary = {
  pathway_id: 'synthetic', pathway_name: 'Synthetic pathway', level: 'Standard',
  evidence_level: 1, called_snps: 1, total_snps: 1,
  missing_snps: [], no_call_snps: [], indeterminate_snps: [], pmids: [],
}
const snp = {
  rsid: 'rs2441000', gene: 'SYNTHETIC', variant_name: 'Synthetic variant name',
  genotype: 'GG', category: 'Standard', effect_summary: 'Synthetic display fixture.',
  evidence_level: 1, recommendation: null, pmids: [], coverage_note: null,
}

for (const module of modules) {
  for (const mode of [
    { name: 'desktop light', width: 1440, height: 1000, theme: 'light' },
    { name: 'mobile dark', width: 390, height: 844, theme: 'dark' },
  ]) {
    test(`${module} orientation note is readable and accessible in ${mode.name}`, async ({ page }) => {
      await page.setViewportSize({ width: mode.width, height: mode.height })
      await page.addInitScript(theme => localStorage.setItem('gi-theme', theme), mode.theme)
      await page.route(/^https?:\/\/[^/]+\/api\//, route => route.fulfill({
        status: 404, json: { detail: 'Unused endpoint in synthetic display test' },
      }))
      await bypassSetup(page)
      await mockFreshSampleState(page)
      const sample = { id: 1, name: 'Synthetic display sample', created_at: '2026-01-01T00:00:00Z' }
      await page.route('**/api/samples', route => route.fulfill({ json: [sample] }))
      await page.route('**/api/samples/1', route => route.fulfill({ json: sample }))
      await page.route('**/api/individuals', route => route.fulfill({ json: [] }))
      const apiModule = module.replace('-', '_')
      await page.route(`**/api/analysis/${apiModule}/pathways**`, route => route.fulfill({
        json: {
          items: [summary], total: 1, cross_context: [], cross_module: [],
          insufficient_data: [], compound_het: null, module_disclaimer: '',
        },
      }))
      await page.route(`**/api/analysis/${apiModule}/pathway/**`, route => route.fulfill({
        json: { ...summary, snp_details: [snp] },
      }))
      if (module === 'traits') {
        await page.route('**/api/analysis/traits/prs**', route => route.fulfill({
          json: { items: [], total: 0, module_disclaimer: '' },
        }))
        await page.route('**/api/analysis/traits/disclaimer', route => route.fulfill({
          json: { disclaimer: 'Synthetic display fixture.', evidence_cap: 2, research_use_only: true },
        }))
      }

      await page.goto(`/${module}?sample_id=1`)
      await waitForReactHydration(page)
      const card = page.getByRole('button', { name: /^Synthetic pathway —/ })
      await card.focus()
      await card.press('Enter')
      const dialog = page.getByRole('dialog', { name: 'Synthetic pathway pathway details' })
      await expect(dialog).toBeVisible()
      if (module === 'methylation') {
        await expect(dialog.getByText(noteStart)).toHaveCount(0)
        const advanced = dialog.getByRole('button', { name: /Advanced View/ })
        await advanced.press('Enter')
        await expect(advanced).toHaveAttribute('aria-expanded', 'true')
      }
      const note = dialog.getByText(noteStart)
      await expect(note).toHaveCount(1)
      await expect(note).toBeVisible()
      await expect(note).toContainText('complementary letters (A pairs with T, C with G)')
      await expect(note).toContainText('cannot be compared letter by letter with a genotype.')
      await expect(dialog.getByText('GG', { exact: true })).toBeVisible()
      const bounds = await note.boundingBox()
      expect(bounds).not.toBeNull()
      expect(bounds!.x).toBeGreaterThanOrEqual(0)
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(mode.width)
      expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
      expect(await page.locator('html').evaluate(element => element.classList.contains('dark')))
        .toBe(mode.theme === 'dark')
      const accessibility = await new AxeBuilder({ page })
        .include('[role="dialog"]')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze()
      expect(accessibility.violations).toEqual([])
      await page.keyboard.press('Escape')
      await expect(dialog).toHaveCount(0)
      await expect(card).toBeFocused()
    })
  }
}
