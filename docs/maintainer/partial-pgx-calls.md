# Prescribing output from partial CYP2D6 and NUDT15 calls

**Proposed decision for clinical and product review, issue #2401.** A person
reading a medication card should be able to tell which gene result the assay
supports and whether that result supports guidance for the particular drug.
We propose option C for both genes. Keep the observed result, its confidence
and assay limitations visible. Withhold a drug-specific action whenever the
observations leave materially different recommendations possible or the
application cannot establish their equivalence.

## Which drugs would this affect?

| Gene and shipped drug | Proposed handling of a partial call |
| --- | --- |
| CYP2D6 / codeine | Compare the entire applicable guidance across compatible results. The shipped normal/intermediate summaries have identical wording, while the poor and ultrarapid rows have different implications. An untyped loss-of-function marker or unresolved copy number can leave the action undecided. |
| CYP2D6 / tramadol | Make an independent decision for tramadol. Its poor and ultrarapid rows both say to avoid use, for different reasons. Treat the reason and monitoring/follow-up as part of the result. |
| CYP2D6 / ondansetron | A lower-activity substitution would miss the ultrarapid branch. CPIC Table 2 gives no recommendation for intermediate and poor metabolizers. The two shipped rows instead direct label dosing. Withhold those actions pending the independent reference repair in [#2720](https://github.com/bioedca/Yeliztli/issues/2720). |
| CYP2D6 / tamoxifen | The existing pair-level evidence hold takes precedence for every call, including a fully resolved one. Retain its audit-only reference rows. |
| NUDT15 / mercaptopurine, azathioprine and thioguanine | Evaluate each drug against the joint TPMT/NUDT15 result and applicable guideline context. If observed NUDT15 variation supports competing actions, withhold contradictory single-gene and compound recommendations for that drug. If only one gene yields a usable result, permit a source-verified single-gene action with the missing partner disclosed. Withhold the action if its source or applicable regimen cannot be established. |

The table describes Yeliztli's seven drug pairs at the audited revision. The
underlying CPIC documents cover known genotypes. They supply no validation
for substituting a phenotype for an unresolved consumer-array call. The
[evidence packet](https://github.com/bioedca/Yeliztli/tree/main/data/science-evidence/2026-11-19-pgx-untyped-2401)
identifies the guideline versions, the two independent assay observations for
each gene, and the boundaries of those findings.

## What counts as enough evidence for an action?

Retaining the direct action with a caveat (option A) can put a specific
instruction on the medication card even though the provisional gene call is
qualified elsewhere. Using the lowest plausible activity (option B) assumes
that every relevant alternative can be ordered on one scale and that the
lowest value gives an appropriate action. Copy gains, unresolved phase and
joint-gene guidance break that assumption. Neither alternative has been
validated here for an unknown phenotype.

For option C, only allow a common action when every compatible state has an
applicable, reviewed source row with the same clinical meaning, qualifiers and
strength for that drug. This includes any activity-score, population and
partner-gene keys used by the source. An absent row, an unrepresented state,
or an explicit source verdict of no recommendation is a reason to withhold.
A comparison of equal summary strings alone cannot establish agreement.
Where every state passes, show the common action with the direct call's
provisional status. Do not report a particular diplotype as confirmed.

- **CYP2D6.** Consider missing defining SNVs, whole-gene deletion, copy
  gains with unknown duplicated allele, and hybrid/conversion alleles.
  Typing all represented SNPs still leaves structural uncertainty. A two-slot
  substitution over the bundled star alleles cannot establish completeness.
- **NUDT15.** Keep missing markers distinct from observed allele states.
  Generic insertion/deletion tokens at rs746071566 do not identify the
  inserted or deleted sequence. An unphased coding SNV plus indel can leave
  both a cis allele (current `*3.002`, historical `*2`) and a trans combination
  possible. Determine which source rows apply to each combination, including
  TPMT. Do not assume a missing TPMT or NUDT15 result is normal.
- **Existing confidence gates.** Keep the insufficient-coverage exit and the
  structural and phase caveats. Simply adding these genes to either existing
  policy set would also opt observed carriers into the sparse-coverage
  exception. The existing conflict helper skips missing diplotype rows and
  handles substitutions in reference-filled slots. Neither property proves
  agreement for these genes.

With today's array input, an unresolved structural class can cause every
affected action for a gene to be withheld. The gene card should explain the
assay limitation in that case. An empty medication card must not read as a
negative result. DPYD already has a conservative path and its own coverage
follow-up in [#2632](https://github.com/bioedca/Yeliztli/issues/2632).

## Synthetic acceptance cases for an implementation

| Input | Required observation |
| --- | --- |
| CYP2D6 carrier with `*4` observed, other five defining SNVs reference and the three defining indels unassayed | Use the synthetic `*4` heterozygote in `test_cyp2d6_alert_carries_cnv_caveat`. Assert the direct `*1/*4` result and **Partial** confidence before testing suppression. Baseline emits guidance; the proposal withholds codeine and tramadol because the untyped `*3` can change their action. |
| Only the CYP2D6 `*4` marker is typed | Assert **Insufficient** confidence and zero prescribing alerts. This exercises the earlier sparse-coverage exit. |
| CYP2D6 `hom_ref` at all six defining SNVs, all three defining indels unassayed, copy number unknown | No positive variant-carrier finding. Keep the copy-number caveat and withhold a normal-only drug action. In a separate positive control, supply the supported deletion token at rs35742686 and assert the observed `*3` signal survives. |
| NUDT15 with a heterozygous rs116855232 plus a heterozygous indel token | Show the provisional observation and phase/sequence limitation. Withhold incompatible thiopurine guidance, including with TPMT intermediate, without losing the independently observed TPMT result. |
| Resolved TPMT intermediate and NUDT15 absent or insufficient, with no specific competing NUDT15 result | Use a source-verified TPMT-only row only when its regimen and indication conditions apply. Name the unavailable partner and assert zero NUDT15 or compound alerts. Include a missing condition or unreviewed row as a negative control with zero drug actions. Exercise the reciprocal known-NUDT15/absent-TPMT case and the both-unavailable case (zero actions). |
| NUDT15 `hom_ref` at assayed SNVs with an untyped indel | Keep the provisional gene result visible. Assert no positive carrier finding and zero NUDT15-directed normal-dose actions for mercaptopurine, azathioprine and thioguanine. Distinguish this result from a genuinely resolved reference control. |
| Resolved synthetic single- and joint-gene controls | Return the exact positive reviewed drug row for its full key and preserve its qualifications. For future data capable of ruling out competing classes, distinguish an established common action from conflicting, unrepresented and no-recommendation states. |
| Tamoxifen or an unrelated drug | Verify zero tamoxifen guidance through all consumers and positive unchanged guidance for an unrelated, eligible control. |

Exercise the production caller, alert generation and storage, the PGx API,
medication-safety report, generic findings and exports. Check complete bodies
and a positive control alongside every suppression. Include samples scored
before a policy revision. A fresh run and a stored result must describe the
same eligibility; use the design work in [#2705](https://github.com/bioedca/Yeliztli/issues/2705)
to settle delivery to existing installs.

**Decision requested.** Clinical and product maintainers should record whether
they accept option C for each of the seven pairs, the whole-action equivalence
rule, and the resulting loss of drug cards on unresolved array calls. Record
any exception by drug and source before implementing or changing existing
results.
