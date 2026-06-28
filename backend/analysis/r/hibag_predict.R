#!/usr/bin/env Rscript
#
# HIBAG HLA imputation (Wave D / SW-D1, roadmap #17).
#
# GPL isolation: this script `library(HIBAG)` (HIBAG is GPL-3) but it is a
# STANDALONE script invoked by path via `subprocess` from the MIT Python app and
# never imported, so nothing GPL is linked into the app — identical to how the
# Beagle/GLIMPSE2 binaries are isolated. See docs/external-inputs-strategy.md
# (§HIBAG) and backend/analysis/hibag_runner.py.
#
# Reads a sample's PLINK genotypes (.bed/.bim/.fam), predicts HLA alleles at the
# requested loci against a BYO pre-fit model, and writes a tab-separated table:
#
#   locus  sample.id  allele1  allele2  prob  matching
#
# The pre-fit model file is ancestry-specific and user-supplied (never bundled);
# it is either a named list keyed by locus (the common form, e.g.
# European-HLA4.RData) or a single per-locus object. `prob` is HIBAG's posterior
# call probability (the confidence the caller thresholds; `matching` is a QC
# signal, NOT a confidence — see Zheng et al. 2014, Pharmacogenomics J,
# PMID:23712092). Usage:
#
#   Rscript hibag_predict.R --plink PREFIX --model MODEL.RData --loci A,B,C --out OUT.tsv
#
# Exits non-zero with a message on stderr on any failure (missing inputs, an
# unsupported locus, or a prediction error) so the Python seam treats it as a
# failed run.

suppressWarnings(suppressMessages(library(HIBAG)))

# --- minimal `--key value` argument parser (no optparse dependency) ------------
parse_args <- function(argv) {
  out <- list()
  i <- 1
  while (i <= length(argv)) {
    key <- argv[i]
    if (startsWith(key, "--") && i < length(argv)) {
      out[[substring(key, 3)]] <- argv[i + 1]
      i <- i + 2
    } else {
      stop(sprintf("unexpected argument: %s", key))
    }
  }
  out
}

die <- function(msg) {
  message(msg)
  quit(status = 1, save = "no")
}

args <- parse_args(commandArgs(trailingOnly = TRUE))
for (req in c("plink", "model", "loci", "out")) {
  if (is.null(args[[req]])) die(sprintf("missing required --%s", req))
}

prefix <- args[["plink"]]
bed <- paste0(prefix, ".bed")
bim <- paste0(prefix, ".bim")
fam <- paste0(prefix, ".fam")
for (f in c(bed, bim, fam, args[["model"]])) {
  if (!file.exists(f)) die(sprintf("input not found: %s", f))
}
loci <- strsplit(args[["loci"]], ",", fixed = TRUE)[[1]]
loci <- trimws(loci)
loci <- loci[nzchar(loci)]
if (length(loci) == 0) die("no loci requested")

geno <- tryCatch(
  hlaBED2Geno(bed.fn = bed, fam.fn = fam, bim.fn = bim, assembly = "hg19"),
  error = function(e) die(sprintf("hlaBED2Geno failed: %s", conditionMessage(e)))
)

mobj <- tryCatch(
  get(load(args[["model"]])),
  error = function(e) die(sprintf("could not load model: %s", conditionMessage(e)))
)
# The model file is either a named list keyed by locus, or a single per-locus
# object — select per locus accordingly.
select_model <- function(locus) {
  if (is.list(mobj) && !is.null(names(mobj)) && locus %in% names(mobj)) {
    hlaModelFromObj(mobj[[locus]])
  } else {
    hlaModelFromObj(mobj)
  }
}

frames <- list()
for (locus in loci) {
  model <- tryCatch(
    select_model(locus),
    error = function(e) die(sprintf("no model for locus %s: %s", locus, conditionMessage(e)))
  )
  pred <- tryCatch(
    hlaPredict(model, geno, type = "response"),
    error = function(e) die(sprintf("prediction failed for %s: %s", locus, conditionMessage(e)))
  )
  df <- pred$value  # sample.id, allele1, allele2, prob, matching
  if (is.null(df) || nrow(df) == 0) next
  df <- cbind(locus = locus, df)
  frames[[locus]] <- df
}

if (length(frames) == 0) die("no HLA calls produced")
out <- do.call(rbind, frames)
# Keep a stable column contract for the Python parser.
cols <- c("locus", "sample.id", "allele1", "allele2", "prob", "matching")
cols <- cols[cols %in% colnames(out)]
out <- out[, cols, drop = FALSE]
write.table(out, file = args[["out"]], sep = "\t", row.names = FALSE, quote = FALSE, na = "NA")
