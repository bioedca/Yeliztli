import { spawnSync } from "node:child_process"
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

const frontendDir = process.cwd()
const repoRoot = path.resolve(frontendDir, "..")
const strictScript = path.join(frontendDir, "scripts", "run-vitest-strict.sh")

function yamlBlock(contents: string, header: string) {
  const lines = contents.split(/\r?\n/)
  const start = lines.findIndex((line) => line.trim() === header)
  expect(start, `Missing YAML header: ${header}`).toBeGreaterThanOrEqual(0)

  const indent = lines[start].search(/\S/)
  const block = [lines[start]]
  for (const line of lines.slice(start + 1)) {
    if (line.trim() === "") {
      block.push(line)
      continue
    }
    const lineIndent = line.search(/\S/)
    if (lineIndent <= indent) break
    block.push(line)
  }
  return block.join("\n")
}

function runWithFakeVitest(
  fakeBody: string,
  scriptArgs: string[] = [],
  extraEnv: Record<string, string> = {},
) {
  const tempDir = mkdtempSync(path.join(tmpdir(), "yeliztli-vitest-contract-"))
  const binDir = path.join(tempDir, "bin")
  const vitestPath = path.join(binDir, "vitest")
  mkdirSync(binDir)
  writeFileSync(vitestPath, `#!/usr/bin/env bash\n${fakeBody}`, { encoding: "utf8", mode: 0o755 })
  chmodSync(vitestPath, 0o755)

  return {
    result: spawnSync("bash", [strictScript, ...scriptArgs], {
      cwd: frontendDir,
      env: {
        ...process.env,
        ...extraEnv,
        PATH: `${binDir}${path.delimiter}${process.env.PATH ?? ""}`,
        VITEST_STRICT_LOG: path.join(tempDir, "vitest.log"),
      },
      encoding: "utf8",
    }),
  }
}

describe("frontend strict test command contract", () => {
  it("keeps package scripts wired to the strict Vitest wrapper", () => {
    const packageJson = JSON.parse(
      readFileSync(path.join(frontendDir, "package.json"), "utf8"),
    ) as { scripts: Record<string, string> }

    expect(packageJson.scripts.test).toBe("bash scripts/run-vitest-strict.sh")
    expect(packageJson.scripts["test:coverage"]).toBe(
      "bash scripts/run-vitest-strict.sh --coverage",
    )
    expect(packageJson.scripts["test:ci"]).toBe("npm test")
    expect(packageJson.scripts["test:watch"]).toBe("vitest")
  })

  it("keeps CI and release frontend jobs on the explicit strict command", () => {
    for (const workflow of [".github/workflows/ci.yml", ".github/workflows/release.yml"]) {
      const contents = readFileSync(path.join(repoRoot, workflow), "utf8")
      expect(yamlBlock(contents, "test-frontend:")).toContain(
        "run: cd frontend && npm run test:ci",
      )
    }
  })

  it("runs frontend tests when frontend contract files change", () => {
    const ciWorkflow = readFileSync(path.join(repoRoot, ".github/workflows/ci.yml"), "utf8")
    const filters = yamlBlock(ciWorkflow, "filters: |")
    const frontendFilter = yamlBlock(filters, "frontend:")
    const frontendJob = yamlBlock(ciWorkflow, "test-frontend:")

    expect(frontendFilter).toContain("- 'tests/frontend/**'")
    expect(frontendFilter).toContain("- '.github/workflows/ci.yml'")
    expect(frontendFilter).toContain("- '.github/workflows/release.yml'")
    expect(frontendJob).toContain(
      "needs.changes.outputs.frontend == 'true' || needs.changes.outputs.workflows == 'true'",
    )
  })
})

describe("strict Vitest wrapper", () => {
  it("fails on React act warnings emitted to stderr", () => {
    const { result } = runWithFakeVitest("printf '%s\\n' 'Warning: not wrapped in act(...)' >&2\n")

    expect(result.status).toBe(1)
    expect(result.stderr).toContain("React act warning detected")
  })

  it("preserves Vitest failures when no act warning is present", () => {
    const { result } = runWithFakeVitest("printf '%s\\n' 'ordinary failure'\nexit 7\n")

    expect(result.status).toBe(7)
    expect(result.stdout).toContain("ordinary failure")
  })

  it("passes clean Vitest output", () => {
    const { result } = runWithFakeVitest("printf '%s\\n' 'all good'\n")

    expect(result.status).toBe(0)
  })

  it("forwards extra arguments to vitest run", () => {
    const argsOut = path.join(mkdtempSync(path.join(tmpdir(), "yeliztli-vitest-args-")), "args.txt")
    const { result } = runWithFakeVitest(
      "printf '%s\\n' \"$@\" > \"$VITEST_ARGS_OUT\"\nprintf '%s\\n' 'all good'\n",
      ["--coverage"],
      { VITEST_ARGS_OUT: argsOut },
    )

    expect(result.status).toBe(0)
    expect(readFileSync(argsOut, "utf8").split(/\r?\n/).filter(Boolean)).toEqual([
      "run",
      "--coverage",
    ])
  })
})
