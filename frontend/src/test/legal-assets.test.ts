import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import sourceLicenseText from '@/assets/legal/LICENSE.txt?raw'
import thirdPartyAttributionText from '@/assets/legal/NOTICE.txt?raw'

describe('embedded legal assets', () => {
  it('matches the authoritative repository LICENSE and NOTICE byte for byte', () => {
    const repositoryLicense = readFileSync(
      resolve(process.cwd(), '../LICENSE'),
      'utf8',
    )
    const repositoryNotice = readFileSync(
      resolve(process.cwd(), '../NOTICE'),
      'utf8',
    )

    expect(sourceLicenseText).toBe(repositoryLicense)
    expect(thirdPartyAttributionText).toBe(repositoryNotice)
  })
})
