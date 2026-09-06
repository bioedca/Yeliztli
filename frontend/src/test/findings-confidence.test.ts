/**
 * @vitest-environment happy-dom
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"
import {
  HIGH_CONFIDENCE_MIN_STARS,
  parseMinStars,
} from "@/lib/findings-confidence"

describe("high-confidence threshold", () => {
  it("matches the backend's dashboard-preview threshold", () => {
    // Cross-stack parity: the dashboard labels a set "High-Confidence" using
    // this constant while the backend decides membership. If they drift, the
    // label silently describes a different set than the API returns -- and
    // nothing else in the suite would notice.
    const source = readFileSync(
      resolve(__dirname, "../../../backend/api/routes/findings.py"),
      "utf-8",
    )
    const match = source.match(
      /high_conf\s*=\s*\[[^\]]*?\(r\.evidence_level or 0\)\s*>=\s*(\d+)/s,
    )
    expect(
      match,
      "could not locate the backend high_conf threshold -- if that expression moved, update this parity test rather than deleting it",
    ).not.toBeNull()
    expect(Number(match![1])).toBe(HIGH_CONFIDENCE_MIN_STARS)
  })
})

describe("parseMinStars", () => {
  it.each([
    ["3", 3],
    ["1", 1],
    ["4", 4],
  ])("accepts in-range %s", (raw, expected) => {
    expect(parseMinStars(raw)).toBe(expected)
  })

  it.each([null, "", "0", "5", "-1", "abc", "3.5", "3x"])(
    "rejects %s",
    (raw) => {
      expect(parseMinStars(raw as string | null)).toBeNull()
    },
  )
})
