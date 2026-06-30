/**
 * @vitest-environment happy-dom
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import { render } from "@/test/test-utils"
import StatusBar from "./StatusBar"
import type { Sample } from "@/types/samples"

// vi.hoisted so the spies exist when the (hoisted) vi.mock factories run.
const { updateCheckSpy, appUpdateSpy, intervalSpy } = vi.hoisted(() => ({
  updateCheckSpy: vi.fn(),
  appUpdateSpy: vi.fn(),
  intervalSpy: vi.fn(),
}))

// StatusBar only consumes these three from @/api/updates.
vi.mock("@/api/updates", () => ({
  useDatabaseStatuses: () => ({ data: [] }),
  useUpdateCheck: (enabled?: boolean) => {
    updateCheckSpy(enabled)
    return { data: undefined }
  },
  useAppUpdate: (enabled?: boolean) => {
    appUpdateSpy(enabled)
    return { data: undefined }
  },
}))

// Preserve the real module (ThemeProvider uses useSetThemePreference); override
// only the interval hook the gating reads.
vi.mock("@/api/preferences", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/preferences")>()),
  useUpdateCheckInterval: () => intervalSpy(),
}))

const sample = { id: 1, name: "Test Sample", created_at: null } as unknown as Sample

beforeEach(() => {
  vi.clearAllMocks()
})

describe("StatusBar update-check gating (#1287)", () => {
  it("disables the outbound update/version checks when the interval is off", () => {
    intervalSpy.mockReturnValue({ data: { update_check_interval: "off" } })
    render(<StatusBar sample={sample} variantCount={null} />)
    expect(updateCheckSpy).toHaveBeenCalledWith(false)
    expect(appUpdateSpy).toHaveBeenCalledWith(false)
  })

  it("enables them when the interval is not off", () => {
    intervalSpy.mockReturnValue({ data: { update_check_interval: "daily" } })
    render(<StatusBar sample={sample} variantCount={null} />)
    expect(updateCheckSpy).toHaveBeenCalledWith(true)
    expect(appUpdateSpy).toHaveBeenCalledWith(true)
  })

  it("defaults to enabled while the preference is still loading", () => {
    intervalSpy.mockReturnValue({ data: undefined })
    render(<StatusBar sample={sample} variantCount={null} />)
    expect(updateCheckSpy).toHaveBeenCalledWith(true)
    expect(appUpdateSpy).toHaveBeenCalledWith(true)
  })
})
