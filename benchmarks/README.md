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
- `results_2026-10-15_run6.json` - analyzer 0.7.1, same frozen inputs:
  identical per-case outcomes again.
- `results_2026-10-15_run7.json` - graph-native traversal, same frozen
  inputs: identical per-case outcomes again.
- `results_2026-10-15_run8.json` - analyzer 0.7.1, same frozen inputs,
  plus a query-side linked-PR bridge tier (files changed in PRs the
  issue links rank with the full-dotted-name tier): **9/12** file hits,
  **6/12** file+symbol hits, 3/3 test-oracle, zero errors. Only
  psf/requests #6711 changes (miss to file hit); it is the only frozen
  case whose issue links a same-repo PR, so the tier cannot move the
  other eleven. None of the eight fresh-sample issues links a same-repo
  PR either, so the generalization numbers are unaffected.
- `results_2026-10-15_run9.json` - same code plus a within-tier
  tie-break: candidates closer (call edges, up to 2 hops) to a
  traceback-frame seed sort first. Same frozen inputs: identical
  per-case statuses (9/12 file, 6/12 file+symbol, 3/3 test-oracle,
  zero errors). One same-tier candidate swap in pallets/click #3487
  (`make_str` replaces `_posixify` in the fifth slot; neither is in
  the case oracle, so the swap is neutral). Cases without traceback
  frames are unaffected by construction.
- `results_2026-10-15_run10.json` - deterministic candidate queries
  (ORDER BY on every capped Cypher return; packet composition had
  varied run to run), a stricter symbol diagnostic (a name the fixed
  file holds as a method counts only as a qualified Class.name match;
  all six existing credits hold under the stricter rule), and a weak
  tier surfacing a variable-implicated file's most-called symbols.
  Same frozen inputs: identical per-case statuses (9/12 file, 6/12
  file+symbol, 3/3 test-oracle, zero errors), and two consecutive
  full runs now produce byte-identical packets. A stronger placement
  of the surfacing tier (level with the variable tier) was measured
  and reverted: common variable names amplified the wrong files and
  regressed the sample to 8/12 file and 5/12 file+symbol.

Reproduce the sample with the database running:

```bash
python -m patchpath.eval --cases benchmarks/cases_2026-10-15.json \
  --issue-evidence benchmarks/issue_evidence_2026-10-15
```

This is not file recall, symbol recall, or general accuracy. The symbol
check compares bare names from diff definitions/hunk context with the
candidate names and their one-hop caller/callee names, so it can collide.

## Why the misses miss

- psf/requests #6711 names no code: the issue text shares no vocabulary
  with the fixed file, so no lexical seed exists. It was an accepted
  miss through run7; run8's linked-PR bridge surfaces the fixed file
  because the issue links the fixing PR (#6644).
- packaging #1406, click #3036 and click #3128 change no product-Python
  file in the harness oracle (test-only fixes); they count as headline
  misses and are separately reported through the test-file oracle (3/3).
- packaging #1318 and requests #3698 hit the changed file but not the
  heuristic symbol check: the symbol check compares bare diff names
  against candidate and one-hop neighbor names, and `__init__` /
  `content` collide too widely to be credited.

## Oracles: product files and test files

Three cases have no product-Python file in the extracted oracle:
packaging #1406, click #3036 and click #3128. They are retained as
misses in the headline, so this is a raw search-derived sample, not
twelve equally scorable localization tasks. The primary file hit is 9/9
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
- pallets/jinja: 1/4 file hits (analyzer 0.7.1; same cases as the
  0.2.0 run, same outcome). The misses are compiler-internals issues
  whose text shares little vocabulary with the fixed code.

A later jinja rerun could not score one case because the GitHub API rate
limit blocked the fix-commit fetch; that artifact is not included.

## Traversal latency, measured

The load-bearing retrieval step is the bounded caller expansion
(`MATCH p=(o:Symbol)-[:CALLS*1..3]->(s:Symbol)`). Median wall time of
15 timed runs after one warm-up, local FalkorDB 6.0.2, measured
2026-10-15 on the published packaging graph (pypa/packaging @ 9a11fba,
10,415 CALLS edges, 2,127 Symbol nodes); client round-trip included:

| target symbol                        | depth<=1        | depth<=2        | depth<=3         |
| ------------------------------------ | --------------- | --------------- | ---------------- |
| `version.Version`                    | 0.42 ms / 663 paths  | 0.79 ms / 852 paths  | 1.02 ms / 1,250 paths |
| `version.Version.__init__`           | 0.70 ms / 661 paths  | 1.13 ms / 848 paths  | 0.94 ms / 1,246 paths |
| `specifiers.Specifier`               | 0.28 ms / 228 paths  | 0.35 ms / 229 paths  | 0.34 ms / 231 paths  |

Three-hop expansion over more than a thousand paths stays near one
millisecond, so the graph step adds no visible latency inside the
retrieval loop. These are single-machine measurements, not a throughput
or comparison claim.

## Snapshot demonstration

pypa/packaging at `ef837aad3127167671eb37b9f6bd8a94ec7ce26e` (analyzer
0.7.0): 2,293 non-module symbols, 523 module/class variables, 11,620
stored CALLS edges (6,720 EXACT / 333 NAME_ONLY / 4,026 EXTERNAL /
541 UNRESOLVED), 1,214 REFS edges. Constructor-derived EXACT
edges count in those totals; these are edges, not distinct source call
sites. 9,604 AST call records were extracted. Some chained/dynamic call
forms are omitted entirely.

The committed demonstration pack (`demo_pack_1449.json` /
`demo_pack_1449.md`) was regenerated at analyzer 0.7.1 on the same
snapshot: candidates, tests, citations and graph stats are identical to
the committed copy. Caller traversal output is ordered (depth, then
qualname, weakest final-edge tag first among equal shortest paths), so
two consecutive pack builds on one snapshot return the same callers in
the same order.

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
0.7.1 fixes re-export resolution through submodule imports
(`import click._termui_impl` still binds the top package): on
click@0b5c6b75 EXACT 4,119 -> 4,344, UNRESOLVED 550 -> 512.
