import { Info } from "lucide-react"

interface GenotypedVariant {
  genotype: string | null
}

/** Whether a genotype is a nucleotide call whose letters can be compared with
 * a variant name. Vendor insertion/deletion tokens ("ID") cannot (#2103). */
function isNucleotideCall(snp: GenotypedVariant): boolean {
  return snp.genotype != null && /^[ACGT]+$/.test(snp.genotype)
}

/** Orientation note (#2103). Evidence and claim mapping:
 * data/science-evidence/2026-10-14-methylation-genotype-orientation-2103/
 * (accessed 2026-10-14). DNA-change names follow the gene's
 * transcriptional orientation (c.), genomic ones the reference (g.): HGVS Nomenclature v21.1.5;
 * PMID:26931183; PMID:39702242; dbSNP rs1801133
 * NC_000001.10:g.11856378G>A = NM_005957.5:c.665C>T. Arrays report
 * some variants on the other strand: PMID:28402386;
 * PMID:24517501. Base pairing: PMID:13054692. */
export default function GenotypeOrientationNote({ snps }: { snps: readonly GenotypedVariant[] }) {
  if (!snps.some(isNucleotideCall)) return null

  return (
    <p className="flex items-start gap-2 text-xs text-muted-foreground mb-3">
      <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" aria-hidden="true" />
      <span>
        Genotypes are shown as your array reported them. A variant name
        written as a coding-DNA change, such as C677T, follows the strand its
        gene is read from (a genomic description, written with g., follows the
        genome&apos;s reference strand instead), and arrays report some variants
        on the other strand. When they do, the genotype shows the
        complementary letters (A pairs with T, C with G). Names written as
        protein changes, such as Val158Met, use amino-acid codes and cannot be
        compared letter by letter with a genotype.
      </span>
    </p>
  )
}
