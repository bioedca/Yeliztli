import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup } from './helpers'

test('collection date can be set, preserved by another edit, and cleared after reload', async ({ page }) => {
  await bypassSetup(page)
  let sample = {
    id: 1,
    name: 'synthetic-metadata.txt',
    db_path: 'samples/sample_1.db',
    file_format: '23andme_v5',
    file_hash: 'synthetic-metadata-fixture',
    notes: 'Original note',
    date_collected: null as string | null,
    source: 'Synthetic lab',
    extra: {},
    created_at: '2025-01-01T00:00:00Z',
    updated_at: null,
  }
  const patches: unknown[] = []
  const json = (body: unknown, status = 200) => ({
    status, contentType: 'application/json', body: JSON.stringify(body),
  })
  await page.route('**/api/individuals', route => route.fulfill(json([])))
  await page.route('**/api/samples', route => route.fulfill(json([sample])))
  await page.route('**/api/samples/1', async route => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON()
      patches.push(body)
      // Match the API's existing rejection of an empty-string date. Backend
      // CRUD tests independently exercise persistence through the real handler.
      if (body.date_collected === '') {
        await route.fulfill(json({ detail: 'Invalid date format' }, 422))
        return
      }
      sample = { ...sample, ...body }
    }
    await route.fulfill(json(sample))
  })

  async function openEditor() {
    await page.getByRole('button', { name: 'Edit synthetic-metadata.txt' }).click()
    await expect(page.getByLabel('Date Collected')).toBeVisible()
  }
  async function saveWithKeyboard() {
    const save = page.getByRole('button', { name: 'Save Changes' })
    await expect(save).toBeEnabled()
    await save.focus()
    await expect(save).toBeFocused()
    await save.press('Enter')
    await expect(page.getByTestId('save-success')).toBeVisible()
    await expect(save).toBeDisabled()
  }

  await page.goto('/settings/general')
  await openEditor()
  await expect(page.getByLabel('Date Collected')).toHaveValue('')
  await page.getByLabel('Date Collected').fill('2025-01-15')
  await saveWithKeyboard()
  expect(patches).toEqual([{ date_collected: '2025-01-15' }])
  await page.reload()
  await openEditor()
  await expect(page.getByLabel('Date Collected')).toHaveValue('2025-01-15')

  await page.getByLabel('Notes', { exact: true }).fill('Updated note')
  await saveWithKeyboard()
  expect(patches).toEqual([{ date_collected: '2025-01-15' }, { notes: 'Updated note' }])
  await page.reload()
  await openEditor()
  await expect(page.getByLabel('Date Collected')).toHaveValue('2025-01-15')
  await expect(page.getByLabel('Notes', { exact: true })).toHaveValue('Updated note')

  await page.getByLabel('Date Collected').fill('')
  await saveWithKeyboard()
  expect(patches).toEqual([
    { date_collected: '2025-01-15' }, { notes: 'Updated note' }, { date_collected: null },
  ])
  await page.reload()
  await openEditor()
  await expect(page.getByLabel('Date Collected')).toHaveValue('')
  await expect(page.getByLabel('Notes', { exact: true })).toHaveValue('Updated note')
  await expect(page.getByLabel('Source / Lab')).toHaveValue('Synthetic lab')
  await expect(page.getByRole('button', { name: 'Save Changes' })).toBeDisabled()
  const accessibility = await new AxeBuilder({ page })
    .include('[data-testid="sample-edit-form"]')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(accessibility.violations).toEqual([])
})
