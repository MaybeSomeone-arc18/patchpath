# Recorded benchmark, 2026-10-15

`cases_2026-10-15.json` contains the first four unique linked issues per
repo discovered by the harness's merged-PR search, at audit time.
`issue_evidence_2026-10-15/` records the exact issue-text batches used.
These are public GitHub records, not current availability checks.

Three harness runs are committed:

- `results_2026-10-15.json` - analyzer 0.1.0 run at audit time:
  7/12 file hits, 5/12 file+symbol hits.
- `results_2026-10-15_run2.json` - analyzer 0.2.0 the same day, same
  frozen inputs: **8/12** any-changed-product-file hits among the first
  five candidate symbols, **6/12** with both that file hit and a
  heuristic symbol hit, zero runtime errors.
- `results_2026-10-15_run3.json` - analyzer 0.5.0, same frozen inputs:
  identical per-case outcomes (8/12 file, 6/12 file+symbol, 3/3
  test-oracle, zero errors). The 0.3.0-0.5.0 graph-resolution work does
  not change this sample's retrieval score.
- `results_2026-10-15_run4.json` - analyzer 0.6.0, same frozen inputs:
  identical per-case outcomes again.
- `results_2026-10-15_run5.json` - analyzer 0.7.0, same frozen inputs:
  identical per-case outcomes again.

Reproduce the sample with the database running:

```bash
python -m patchpath.eval --cases benchmarks/cases_2026-10-15.json \
  --issue-evidence benchmarks/issue_evidence_2026-10-15
```

This is not file recall, symbol recall, or general accuracy. The symbol
check compares bare names from diff definitions/hunk context with the
candidate names and their one-hop caller/callee names, so it can collide.

## Oracles: product files and test files

Three cases have no product-Python file in the extracted oracle:
packaging #1406, click #3036 and click #3128. They are retained as
misses in the headline, so this is a raw search-derived sample, not
twelve equally scorable localization tasks. The primary file hit is 8/9
among cases with an extracted product-Python oracle, but that filtered
number is not the headline result.

The harness's secondary oracle (`fixed_test_files`,
`test_oracle_2026-10-15.json`) records test-Python files the fix touched.
Under it, all three no-product-oracle cases surface the actually-fixed
test file among their first five candidates (3/3). Test-oracle hits are
reported separately, never merged into the product-file headline.

Diff APIs may omit patches; tests and non-Python changes are excluded
from the primary oracle by design. The issue text is fetched after the
fix and may have been edited since it was opened. No claim of historical
text purity or statistical representativeness is made.

## Fresh-repo generalization sample

`fresh_sample_2026-10-15.json` records the same live discovery harness
run the same day on two repos not used during development:

- pallets/itsdangerous: 4/4 file hits, 3/4 with symbol hit (analyzer
  0.5.0; same cases as the 0.2.0 and 0.3.0 runs, same outcome).
- pallets/jinja: 1/4 file hits (analyzer 0.2.0). The misses are
  compiler-internals issues whose text shares little vocabulary with
  the fixed code.

A later jinja rerun could not score one case because the GitHub API rate
limit blocked the fix-commit fetch; that artifact is not included.

## Snapshot demonstration

pypa/packaging at `ef837aad3127167671eb37b9f6bd8a94ec7ce26e` (analyzer
0.7.0): 2,293 non-module symbols, 523 module/class variables, 11,620
stored CALLS edges (6,720 EXACT / 333 NAME_ONLY / 4,026 EXTERNAL /
541 UNRESOLVED), 1,214 REFS edges. Constructor-derived EXACT
edges count in those totals; these are edges, not distinct source call
sites. 9,604 AST call records were extracted. Some chained/dynamic call
forms are omitted entirely.

Analyzer history on the same snapshot: 0.1.0 measured 6,102 EXACT / 657
NAME_ONLY / 4,747 UNRESOLVED. 0.2.0 reclassified known builtins, stdlib
and third-party calls as EXTERNAL (3,474) instead of UNRESOLVED. 0.3.0
resolves package re-exports (`click.echo` through `__init__`) and local
constructor bindings (`runner = CliRunner(); runner.invoke()`), moving
more edges to EXACT (6,494) and UNRESOLVED down to 1,086 (9%). 0.4.0
binds test-function parameters named after pytest fixtures to the
fixture's returned class (`def test_x(runner)` with a fixture returning
`CliRunner()`). 0.5.0 binds annotated parameters (`ctx: Context`) to the
annotation's class and classifies pytest builtin fixtures (monkeypatch,
capfd, tmp_path, ...) as EXTERNAL: EXACT 6,727, NAME_ONLY 350,
UNRESOLVED 899 (8%). 0.6.0 classifies builtin-typed locals
(`bits = []`, `f = open(...)`) and module-level variables created by
external factories (`T = namedtuple(...)`) as EXTERNAL: UNRESOLVED
810 (7%), EXTERNAL 3,735. 0.7.0 binds externally-annotated parameters
(`draw: st.DrawFn`) and locals assigned from external factories
(`match = re.match(...)`) as EXTERNAL: UNRESOLVED 541 (5%), EXTERNAL
4,026; EXACT 6,720 after removing 7 false-positive EXACT edges where
a Callable-annotated fixture parameter shadowed its fixture function.
