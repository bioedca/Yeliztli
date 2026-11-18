/** Native paint must agree with readable selected text, not just computed CSS (#2678). */
import AxeBuilder from '@axe-core/playwright'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

function deferred() {
  let release!: () => void
  const promise = new Promise<void>(resolve => { release = resolve })
  return { promise, release }
}

async function mockSettings(page: Page) {
  const sample = {
    id: 1, name: 'synthetic-native-select.txt', db_path: 'synthetic-sample.db',
    file_format: '23andme_v5', file_hash: 'synthetic-native-select', notes: null,
    date_collected: null, source: null, extra: null,
    created_at: '2026-06-17T12:00:00Z', updated_at: '2026-06-17T12:00:00Z',
  }
  const person = { id: 10, display_name: 'Synthetic A', notes: null, biological_sex: null,
    created_at: null, updated_at: null }
  let linked = false
  const detail = () => ({ ...person, linked_samples: linked ? [{ ...sample, vendor: null, is_merged: false }] : [],
    aggregated_findings_count: 0 })
  const gates = { 'link-sample': deferred(), 'unlink-sample': deferred() }
  const posts: { action: string; body: unknown }[] = []
  const unexpected: string[] = []
  const replies: Record<string, unknown> = {
    '/api/auth/status': { auth_enabled: false, has_password: false, authenticated: true },
    '/api/setup/status': { needs_setup: false, disclaimer_accepted: true, has_databases: true,
      required_dbs_ready: true, db_readiness: [], has_samples: true, data_dir: 'synthetic-fixture' },
    '/api/samples': [sample], '/api/samples/1': sample,
    '/api/backup/estimate': { sample_bytes: 4096, config_bytes: 0, reference_bytes: 8192,
      total_without_ref_bytes: 4096, total_with_ref_bytes: 12288,
      total_without_ref_mb: 0.004, total_with_ref_mb: 0.012, sample_count: 1, reference_db_count: 1 },
    '/api/preferences/update-check-interval': { update_check_interval: 'off' },
    '/api/updates/status': [], '/api/updates/history': [], '/api/updates/prompts': [],
    '/api/updates/check': { available: [], up_to_date: [], errors: [], checked_at: null },
    '/api/updates/app-update': { update_available: false, current_version: '0.2.0',
      latest_version: null, release_url: null, release_notes: null, error: null },
  }
  await page.route(/^https?:\/\/[^/]+\/api\//, async route => {
    const request = route.request(), pathname = new URL(request.url()).pathname
    const match = /^\/api\/individuals\/10\/(link-sample|unlink-sample)$/.exec(pathname)
    if (request.method() === 'POST' && match) {
      const action = match[1] as keyof typeof gates
      posts.push({ action, body: request.postDataJSON() })
      await gates[action].promise
      linked = action === 'link-sample'
      return route.fulfill({ json: detail() })
    }
    if (request.method() === 'GET') {
      if (pathname === '/api/individuals') return route.fulfill({ json: [{ ...person,
        sample_count: linked ? 1 : 0, vendors: [], last_activity: null }] })
      if (pathname === '/api/individuals/10') return route.fulfill({ json: detail() })
      if (Object.hasOwn(replies, pathname)) return route.fulfill({ json: replies[pathname] })
    }
    unexpected.push(`${request.method()} ${pathname}`)
    return route.abort()
  })
  return { gates, posts, unexpected }
}

async function readableNativeSelection(page: Page, control: Locator, info: TestInfo, label: string, dark: boolean) {
  await expect(control).toHaveCount(1)
  await expect(control).toBeVisible()
  await expect(control).toBeEnabled()
  await expect(control).toHaveAccessibleName('Individual:')
  await control.scrollIntoViewIfNeeded()
  await expect(control).toBeInViewport({ ratio: 1 })
  const computed = await control.evaluate(node => {
    const element = node as HTMLSelectElement, style = getComputedStyle(element)
    return { foreground: style.color, background: style.backgroundColor, appearance: style.appearance,
      scheme: style.colorScheme, font: style.font, opacity: style.opacity, disabled: element.disabled,
      selectedText: element.selectedOptions[0]?.textContent?.trim(),
      dark: document.documentElement.classList.contains('dark'),
      disabledAncestor: Boolean(element.closest(':disabled, [aria-disabled="true"], [inert]')) }
  })
  expect(computed.disabled).toBe(false)
  expect(computed.disabledAncestor).toBe(false)
  expect(computed.opacity).toBe('1')
  expect(computed.selectedText).toBeTruthy()
  expect(computed.dark).toBe(dark)
  const png = await control.screenshot()
  // WebKit can report the CSS dark background while painting a light native field.
  // Decode the actual PNG with the browser instead of adding a PNG dependency.
  const native = await page.evaluate(async ({ png, foreground }) => {
    const image = new Image()
    image.src = 'data:image/png;base64,' + png
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.width; canvas.height = image.height
    const context = canvas.getContext('2d')
    if (!context || image.width < 24 || image.height < 12) throw new Error('Native field image is unavailable')
    context.drawImage(image, 0, 0)
    const data = context.getImageData(0, 0, image.width, image.height).data
    const colors = new Map<string, number>()
    const mx = Math.max(2, Math.ceil(image.width * 0.04)), my = Math.max(2, Math.ceil(image.height * 0.08))
    let eligible = 0
    for (let y = my; y < image.height - my; y++) for (let x = mx; x < image.width - mx; x++) {
      const i = (y * image.width + x) * 4
      if (data[i + 3] !== 255) continue
      const key = [data[i], data[i + 1], data[i + 2]].join(',')
      colors.set(key, (colors.get(key) ?? 0) + 1); eligible++
    }
    const dominant = [...colors.entries()].sort((a, b) => b[1] - a[1])[0]
    if (!dominant || eligible === 0) throw new Error('Native field background is empty')
    context.clearRect(0, 0, 1, 1); context.fillStyle = foreground; context.fillRect(0, 0, 1, 1)
    const foregroundRgba = [...context.getImageData(0, 0, 1, 1).data]
    const fg = foregroundRgba.slice(0, 3), bg = dominant[0].split(',').map(Number)
    // Count actual foreground-like paint in the label area, excluding border/arrow.
    const distance = (rgb: number[], target: number[]) => rgb.reduce((sum, v, i) => sum + (v - target[i]) ** 2, 0)
    let ink = 0
    for (let y = Math.ceil(image.height * 0.12); y < image.height * 0.88; y++) {
      for (let x = Math.ceil(image.width * 0.06); x < image.width * 0.75; x++) {
        const i = (y * image.width + x) * 4, rgb = [data[i], data[i + 1], data[i + 2]]
        if (data[i + 3] === 255 && distance(rgb, fg) < distance(rgb, bg)) ink++
      }
    }
    // W3C WCAG 2.2 relative luminance/contrast definitions; use solid CSS ink,
    // not antialiased edge colors, against the actual native background fill.
    const luminance = (rgb: number[]) => rgb.map(n => n / 255)
      .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
      .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0)
    const [low, high] = [luminance(fg), luminance(bg)].sort((a, b) => a - b)
    return { foregroundRgba, background: bg, contrast: (high + 0.05) / (low + 0.05),
      backgroundCoverage: dominant[1] / eligible, ink, width: image.width, height: image.height }
  }, { png: png.toString('base64'), foreground: computed.foreground })
  const audit = await new AxeBuilder({ page }).include('[data-testid="assign-individual-select-1"]')
    .withRules(['color-contrast']).analyze()
  await info.attach(`${label}-native-paint`, { body: png, contentType: 'image/png' })
  await info.attach(`${label}-measurements`, { body: Buffer.from(JSON.stringify({ computed, native, audit }, null, 2)),
    contentType: 'application/json' })
  expect(native.foregroundRgba[3]).toBe(255)
  expect(native.backgroundCoverage, 'A uniform native field fill must dominate the interior').toBeGreaterThan(0.5)
  expect(native.ink, 'The selected label must have actual foreground-like paint').toBeGreaterThan(0)
  expect(native.contrast, 'Actual native field background must contrast with selected text').toBeGreaterThanOrEqual(4.5)
  expect(audit.violations).toEqual([])
  expect(audit.incomplete).toEqual([])
  expect(audit.passes.find(rule => rule.id === 'color-contrast')?.nodes.length).toBe(1)
}

test.beforeEach(async ({ page, baseURL }) => {
  const dist = path.resolve(__dirname, '../../frontend/dist')
  expect(existsSync(path.join(dist, 'index.html')), 'Build the production frontend first').toBe(true)
  if (!baseURL) throw new Error('Production asset routes need baseURL')
  const origin = new URL(baseURL).origin
  await page.route(url => url.origin === origin && url.pathname === '/settings/general',
    route => route.fulfill({ path: path.join(dist, 'index.html') }))
  await page.route(url => url.origin === origin && url.pathname.startsWith('/assets/'), route => {
    const asset = new URL(route.request().url()).pathname.slice('/assets/'.length)
    expect(asset).toMatch(/^[A-Za-z0-9_.-]+$/)
    return route.fulfill({ path: path.join(dist, 'assets', asset) })
  })
})

test.afterEach(async ({ page }) => {
  await expect(page.locator('link[rel="stylesheet"][href^="/assets/"]')).toHaveCount(1)
  await expect(page.locator('script[src*="@vite/client"]')).toHaveCount(0)
})

for (const theme of ['light', 'dark'] as const) {
  test(`native Individual selection stays readable through assignment in ${theme} theme`, async ({ page }, info) => {
    const fixture = await mockSettings(page)
    try {
      // App preference must work even when the OS preference is the opposite.
      await page.emulateMedia({ colorScheme: theme === 'dark' ? 'light' : 'dark' })
      await page.addInitScript(value => localStorage.setItem('gi-theme', value), theme)
      await page.goto('/settings/general')
      const control = page.getByTestId('assign-individual-select-1')
      await expect(control.getByRole('option')).toHaveCount(3)
      await expect(control).toHaveValue('unassigned')
      await readableNativeSelection(page, control, info, 'unassigned', theme === 'dark')
      const linkResponse = page.waitForResponse(response =>
        response.url().endsWith('/individuals/10/link-sample') && response.request().method() === 'POST')
      await control.selectOption('10')
      await expect(control).toBeDisabled()
      fixture.gates['link-sample'].release()
      const linked = await linkResponse
      expect(linked.status()).toBe(200)
      expect(linked.request().postDataJSON()).toEqual({ sample_id: 1 })
      expect((await linked.json()).linked_samples.map((sample: { id: number }) => sample.id)).toEqual([1])
      await expect(control).toHaveValue('10')
      await readableNativeSelection(page, control, info, 'assigned', theme === 'dark')
      const unlinkResponse = page.waitForResponse(response =>
        response.url().endsWith('/individuals/10/unlink-sample') && response.request().method() === 'POST')
      await control.selectOption('unassigned')
      await expect(control).toBeDisabled()
      fixture.gates['unlink-sample'].release()
      const unlinked = await unlinkResponse
      expect(unlinked.status()).toBe(200)
      expect(unlinked.request().postDataJSON()).toEqual({ sample_id: 1 })
      expect((await unlinked.json()).linked_samples).toEqual([])
      await expect(control).toHaveValue('unassigned')
      await readableNativeSelection(page, control, info, 'unlinked', theme === 'dark')
      expect(fixture.posts).toEqual([{ action: 'link-sample', body: { sample_id: 1 } },
        { action: 'unlink-sample', body: { sample_id: 1 } }])
      expect(fixture.unexpected).toEqual([])
    } finally { fixture.gates['link-sample'].release(); fixture.gates['unlink-sample'].release() }
  })
}

test('native Individual selection follows system theme changes without reload', async ({ page }, info) => {
  const fixture = await mockSettings(page)
  await page.addInitScript(() => localStorage.setItem('gi-theme', 'system'))
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/settings/general')
  const control = page.getByTestId('assign-individual-select-1')
  await expect(control.getByRole('option')).toHaveCount(3)
  for (const scheme of ['light', 'dark', 'light'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await expect.poll(() => page.locator('html').evaluate(node => node.classList.contains('dark'))).toBe(scheme === 'dark')
    await expect(control).toHaveValue('unassigned')
    await readableNativeSelection(page, control, info, `system-${scheme}`, scheme === 'dark')
  }
  expect(fixture.posts).toEqual([])
  expect(fixture.unexpected).toEqual([])
})
