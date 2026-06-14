/**
 * Issue #607 — the SQL Console schema sidebar nested a `<button>` (the table-name
 * "insert into editor" control) INSIDE another `<button>` (the expand/collapse
 * toggle). Nesting interactive content is invalid HTML and a *serious* axe
 * `nested-interactive` violation: keyboard / screen-reader users get undefined
 * focus & activation behaviour for the inner control.
 *
 * The fix makes the row a non-interactive `<div>` holding two sibling buttons
 * (a chevron toggle with `aria-expanded`, and the table-name insert button).
 *
 * The repo-wide `wcag-audit.spec.ts` visits `/query-builder` with NO sample, so
 * the schema sidebar renders empty ("No tables found") and the table-row buttons
 * never mount — that is exactly why this defect slipped through. This spec
 * route-stubs the schema so the table rows actually render, then axes them.
 */

import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { bypassSetup, waitForReactHydration } from './helpers'

test.beforeEach(async ({ page }) => {
  await bypassSetup(page)
})

const jsonRoute = (payload: unknown, status = 200) => ({
  status,
  contentType: 'application/json',
  body: JSON.stringify(payload),
})

// `useSchemaInfo` issues two kinds of POST /api/query/sql: a `sqlite_master`
// table-name listing, then a `PRAGMA table_info(...)` per table. Branch on the
// request body's SQL and return the matching SqlResult shape.
const SCHEMA_TABLES = {
  columns: [{ name: 'name', type: null }],
  rows: [['annotated_variants'], ['raw_variants']],
  row_count: 2,
  truncated: false,
  execution_time_ms: 0.5,
}
const TABLE_INFO = {
  columns: [
    { name: 'cid', type: null },
    { name: 'name', type: null },
    { name: 'type', type: null },
    { name: 'notnull', type: null },
    { name: 'dflt_value', type: null },
    { name: 'pk', type: null },
  ],
  rows: [
    [0, 'rsid', 'TEXT', 0, null, 0],
    [1, 'chrom', 'TEXT', 0, null, 0],
    [2, 'pos', 'INTEGER', 0, null, 0],
  ],
  row_count: 3,
  truncated: false,
  execution_time_ms: 0.2,
}

async function stubSchema(page: import('@playwright/test').Page) {
  await page.route('**/api/query/sql', async (route) => {
    const body = route.request().postDataJSON() as { sql?: string } | null
    const sql = body?.sql ?? ''
    if (sql.includes('sqlite_master')) {
      return route.fulfill(jsonRoute(SCHEMA_TABLES))
    }
    if (sql.includes('PRAGMA table_info')) {
      return route.fulfill(jsonRoute(TABLE_INFO))
    }
    // Any other (real) query execution — irrelevant to this a11y check.
    return route.fulfill(jsonRoute({ ...SCHEMA_TABLES, rows: [] }))
  })
}

async function openSqlConsoleWithSchema(page: import('@playwright/test').Page) {
  await stubSchema(page)
  await page.goto('/query-builder?sample_id=1')
  await waitForReactHydration(page)
  await page.getByTestId('tab-sql').click()
  // Wait for the stubbed schema rows to mount (this is what the audit misses).
  await expect(page.getByText('annotated_variants')).toBeVisible()
  await expect(page.getByText('raw_variants')).toBeVisible()
}

test.describe('SQL Console schema sidebar a11y (#607)', () => {
  test('schema table rows contain no nested interactive controls', async ({ page }) => {
    await openSqlConsoleWithSchema(page)

    // Scope axe to the schema panel (excludes the Monaco editor's own DOM).
    const results = await new AxeBuilder({ page })
      .include('[data-testid="schema-panel"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()

    const violations = results.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      description: v.description,
      nodes: v.nodes.length,
    }))

    // No nested-interactive specifically, and no other violations either.
    expect(
      violations.find((v) => v.id === 'nested-interactive'),
      `nested-interactive violation present:\n${JSON.stringify(violations, null, 2)}`,
    ).toBeUndefined()
    expect(
      violations,
      `axe-core violations on SQL Console schema panel:\n${JSON.stringify(violations, null, 2)}`,
    ).toEqual([])
  })

  test('the table-name insert button is a real sibling button, not nested in the toggle', async ({ page }) => {
    await openSqlConsoleWithSchema(page)

    // The expand/collapse toggle exposes its state via aria-expanded...
    const toggle = page.getByTestId('schema-table-toggle').first()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')

    // ...and the table-name "insert" control is a sibling <button>, never a
    // descendant of the toggle (the nested-interactive defect).
    const insertBtn = page.getByRole('button', { name: 'annotated_variants', exact: true })
    await expect(insertBtn).toBeVisible()
    const nestedInsideToggle = toggle.locator('button')
    await expect(nestedInsideToggle).toHaveCount(0)

    // The toggle still works: clicking it expands the table's columns and
    // flips aria-expanded.
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByTestId('schema-column').first()).toBeVisible()
  })
})
