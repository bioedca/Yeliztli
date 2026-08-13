import type { APOEGenotypeResponse } from "@/types/apoe"

type APOEGenotypeStatus = APOEGenotypeResponse["status"]

const APOE_GENOTYPE_STATUS_MESSAGES: Partial<Record<APOEGenotypeStatus, string>> = {
  determined_but_locked: "Loading your genotype…",
  not_run: "APOE analysis has not been run yet.",
  missing_snps: "One or both APOE SNPs (rs429358, rs7412) are missing from this sample.",
  no_call: "APOE SNPs are present but have no-call genotypes.",
  ambiguous: "APOE genotype could not be unambiguously determined.",
}

export function getAPOEGenotypeStatusMessage(status: APOEGenotypeStatus) {
  return APOE_GENOTYPE_STATUS_MESSAGES[status]
}

export function getAPOEEmptyFindingsDescription(status: APOEGenotypeStatus) {
  if (status === "not_run") {
    return "Run the APOE analysis first."
  }
  return getAPOEGenotypeStatusMessage(status)
}
