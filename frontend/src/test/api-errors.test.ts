import { describe, expect, it } from 'vitest'
import { ApiError, getApiErrorDetail, throwApiError } from '@/api/errors'

describe('throwApiError', () => {
  it('retains a response body for explicit handling without exposing it in Error.message', async () => {
    const rawDiagnostic = 'sqlite:///private/data/sample.db: permission denied'
    const response = new Response(JSON.stringify({ detail: rawDiagnostic }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })

    let thrown: unknown
    try {
      await throwApiError(response, 'Unable to load results. Please try again.')
    } catch (error) {
      thrown = error
    }

    expect(thrown).toBeInstanceOf(ApiError)
    const error = thrown as ApiError
    expect(error.status).toBe(500)
    expect(error.message).toBe('Unable to load results. Please try again.')
    expect(error.message).not.toContain(rawDiagnostic)
    expect(error.body).toEqual({ detail: rawDiagnostic })
  })
})

describe('getApiErrorDetail', () => {
  it('only exposes an explicit detail member for structured callers', () => {
    expect(getApiErrorDetail({ detail: { reason: 'stale' } })).toEqual({
      reason: 'stale',
    })
    expect(getApiErrorDetail('raw response')).toBeUndefined()
    expect(getApiErrorDetail(null)).toBeUndefined()
  })
})
