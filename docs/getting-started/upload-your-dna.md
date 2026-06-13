# Upload your DNA

Yeliztli analyses the **raw data** file you can download from a consumer genotyping service.

## Supported files

| Service | Versions | File type |
|---------|----------|-----------|
| **23andMe** | v3, v4, v5 | `.txt` or `.zip` |
| **AncestryDNA** | v2.0 | `.txt` or `.zip` |

Yeliztli **auto-detects** the service and format from the file header — you don't need to
tell it which one you have. (Both are genotyping-array exports on the GRCh37 build; older
23andMe v3 files on GRCh36 are lifted over automatically.)

!!! info "Where to get your raw data"
    Each service has its own "download raw data" option in your account settings. Yeliztli
    needs that raw file — not the polished reports the service shows you on its website.

## How to upload

1. Click **Upload** from the dashboard or sidebar.
2. Drag and drop your file (or click to browse).
3. Yeliztli parses the file and shows progress.
4. **Annotation runs automatically** in the background, followed by the analysis modules.
5. You're taken to the dashboard when it finishes — typically a couple of minutes for a
   standard file (~600,000 markers).

If annotation is interrupted, Yeliztli simply re-runs it from scratch (it's fast enough that
no checkpointing is needed).

## More than one file?

If you've tested with more than one service, you can upload additional files and **group
files from the same person** under a single *individual* — and optionally combine them into
one merged sample with a concordance check. Each uploaded sample otherwise keeps its own
isolated database; use the **sample selector** in the top navigation to switch between them.
See **[multi-sample merging](multi-sample-merging.md)** for the full walkthrough.

Next: **[read your results](reading-your-results.md)**.
