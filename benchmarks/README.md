# Recorded benchmark, 2026-10-15

`cases_2026-10-15.json` contains the first four unique linked issues per
repo discovered by the harness's merged-PR search, at audit time.
`results_2026-10-15.json` contains all twelve outcomes, including misses.
`issue_evidence_2026-10-15/` records the exact issue-text batches used.
These are public GitHub records, not current availability checks.

Reproduce the sample with the database running:

```bash
python -m patchpath.eval --cases benchmarks/cases_2026-10-15.json \
  --issue-evidence benchmarks/issue_evidence_2026-10-15
```

Result: 7/12 issue-level any-changed-product-file hits among the first five
candidate symbols, 5/12 with both that file hit and a heuristic symbol hit.
This is not file recall, symbol recall, or general accuracy. The symbol
check compares bare names from diff definitions/hunk context with the
candidate names and their one-hop caller/callee names, so it can collide.

Three cases have no product-Python file in the extracted oracle:
packaging #1406, click #3036 and click #3128. They are retained as misses,
not silently excluded. Thus this is a raw search-derived sample, not twelve
equally scorable localization tasks. The file hit is 7/9 among cases with
an extracted product-Python oracle, but that filtered number is not the
headline result. Diff APIs may omit patches; tests and non-Python changes
are excluded by the harness. The issue text is fetched after the fix and
may have been edited since it was opened. No claim of historical text
purity or statistical representativeness is made.

Snapshot demonstration: pypa/packaging at
`ef837aad3127167671eb37b9f6bd8a94ec7ce26e` has 2,293 non-module symbols,
11,506 stored CALLS edges (6,102 EXACT, 657 NAME_ONLY, 4,747 UNRESOLVED).
The 1,856 constructor-derived EXACT edges count in those totals; these
are edges, not distinct source call sites. 9,604 AST call records were
extracted. Some chained/dynamic call forms are omitted entirely.
