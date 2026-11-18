/** Keep the development-only WebKit hover regression separate from built CSS (#2667). */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator } from '@playwright/test'
import { bypassSetup } from './helpers'

async function backgroundColors(button: Locator) {
  return button.evaluate(node => {
    const style = getComputedStyle(node)
    const token = style.getPropertyValue('--color-destructive').trim()
    const canvas = document.createElement('canvas').getContext('2d')
    if (!canvas || !token) throw new Error('Color conversion and destructive token are required')
    const rgba = (color: string) => {
      canvas.clearRect(0, 0, 1, 1)
      canvas.fillStyle = color
      canvas.fillRect(0, 0, 1, 1)
      return Array.from(canvas.getImageData(0, 0, 1, 1).data)
    }
    return {
      actual: rgba(style.backgroundColor),
      expected: rgba(`color-mix(in oklab, ${token} 10%, transparent)`),
    }
  })
}

for (const colorScheme of ['light', 'dark'] as const) {
  test(`Delete applies its tint with Vite development CSS in ${colorScheme} theme`, async ({ page, browserName }) => {
    await page.emulateMedia({ colorScheme })
    const unexpected: string[] = []
    await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
      unexpected.push(new URL(route.request().url()).pathname)
      await route.abort()
    })
    await bypassSetup(page)
    await page.route(/\/api\/samples$/, route => route.fulfill({ json: [{
      id: 2, name: 'Synthetic sample', db_path: 'synthetic-fixture', file_format: null,
      file_hash: null, notes: null, date_collected: null, source: null, extra: null,
      created_at: null, updated_at: null,
    }] }))
    await page.route(/\/api\/individuals$/, route => route.fulfill({ json: [] }))
    await page.route(/\/api\/annotation\/active\/\d+$/, route =>
      route.fulfill({ status: 404, json: { detail: 'No active job' } }))
    await page.route(/\/api\/variants\/count\?/, route => route.fulfill({ json: { total: 0, filtered: false } }))
    await page.route(/\/api\/overlays$/, route => route.fulfill({ json: { items: [{
      id: 1, name: 'Synthetic overlay', description: 'Synthetic UI fixture',
      file_type: 'bed', column_names: ['label'], region_count: 1, created_at: null,
    }], total: 1 } }))

    await page.goto('/overlays?sample_id=2')
    // Do not fulfill document/assets routes: this case must use Vite's nested CSS.
    await expect(page.locator('script[src*="/@vite/client"]')).toHaveCount(1)
    await expect(page.locator('style[data-vite-dev-id$="/src/index.css"]')).toHaveCount(1)
    await expect(page.locator('link[rel="stylesheet"][href^="/assets/"]')).toHaveCount(0)
    const button = page.getByRole('button', { name: 'Delete', exact: true })
    await expect(button).toHaveCount(1)
    await expect(button).toBeEnabled()
    await page.mouse.move(0, 0)
    await expect(button).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    const supportsHover = await page.evaluate(() => matchMedia('(hover: hover)').matches)
    if (browserName !== 'firefox') expect(supportsHover).toBe(true)
    await button.hover()
    await expect.poll(() => button.evaluate(node => node.matches(':hover'))).toBe(true)
    if (supportsHover) {
      const colors = await backgroundColors(button)
      expect(colors.expected).toHaveLength(4)
      expect(colors.expected[3]).toBe(26)
      await expect.poll(async () => (await backgroundColors(button)).actual).toEqual(colors.expected)
    } else {
      await expect(button).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    }
    const audit = await new AxeBuilder({ page })
      .include('#main-content button[class*="hover:bg-destructive"]')
      .withRules(['color-contrast']).analyze()
    expect(audit.violations).toEqual([])
    expect(audit.passes.find(rule => rule.id === 'color-contrast')?.nodes.length).toBeGreaterThan(0)
    await page.mouse.move(0, 0)
    await expect(button).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    expect(unexpected).toEqual([])
  })
}
