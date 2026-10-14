# Graph Hacks submission notes

**Track:** 01 - Agents That Act on Connected Data

**AI-use disclosure (rule 11):** PatchPath was designed and built with AI
coding assistance (Instinct). The participant chose the concept, set the
scope and constraints (read-only, no auto-claims, honesty model), reviewed
the architecture, and can explain the graph model, the Cypher queries, and
the design decisions. All demo outputs shown are real runs; evaluation
numbers are measured by `patchpath/eval.py` and include the failure case.

**What the graph does (rule 3):** FalkorDB stores the snapshot's
files/symbols/tests and the CALLS/IMPORTS relationships; every pack section
(candidates, callers, test candidates, constraint files) comes from bounded
Cypher traversals. Without FalkorDB the product has no investigation paths;
there is no keyword fallback presented as graph output.

**Deployment:** local-first, no hosted service (zero-spend constraint).
Complete local setup is in README.md (Docker + pip). Demo video: 60s,
terminal capture of real runs.

**Known limits (stated, not hidden):** static analysis cannot resolve
dynamic dispatch/decorators/monkey-patching - those stay visible as
UNRESOLVED edges with the percentage reported in every pack. Issue evidence
requires the public GitHub API; when it is rate-limited the pack says
"state unverified" and makes no availability claim. Eval recall is 5/6 on
the curated set; the miss is an issue whose text names no code.
