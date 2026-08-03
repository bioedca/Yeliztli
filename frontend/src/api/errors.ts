/** Safe, structured failures for frontend API responses.
 *
 * Keep server response payloads available for explicit, local handling, but
 * never promote them to `Error.message`: generic error UI is allowed to render
 * that property and response bodies can contain implementation details.
 */

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.body = body
  }
}

const UNSUPPORTED_GENOME_BUILD_CODE = "unsupported_genome_build"
export const UNSUPPORTED_GENOME_BUILD_MESSAGE =
  "This file uses an unsupported genome build. Upload a GRCh37 (build 37) export, such as 23andMe v4/v5 or AncestryDNA."

/** Read a response body for structured local handling without assuming mocks
 * or intermediaries implement every standard Response method. */
export async function readApiResponseBody(response: Response): Promise<unknown> {
  const copy = () =>
    typeof response.clone === "function" ? response.clone() : response

  try {
    const jsonResponse = copy()
    if (typeof jsonResponse.json === "function") return await jsonResponse.json()
  } catch {
    // The fallback below handles non-JSON error bodies.
  }
  try {
    const textResponse = copy()
    if (typeof textResponse.text === "function") return await textResponse.text()
  } catch {
    // A status-only response is still a valid failure.
  }
  return null
}

/** Return a conventional API `detail` field for explicit structured handling. */
export function getApiErrorDetail(body: unknown): unknown {
  if (!body || typeof body !== "object") return undefined
  return (body as Record<string, unknown>).detail
}

/** Match the documented ingest validation code without promoting response data
 * to UI. Unrecognized response data remains opaque to generic error UI. */
export function isUnsupportedGenomeBuildDetail(value: unknown): boolean {
  return (
    !!value &&
    typeof value === "object" &&
    (value as Record<string, unknown>).code === UNSUPPORTED_GENOME_BUILD_CODE
  )
}

/** Throw a typed response failure with curated, safe display copy. */
export async function throwApiError(
  response: Response,
  message: string,
): Promise<never> {
  throw new ApiError(response.status, message, await readApiResponseBody(response))
}
