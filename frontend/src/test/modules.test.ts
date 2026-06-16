/**
 * #620/#544: the shared module registry (`@/lib/modules`) is the single source of
 * truth mapping a backend `module` string → display metadata, consumed by
 * FindingsExplorer (and any future module-rendering view). These cover its
 * internal contract; the cross-stack drift-guard that the registry has an entry
 * for every backend findings-producing module lives in
 * `tests/backend/test_findings_module_registry.py` (which reads this file).
 */

import { describe, expect, it } from "vitest"

import { MODULE_META, getModuleMeta } from "@/lib/modules"

describe("module registry (#620/#544)", () => {
  it("is non-trivially populated (guards against accidental gutting)", () => {
    expect(Object.keys(MODULE_META).length).toBeGreaterThanOrEqual(25)
  })

  it("every entry has a label, a color, and an explicit route (string or null)", () => {
    for (const [module, meta] of Object.entries(MODULE_META)) {
      expect(meta.label, module).toBeTruthy()
      expect(meta.color, module).toBeTruthy()
      expect(meta.route === null || typeof meta.route === "string", module).toBe(true)
    }
  })

  it("a known page-backed module resolves to its route; a panel-only one is non-navigable", () => {
    expect(getModuleMeta("pharmacogenomics").route).toBe("/pharmacogenomics")
    expect(getModuleMeta("carrier").route).toBe("/carrier-status") // alias label/route
    expect(getModuleMeta("lhon").route).toBeNull()
  })

  it("getModuleMeta falls back to a non-navigable, title-cased label for an unknown module", () => {
    const meta = getModuleMeta("some_unmapped_future_module")
    expect(meta.route).toBeNull()
    expect(meta.label).toBe("Some Unmapped Future Module")
  })

  it("resolves the cross-module-target keys to their canonical (acronym-correct) labels", () => {
    // The cross-module "View in X" cards route their display name through this
    // registry (#699). These are the exact keys an ad-hoc capitalize of the raw
    // key mis-rendered — gene_health→"Gene health", ebmd→"Ebmd", lhon→"Lhon".
    expect(getModuleMeta("gene_health").label).toBe("Gene Health")
    expect(getModuleMeta("ebmd").label).toBe("eBMD")
    expect(getModuleMeta("lhon").label).toBe("LHON")
    expect(getModuleMeta("apoe").label).toBe("APOE")
    expect(getModuleMeta("amd").label).toBe("AMD")
    expect(getModuleMeta("mt_rnr1").label).toBe("MT-RNR1")
  })
})
