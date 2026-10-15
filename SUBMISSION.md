# Graph Hacks submission notes

Track: 01 - Agents That Act on Connected Data.

## What it does

PatchPath builds a commit-pinned evidence pack before a contributor starts
an open-source issue: lexical candidate symbols, static callers and test
candidates, contribution-document paths, assignees and open PR body-search
results. It never claims an issue, comments, opens PRs or runs target code.

FalkorDB stores files/symbols and CALLS/IMPORTS relationships. Fixed,
parameterized graph tools query candidates and bounded investigation
paths. Candidate ranking is lexical, not an LLM reasoning benchmark.
SQLite holds snapshot readiness metadata, not the relationship graph.

## Evidence

At packaging ef837aad3127167671eb37b9f6bd8a94ec7ce26e: 2,293 non-module
symbols and 11,506 stored CALLS edges (6,102 EXACT / 657 NAME_ONLY /
4,747 UNRESOLVED, rounded 53/6/41%). Totals include 1,856 derived
constructor edges and are not unique source call counts or accuracy.

Frozen raw auto-discovered sample, 2026-10-15: 7/12 issue-level
any-changed-product-file hits among the first five candidate symbols;
5/12 also have a heuristic symbol hit; zero runtime errors. Three cases
have no product-Python file in the harness oracle and count as misses.
The symbol diagnostic uses bare names and one-hop neighbors, not recall.
Issue text was observed after the fixes. Full cases, text and outcomes are
committed under benchmarks/; no historical curated score is claimed.

## Known limits

AST analysis is incomplete. Some dynamic/chained calls are omitted;
UNRESOLVED only counts captured edges. Static test candidates are not
executed. Hash checks verify local source against the ingested file hash.
GitHub issue/PR API failures are explicitly unverified. PR search only
checks open PR bodies for the number and can miss competing work.
No issue-availability or patch-correctness verdict is made.

## Deployment and demo

Local-first, no hosted service or paid model dependency. README has the
Docker/pip setup and optional read-only MCP stdio server. The short demo
uses designed visuals; decorative graph animation is not the actual graph
layout. Counts and example issue/PR are from real runs. Upload the corrected
video and update its final link before submitting.

## AI-use disclosure

Designed and built with AI coding assistance (Instinct), including design,
implementation, debugging, verification and demo preparation. The
participant chose the concept and set scope/constraints. Before submitting,
the participant should confirm that the description of their own work and
understanding is accurate, and complete any required walkthrough. This file
does not claim they have completed a code review or can already explain
all implementation choices.
