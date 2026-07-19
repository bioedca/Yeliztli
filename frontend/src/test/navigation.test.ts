import { describe, expect, it } from 'vitest'
import { withActiveSample } from '@/lib/navigation'

describe('withActiveSample', () => {
  it('adds the active sample to a bare destination', () => {
    expect(withActiveSample('/cancer', 7)).toBe('/cancer?sample_id=7')
  })

  it('merges the sample with destination-owned search and hash state', () => {
    expect(withActiveSample('/genome-browser?locus=BRCA1#tracks', 7)).toBe(
      '/genome-browser?locus=BRCA1&sample_id=7#tracks',
    )
  })

  it('replaces a stale destination sample instead of duplicating it', () => {
    expect(withActiveSample('/variants?sample_id=2', 7)).toBe(
      '/variants?sample_id=7',
    )
  })

  it('leaves the destination unchanged without a valid active sample', () => {
    expect(withActiveSample('/settings', null)).toBe('/settings')
    expect(withActiveSample('/settings', 0)).toBe('/settings')
  })
})
