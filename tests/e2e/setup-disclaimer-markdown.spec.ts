import { expect, test } from '@playwright/test'
import { waitForReactHydration } from './helpers'

const jsonRoute = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})

const DISCLAIMER_TEXT = [
  'Yeliztli is an educational and research tool.',
  'Please read and understand the following before proceeding:',
  '1. **Not a diagnostic tool.** The information is for education and research only.',
  '2. **Not a substitute for professional medical advice.** Consult a qualified provider.',
].join('\n\n')

test('renders the setup consent disclaimer as a semantic ordered list (#1984)', async ({
  page,
}) => {
  await page.route('**/api/auth/status', (route) =>
    route.fulfill(
      jsonRoute({ auth_enabled: false, has_password: false, authenticated: true }),
    ),
  )
  await page.route('**/api/setup/status', (route) =>
    route.fulfill(
      jsonRoute({
        needs_setup: true,
        disclaimer_accepted: false,
        has_databases: false,
        required_dbs_ready: false,
        has_samples: false,
        data_dir: '/tmp/.yeliztli',
      }),
    ),
  )
  await page.route('**/api/setup/disclaimer', (route) =>
    route.fulfill(
      jsonRoute({
        title: 'Important Information About Yeliztli',
        text: DISCLAIMER_TEXT,
        accept_label: 'I Understand and Accept',
      }),
    ),
  )

  await page.goto('/setup')
  await waitForReactHydration(page)

  const disclaimer = page.getByRole('region', { name: 'Disclaimer text' })
  await expect(disclaimer).toBeVisible()
  await expect(disclaimer).not.toContainText('**')
  await expect(disclaimer.locator('ol')).toHaveCount(1)
  await expect(disclaimer.locator('li')).toHaveCount(2)
  await expect(disclaimer.locator('strong').filter({ hasText: 'Not a diagnostic tool.' })).toBeVisible()
  await expect(
    disclaimer.locator('strong').filter({
      hasText: 'Not a substitute for professional medical advice.',
    }),
  ).toBeVisible()
})
