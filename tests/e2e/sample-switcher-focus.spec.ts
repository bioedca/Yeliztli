import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { bypassSetup } from './helpers'

// Empty, synthetic sample metadata: this flow does not need variant data.
const samples = [1, 2, 5].map((id) => ({
  id,
  name: `Keyboard sample ${id}`,
  file_format: '23andme_v5',
  created_at: null,
}))
const individual = { id: 10, display_name: 'Keyboard individual' }
const detail = {
  ...individual,
  linked_samples: samples.slice(0, 2).map((sample) => ({ ...sample, relationship: 'self' })),
}

async function openTree(page: Page) {
  const trigger = page.getByRole('button', { name: /switch sample/i })
  await trigger.focus()
  await page.keyboard.press('Enter')
  const tree = page.getByRole('tree', { name: 'Individuals and samples' })
  await expect(tree).toBeVisible()
  await expect(tree.getByRole('treeitem', { name: 'Keyboard individual, 2 samples' })).toBeVisible()
  await expect(tree.locator('[role="treeitem"]:focus')).toHaveCount(1)
  return { trigger, tree }
}

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/samples', (route) => route.fulfill({ json: samples }))
  await page.route('**/api/individuals', (route) => route.fulfill({ json: [individual] }))
  await page.route('**/api/individuals/10', (route) => route.fulfill({ json: detail }))
})

for (const direction of ['Tab', 'Shift+Tab'] as const) {
  test(`${direction} leaves the complete selector and closes it without restoring focus`, async ({ page }) => {
    await page.goto('/settings/general?sample_id=1')
    const { trigger, tree } = await openTree(page)
    if (direction === 'Shift+Tab') {
      await page.keyboard.press(direction)
      await expect(trigger).toBeFocused()
      await expect(tree).toBeVisible()
      await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    }

    await page.keyboard.press(direction)

    const destination = direction === 'Tab'
      ? page.getByTestId('command-palette-trigger')
      : page.getByRole('link', { name: 'Yeliztli', exact: true })
    await expect(destination).toBeFocused()
    await expect(tree).toHaveCount(0)
    await expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await expect(destination).toBeFocused()
  })
}

test('internal navigation, Escape, and selection keep their focus behavior', async ({ page }) => {
  await page.goto('/settings/general?sample_id=1')
  const { trigger, tree } = await openTree(page)
  await page.keyboard.press('Home')
  const parent = tree.getByTestId('individual-row-10')
  await expect(parent).toBeFocused()
  await page.keyboard.press('ArrowLeft')
  await expect(parent).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('ArrowRight')
  await expect(parent).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(tree.getByTestId('sample-row-1')).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(tree.getByTestId('sample-row-2')).toBeFocused()
  await expect(tree).toBeVisible()
  await expect(tree.locator('[role="treeitem"][tabindex="0"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(tree).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(tree).toBeVisible()
  await page.keyboard.press('End')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/sample_id=5/)
  await expect(tree).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('the expanded tree passes its scoped accessibility audit', async ({ page }) => {
  await page.goto('/settings/general?sample_id=1')
  const { tree } = await openTree(page)
  await expect(tree.getByTestId('sample-row-1')).toBeVisible()
  const accessibility = await new AxeBuilder({ page })
    .include('[role="tree"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(accessibility.passes.length).toBeGreaterThan(0)
  expect(accessibility.violations).toEqual([])
})

test('unknown focus destinations and window blur do not dismiss the tree', async ({ page }) => {
  await page.goto('/settings/general?sample_id=1')
  const { trigger, tree } = await openTree(page)
  // Dispatching these events covers their envelopes without pretending that
  // a headless browser can reproduce an operating-system window switch.
  await tree.locator('[role="treeitem"]:focus').evaluate((row) => {
    row.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }))
    window.dispatchEvent(new FocusEvent('blur'))
    window.dispatchEvent(new FocusEvent('focus'))
    row.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: window }))
  })
  await expect(tree).toBeVisible()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('End')
  await expect(tree.getByTestId('sample-row-5')).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByTestId('command-palette-trigger')).toBeFocused()
  await expect(tree).toHaveCount(0)
})

test('late individual details regroup a focused sample without closing the tree', async ({ page }) => {
  let releaseDetail!: () => void
  const gate = new Promise<void>((resolve) => { releaseDetail = resolve })
  await page.route('**/api/individuals/10', async (route) => {
    await gate
    await route.fulfill({ json: detail })
  })
  try {
    await page.goto('/settings/general?sample_id=1')
    const trigger = page.getByRole('button', { name: /switch sample/i })
    await trigger.focus()
    await page.keyboard.press('Enter')
    const tree = page.getByRole('tree', { name: 'Individuals and samples' })
    await expect(tree.locator('[data-tree-key="unassigned-1"]')).toBeFocused()

    releaseDetail()

    await expect(tree.locator('[data-tree-key="sample-1"]')).toBeVisible()
    await expect(tree.locator('[role="treeitem"]:focus')).toHaveCount(1)
    await expect(tree.locator('[data-tree-key="sample-1"], [data-tree-key="ind-10"]')
      .and(page.locator(':focus'))).toHaveCount(1)
    await expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await page.keyboard.press('Tab')
    await expect(page.getByTestId('command-palette-trigger')).toBeFocused()
    await expect(tree).toHaveCount(0)
  } finally {
    releaseDetail()
  }
})
