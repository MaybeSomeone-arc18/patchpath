# PatchPath

Before you claim an open-source issue, see the evidence, the unknowns,
and the next investigation.

Give PatchPath a public GitHub repo and an issue number. It builds an
evidence pack with lexical candidate code, static callers and test
candidates, contribution-document paths, assignees and open PRs whose
bodies mention the issue number. Code is pinned to a commit. It does not
promise that an issue is available, a patch is correct, or anything will
merge. It never claims, comments, opens PRs or runs repository code.

Built for Graph Hacks (WeMakeDevs x FalkorDB), Track 01: Agents That Act
on Connected Data.

## Why a graph

FalkorDB stores files, symbols, static CALLS relationships and internal
IMPORTS. Candidate selection is lexical matching over graph nodes -
names, dotted paths, traceback frames, parameter names and constants -
expanded by bounded graph traversals for callers and test candidates. Contribution
rules are detected by filename, not interpreted. Removing the graph
removes these query paths; there is no keyword-only fallback disguised
as graph output.

## Install and run

Requirements: Python 3.10+, Docker for FalkorDB, approximately 2GB RAM for
small repos. Larger snapshots may need more. Run from the repo root.

```bash
docker run -d --name falkordb -p 127.0.0.1:6380:6379 falkordb/falkordb:6.0.2
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest
python -m patchpath.cli ingest pypa/packaging
python -m patchpath.cli pack pypa/packaging 1449
```

The app connects to loopback port 6380. Packs are written as Markdown and
JSON under `data/packets/`. Public GitHub API limits can block acquisition
of issue evidence or evaluation cases. A failed issue or competing-PR
check is marked unverified, never interpreted as no competing work.
Cached evidence is reused for up to 15 minutes and shows its observation
time. PR discovery searches open PR bodies only, returns at most 10
results, and can miss work that does not mention the issue number there.

For the exact demonstration snapshot, pass
`--ref ef837aad3127167671eb37b9f6bd8a94ec7ce26e` to ingest and pack.
Use a full 40-character SHA for reproducible pinning.

Optional local viewer: `python -m patchpath.viewer`. It binds loopback,
prints a per-start access token, and can build packs. It is single-user,
not a hosted or multi-tenant deployment.

## Graph data model

- `Snapshot`: repo, commit SHA, analyzer/schema versions
- `File`: normalized path and SHA-256 content hash
- `Symbol`: function/class/method or synthetic module, qualified name,
  line range, test flag, parameter names and (for classes) base names
- `ModuleVar`: a module- or class-level assignment target (constants,
  flags, aliases); searchable but not counted as a symbol
- `External`: a known outside-snapshot name (builtin, stdlib or
  third-party import root)
- `Unresolved`: a captured call target that could not be resolved

Relationships: `IN_SNAPSHOT`, `DEFINED_IN`, `CALLS`, `REFS` and internal
`IMPORTS`. CALLS edges are tagged `EXACT` (an analyzer-resolved
definition, including statically resolvable inherited methods),
`NAME_ONLY` (unique-name suggestion), `EXTERNAL` (builtin/stdlib/
third-party), or `UNRESOLVED`. EXACT means a static binding decision,
not proof of runtime behavior. Class calls can also produce derived
edges to `__init__`. REFS edges record resolved non-call references -
decorators, parameter defaults and module/class assignment values such
as `digest_method = hashlib.sha1` - so dependency touch points like "who
uses `hashlib.sha1`?" are graph queries, not grep.

Fixed, parameterized tools run queries such as:

```cypher
MATCH (t:Symbol {is_test: true})-[c:CALLS]->(s:Symbol {qualname: $q})
RETURN t.qualname, c.tag LIMIT 50

MATCH (o:Symbol)-[c:CALLS]->(s:Symbol {qualname: $q})
WHERE o.kind <> 'module'
RETURN DISTINCT o.qualname, c.tag LIMIT 50
```

Caller/callee expansion is capped at three hops and 50 results. Test
candidates can include a final test edge after that caller expansion.
No arbitrary model-supplied Cypher is accepted.

## Limits and safety

- Analysis parses Python ASTs; it does not import or execute target code,
  run target tests, generate patches, or write to the target GitHub repo.
- This is not an OS/network sandbox. Acquisition and issue checks use
  GitHub; graph queries connect to local FalkorDB.
- Static paths are candidates, not bug causality or a complete blast
  radius. Dynamic dispatch, decorators, monkey-patching and inference can
  defeat resolution. Some dynamic/chained call expressions are omitted,
  not represented as UNRESOLVED. Counts describe captured stored edges.
- Archives have file/byte limits and skip links, vendored/generated Python
  and oversized files. Skips and parse errors can make coverage partial.
- Citations re-read local snapshot bytes, compare the full file's SHA-256
  with its stored graph hash, then hash the cited excerpt. Missing hashes,
  modified bytes and invalid ranges fail the pack. This checks consistency
  with acquisition, not an independently signed upstream artifact.
- Static test candidates have not been run. Issue availability remains
  the contributor's decision even when issue and PR searches succeed.

## MCP server

```bash
pip install -r requirements-mcp.txt
python -m patchpath.mcp_server --repo pypa/packaging
```

Seven read-only tools over an ingested snapshot: `snapshot_status`,
`find_candidates`, `inspect_symbol`, `neighbors`, `nearby_tests`,
`external_usage`, `constraint_files`. The stdio server cannot ingest or mutate graph data;
it connects to the local graph server. Bind a repo or explicit snapshot
when several repos have been ingested.

## Measured results

The committed [benchmark](benchmarks/README.md) includes case definitions,
recorded issue text and all per-case outcomes. Reproduce it with:

```bash
python -m patchpath.eval --cases benchmarks/cases_2026-10-15.json \
  --issue-evidence benchmarks/issue_evidence_2026-10-15
```

On the 2026-10-15 raw auto-discovered 12-issue sample across packaging,
requests and click: **8/12 any-changed-product-file hits among the first
five candidate symbols**, **6/12 with both a file hit and a heuristic
symbol hit**, zero runtime errors. The three cases with no product-Python
file in the extracted oracle are retained as misses in that headline; all
three do surface the actually-fixed test file under the secondary
test-file oracle (3/3). The only case with no useful candidate is
requests #6711, whose recorded issue text names no code. These are
issue-level hit rates, not file/symbol recall or general accuracy. Symbol
matching uses bare diff names plus one-hop caller/callee names and can
collide. Issue text was observed after the fix and may have been edited.
See the benchmark notes before comparing systems. Live discovery without
`--cases` changes as GitHub changes; it is not the frozen sample.

As a generalization check, the same discovery harness was run on two
repos it was not tuned on (2026-10-15, live GitHub): pallets/itsdangerous
scored 4/4 file hits (3/4 with symbol hit); pallets/jinja scored 1/4 -
the three misses are compiler-internals issues whose text shares little
vocabulary with the fixed code. One jinja case in a later rerun was
uncountable due to a GitHub API rate limit, not a pack failure.

At packaging commit `ef837aad3127167671eb37b9f6bd8a94ec7ce26e`:
2,293 non-module symbols, 523 module/class variables, 11,620 stored CALLS
edges (6,727 EXACT / 350 NAME_ONLY / 3,644 EXTERNAL / 899 UNRESOLVED,
rounded 58/3/31/8%) and 1,214 REFS edges. CALLS totals include derived
constructor edges, not 11,620 distinct source calls. The analyzer
extracted 9,604 AST call records. This does not measure runtime accuracy.

## AI use disclosure

Built with AI coding assistance for design, implementation, debugging and
verification. Participant contribution and understanding must be described
accurately in the final submission. See SUBMISSION.md.

## License

MIT (see LICENSE).
