/**
 * Issues #2055 / #2059 — the Query Builder's multi-value operators (`between`,
 * `in`, `not in`) must reach `POST /api/query` as arrays, never as the
 * comma-joined string react-querybuilder stores by default, because the
 * backend translator rejects a string with HTTP 422.
 */

import { test, expect, type Page } from '@playwright/test'
import { bypassSetup, mockFreshSampleState, waitForReactHydration } from './helpers'

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

type Rule = { field: string; operator: string; value: unknown }
type QueryBody = { sample_id: number; filter: { combinator: string; rules: Rule[] } }

const FIELDS = {
  fields: [
    { name: 'chrom', type: 'text', label: 'Chrom' },
    { name: 'gene_symbol', type: 'text', label: 'Gene Symbol' },
    { name: 'gnomad_af_global', type: 'number', label: 'Gnomad Af Global' },
  ],
  operators: ['=', '!=', '<', '>', '<=', '>=', 'beginsWith', 'between', 'contains', 'endsWith', 'in', 'notIn', 'notNull', 'null'],
}

const EMPTY_PAGE = {
  items: [],
  total_matching: 0,
  next_cursor_chrom: null,
  next_cursor_pos: null,
  has_more: false,
  limit: 50,
}

async function stubQueryBuilder(page: Page): Promise<QueryBody[]> {
  const bodies: QueryBody[] = []
  await bypassSetup(page)
  await mockFreshSampleState(page)
  await page.route(/\/api\/variants\/count\?sample_id=\d+$/, (route) => route.fulfill(jsonRoute({ count: 1 })))
  await page.route(/\/api\/annotation\/active\/\d+$/, (route) =>
    route.fulfill(jsonRoute({ detail: 'No active job' }, 404)),
  )
  await page.route(/\/api\/query\/fields$/, (route) => route.fulfill(jsonRoute(FIELDS)))
  await page.route(/\/api\/saved-queries(\?|$)/, (route) => route.fulfill(jsonRoute({ queries: [] })))
  await page.route(/\/api\/preferences\/theme$/, (route) => {
    if (route.request().method() !== 'PUT') return route.fallback()
    const { theme } = route.request().postDataJSON() as { theme: string }
    return route.fulfill(jsonRoute({ theme }))
  })
  await page.route(/\/api\/query$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    bodies.push(route.request().postDataJSON() as QueryBody)
    return route.fulfill(jsonRoute(EMPTY_PAGE))
  })
  return bodies
}

async function addRule(page: Page, field: string, operator: string) {
  await page.getByRole('button', { name: '+ Rule' }).click()
  const rule = page.getByTestId('rule').last()
  await rule.getByTestId('fields').selectOption(field)
  await rule.getByTestId('operators').selectOption(operator)
  return rule
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
})

test('a between rule built in the UI is posted as a two-element array (#2055)', async ({ page }) => {
  const bodies = await stubQueryBuilder(page)
  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)

  const rule = await addRule(page, 'gnomad_af_global', 'between')
  const bounds = rule.getByTestId('value-editor').locator('input')
  await expect(bounds).toHaveCount(2)
  await bounds.nth(0).fill('0.1')
  await bounds.nth(1).fill('0.5')


  await page.getByTestId('run-query-btn').click()

  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].sample_id).toBe(1)
  expect(bodies[0].filter.rules).toEqual([
    expect.objectContaining({ field: 'gnomad_af_global', operator: 'between', value: ['0.1', '0.5'] }),
  ])
})

test('in and not in rules typed as comma lists are posted as arrays (#2059)', async ({ page }) => {
  const bodies = await stubQueryBuilder(page)
  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)

  const inRule = await addRule(page, 'gene_symbol', 'in')
  await inRule.getByTestId('value-editor').fill('BRCA1, BRCA2')
  const notInRule = await addRule(page, 'chrom', 'notIn')
  await notInRule.getByTestId('value-editor').fill('X,Y')
  await page.getByTestId('run-query-btn').click()

  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].filter.rules).toEqual([
    expect.objectContaining({ field: 'gene_symbol', operator: 'in', value: ['BRCA1', 'BRCA2'] }),
    expect.objectContaining({ field: 'chrom', operator: 'notIn', value: ['X', 'Y'] }),
  ])
  await expect(page.getByText(/Query failed/i)).toHaveCount(0)
})
