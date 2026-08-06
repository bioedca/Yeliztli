# Query Builder

For advanced exploration, choose the query tool that covers the data you need:

- **Visual builder** — combines filter rules with AND/OR grouping, but filters only columns
  from `annotated_variants`.
- **SQL console** — runs read-only SQL against the full per-sample database schema. Use it
  when a filter needs variant tags, cross-source concordance, or raw-genotype source data.

## Choose the right scope

| Data to filter | Database fields | Visual builder | SQL console |
| --- | --- | :---: | :---: |
| Annotated variant fields, such as gene or consequence | `annotated_variants` | Yes | Yes |
| Variant tags | `tags` and `variant_tags` | No | Yes |
| Raw-genotype source | `raw_variants.source` | No | Yes |
| Cross-source concordance | `raw_variants.concordance` | No | Yes |

For example, the following SQL returns annotated variants carrying the predefined
**Review later** tag:

```sql
SELECT av.*
FROM annotated_variants AS av
JOIN variant_tags AS vt ON vt.rsid = av.rsid
JOIN tags AS t ON t.id = vt.tag_id
WHERE t.name = 'Review later'
ORDER BY av.rsid;
```

The same query shape can join `raw_variants` on `rsid` when you need to filter its `source`
or `concordance` fields.

## Result limits

- The visual builder defaults to **50 rows per page** and accepts at most **500 rows per
  page**. Its results are paginated: request the next cursor while `has_more` is `true`.
- The SQL console defaults to **500 rows** and accepts at most **1,000 rows**. A response
  sets `truncated` to `true` when more matching rows exist.
- SQL execution has a **30-second timeout**. A query that exceeds it returns HTTP 408.

You can **save, name, and re-run** your favourite queries, and **[export](export.md)** the
results in any supported format.

!!! note "Read-only by design"
    The SQL console can only read your data — it cannot modify it. The command palette and
    query tools never perform destructive actions.
