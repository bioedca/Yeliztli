# DPYD coverage in supported consumer exports

**Result for #2719.** All three candidate variants have identified probes on
Illumina's public GSA-24v3-0_A1. For the four raw-file versions Yeliztli
accepts, the manufacturer sources reviewed do not establish inclusion or
exclusion of any of the three markers in the customer download. All twelve
vendor-specific cells therefore remain **unknown** as of 2026-11-19.
The result leaves the coverage prerequisite for allele delivery in #2632 open.

| DPYD candidate and GRCh37 position | Illumina GSA-24v3-0_A1 design | 23andMe v3 raw | 23andMe v4 raw | 23andMe v5 raw | AncestryDNA v2.0 raw |
| --- | --- | --- | --- | --- | --- |
| c.868A>G · rs146356975 · chr1:98060705 | Verified probe `ilmnseq_rs146356975_F2BT-138_T_R_2609646621` | Unknown | Unknown | Unknown | Unknown |
| c.1314T>G · rs186169810 · chr1:98039341 | Verified probe `rs186169810-138_T_F_2304765371` | Unknown | Unknown | Unknown | Unknown |
| c.2279C>T · rs112766203 · chr1:97770835 | Verified probe `rs112766203-138_T_F_2279636189` | Unknown | Unknown | Unknown | Unknown |

Coding names follow NM_000110.4. The coordinates are one-based GRCh37
chromosome 1 positions. The manufacturer manifest identifies the first probe
on the minus reference strand and the other two on the plus strand. Its probe
identifiers and orientations describe assay design. No per-person call,
clinical sensitivity, analytical performance or dosing rule follows from a
manifest row. The supported upload versions, including legacy v3 build
handling, are listed in the [upload guide](../getting-started/upload-your-dna.md).

## What evidence would change a cell?

**Verified** needs a manufacturer-authenticated document for that exact
vendor/chip and export revision that connects the marker to the downloadable
result by rsID, or by a documented alias and matching build, coordinate and
orientation. **Unsupported** needs an explicit exclusion or a demonstrably
complete, version-matched export specification with the identifier and alias
space accounted for. An incomplete or unavailable list leaves the cell
**unknown**. Separately assess call quality if performance is later claimed.

| Export | Evidence reviewed | What is missing for each of its three cells |
| --- | --- | --- |
| 23andMe v3 | The manufacturer describes an OmniExpress-based design with custom content. | A v3-specific marker or alias to export-column mapping. For an older GRCh36 file, also establish the source-build mapping before comparing with the GRCh37 loci above. |
| 23andMe v4 | The manufacturer describes a custom design. | Marker identifiers for the applicable v4 design and a statement that its download includes or excludes them. A v3 list cannot establish v4 coverage. |
| 23andMe v5 | The manufacturer identifies a GSA-based design with custom content. The public Illumina manifest establishes the three backbone probes. | The vendor-specific design and export rule for each rsID or internal alias. Platform family membership cannot identify which custom probes or quality filters apply to a released download. |
| AncestryDNA v2.0 | Ancestry describes forward-strand GRCh37 raw downloads. Its published research methods describe an OmniExpress platform. | A v2.0-specific design and customer-export marker inventory binding each rsID or alias to the stated position. The research array and the generic format description supply neither complete membership nor a supported exclusion. |

23andMe also uses internal marker IDs. The FDA indication for 23andMe's
K193492 pharmacogenetic **report** enumerates two other DPYD variants. That
list bounds the regulated report only. It cannot settle any customer
raw-download cell in the matrix. A missing call in an individual file also
cannot establish platform-wide exclusion.

The dated evidence packet is in
`data/science-evidence/2026-11-19-dpyd-vendor-exports-2719/`. It contains
the selected public probe and document fields, identifiers, source hashes
and search limits. The [earlier DPYD investigation](dpyd-coverage-investigation.md)
covers the independent reference-coordinate checks and separate allele
classification question. This review changes no allele definition or
stored finding.
