# PatchPath

**Before you claim an open-source issue, see the evidence, the unknowns, and the next investigation.**

PatchPath is a contribution-readiness agent. Give it a public GitHub repo and
an issue number; it assembles a source-linked evidence pack: the code the
issue likely touches, who calls it, which tests exercise it, the repo's
contribution rules, and competing work (assignees, open PRs) - pinned to an
exact commit. It does not promise the issue is available, that a patch is
correct, or that anything will merge. It never claims, comments, or opens
PRs, and it never runs repository code.

Built for **Graph Hacks (WeMakeDevs x FalkorDB), Track 01: Agents That Act
on Connected Data.**

## Why a graph (and why it is load-bearing)

Readiness is a relationship question: issue -> candidate symbols -> callers
-> tests -> rules -> competing work. FalkorDB stores that structure and the
agent's tools are bounded Cypher traversals over it. Take FalkorDB out and
the multi-hop investigation paths this tool produces (e.g. "tests that
exercise this function's callers") cannot be answered - there is no keyword
fallback presented as graph output.

## Graph data model

Nodes:
- `Snapshot` - repo + exact commit SHA + analyzer/schema versions
- `File` - normalized path, content hash
- `Symbol` - function/class/method (and synthetic `module`), qualified name,
  line range, `is_test` flag
- `Unresolved` - a call target static analysis could not resolve

Edges:
- `(File)-[:IN_SNAPSHOT]->(Snapshot)`
- `(Symbol)-[:DEFINED_IN]->(File)`
- `(Symbol)-[:CALLS {tag, expr, line}]->(Symbol|Unresolved)` - tag is
  `EXACT` (resolved to a definition in this snapshot), `NAME_ONLY` (unique
  name match, suggestion only), or `UNRESOLVED`
- `(File)-[:IMPORTS]->(File)` - internal imports only

Key Cypher the product depends on (via fixed, parameterized tools - no
arbitrary Cypher from UI or models):

```cypher
// tests that statically call a symbol
MATCH (t:Symbol {is_test: true})-[c:CALLS]->(s:Symbol {qualname: $q})
RETURN t.qualname, c.tag LIMIT 50

// bounded caller expansion (applied iteratively, max 3 hops)
MATCH (o:Symbol)-[c:CALLS]->(s:Symbol {qualname: $q})
WHERE o.kind <> 'module'
RETURN DISTINCT o.qualname, c.tag LIMIT 50
```

## Install and run

Requirements: Python 3.10+, Docker (for FalkorDB), ~2GB RAM.

```bash
docker run -d --name falkordb -p 6379:6379 falkordb/falkordb:6.0.2
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m patchpath.cli ingest pypa/packaging
python -m patchpath.cli pack pypa/packaging 1449
```

The pack is written to `data/packets/` as Markdown + JSON.

## What it will not do

- No automatic claims, comments, PRs, or generated patches
- No repository code execution (analysis is AST-only, network-isolated)
- No "issue is free" verdicts: assignees and linked PRs are evidence; the
  decision is yours. If issue state cannot be fetched, the pack says
  **state unverified**.
- Static paths are not proof of bug causality or a complete blast radius;
  unresolved calls stay visible as gaps.

## Honesty model

Every call edge is tagged EXACT / NAME_ONLY / UNRESOLVED. Dynamic dispatch,
decorators and monkey-patching defeat static analysis; the pack reports the
unresolved percentage instead of inventing nearest-name facts. Code
citations are re-validated against the stored snapshot at the pinned SHA
(hash-checked); a mismatch fails the pack.

## AI use disclosure

Built with AI coding assistance (design, implementation and debugging).
Architecture, scope decisions, and verification are the participant's.
See SUBMISSION.md for the full disclosure.

## License

MIT (see LICENSE).
