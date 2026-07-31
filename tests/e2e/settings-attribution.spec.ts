import AxeBuilder from '@axe-core/playwright'
import { test, expect } from '@playwright/test'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
  await page.route('**/api/updates/app-update', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        current_version: '0.2.0',
        latest_version: '0.2.0',
        update_available: false,
        release_url: null,
        error: null,
      }),
    }),
  )
})

test('About distinguishes source licensing from third-party data attribution (#2016)', async ({
  page,
}) => {
  await page.goto('/settings/about')
  await waitForReactHydration(page)

  await expect(
    page.getByRole('heading', { name: 'About Yeliztli' }),
  ).toBeVisible()
  await expect(page.getByText(/Yeliztli source code/i)).toContainText('MIT License')
  await expect(page.getByText(/Bundled reference data/i)).toContainText(
    'CC0 / CC-BY-4.0 / CC-BY-SA-4.0',
  )

  await page.getByText('Read the full MIT source license').click()
  await expect(page.getByLabel('Full MIT source license')).toHaveValue(
    /Permission is hereby granted, free of charge/,
  )

  await page.getByText('Read the full third-party data attribution').click()
  const attribution = page.getByLabel('Full third-party data attribution')
  await expect(attribution).toBeVisible()
  await expect(attribution).toHaveValue(/AlphaMissense/)
  await expect(attribution).toHaveValue(/CC-BY-4.0/)
  await expect(attribution).toHaveValue(/PharmGKB/)
  await expect(attribution).toHaveValue(/CC-BY-SA 4.0/)

  await expect(
    page.getByRole('link', { name: 'MIT source license (LICENSE)' }),
  ).toHaveAttribute(
    'href',
    'https://github.com/bioedca/Yeliztli/blob/main/LICENSE',
  )
  await expect(
    page.getByRole('link', { name: 'Third-party data attribution (NOTICE)' }),
  ).toHaveAttribute(
    'href',
    'https://github.com/bioedca/Yeliztli/blob/main/NOTICE',
  )
  await expect(
    page.getByRole('link', { name: 'Data sources and attribution documentation' }),
  ).toHaveAttribute(
    'href',
    'https://bioedca.github.io/Yeliztli/attribution/',
  )
  await expect(
    page.getByRole('link', { name: 'External inputs licensing policy' }),
  ).toHaveAttribute(
    'href',
    'https://bioedca.github.io/Yeliztli/external-inputs-strategy/',
  )

  const accessibility = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze()
  expect(accessibility.violations).toEqual([])
})
