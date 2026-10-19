/** The command-palette trigger's accessible name contains its visible label (#2306).
 *
 * WCAG 2.5.3 Label in Name: a speech-input user activates a control by saying the
 * text they can see. The trigger showed "Search..." but was named "Open command
 * palette", so saying "Search" reached nothing. axe flags this only through its
 * experimental `label-content-name-mismatch` rule, which the E2E audit now enables;
 * that audit is Tier-2, so this pins the same contract on every PR.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from './test-utils'
import TopNav from '@/components/layout/TopNav'

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ theme: 'system' }) })),
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('command-palette trigger (Label in Name, #2306)', () => {
  it('starts its accessible name with the text it shows', () => {
    render(<TopNav />)
    const trigger = screen.getByTestId('command-palette-trigger')

    const visible = Array.from(trigger.querySelectorAll('span'))
      .map((node) => node.textContent ?? '')
      .join(' ')
      .trim()
    expect(visible).toBe('Search...')
    expect(trigger).toHaveAccessibleName('Search..., open command palette')
    expect(trigger.getAttribute('aria-label')?.startsWith(visible)).toBe(true)
  })

  it('announces the shortcut as a shortcut, not as part of the name', () => {
    render(<TopNav />)
    const trigger = screen.getByTestId('command-palette-trigger')

    expect(trigger).toHaveAttribute('aria-keyshortcuts', 'Control+K Meta+K')
    expect(trigger.querySelector('kbd')).toHaveAttribute('aria-hidden', 'true')
    expect(trigger.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})
