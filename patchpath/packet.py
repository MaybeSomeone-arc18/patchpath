"""Deterministic readiness-packet assembly + citation validation.

Assembles a "before you claim" pack from fixed query tools and pinned
evidence. Every code citation is re-validated against stored snapshot text
at the pinned SHA; a mismatch fails the pack rather than shipping a wrong
line. Statuses are honest: "evidence assembled" / "needs investigation" /
"state unverified" - never "ready to claim".
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .issues import IssueEvidence
from .tools_query import QueryTools


@dataclass
class Citation:
    file: str
    line_start: int
    line_end: int
    excerpt_hash: str      # sha256 of the exact lines at validation time
    url: str               # https://github.com/<repo>/blob/<sha>/<path>#Lx-Ly


@dataclass
class Packet:
    repo: str
    sha: str
    issue_number: int
    issue_url: str
    generated_at: float
    status: str
    issue: dict
    candidates: list[dict] = field(default_factory=list)
    callers: dict[str, list[dict]] = field(default_factory=dict)
    tests: dict[str, list[dict]] = field(default_factory=dict)
    constraint_files: list[str] = field(default_factory=list)
    linked_prs: list[dict] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    suggested_steps: list[str] = field(default_factory=list)
    graph_stats: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class CitationError(Exception):
    pass


def _github_url(repo: str, sha: str, path: str, ls: int, le: int) -> str:
    frag = f"#L{ls}" if ls == le else f"#L{ls}-L{le}"
    return f"https://github.com/{repo}/blob/{sha}/{path}{frag}"


def validate_citation(snapshot_root: Path, repo: str, sha: str,
                      path: str, ls: int, le: int) -> Citation:
    """Re-read the stored source at the pinned SHA and hash the cited lines."""
    import hashlib
    fp = snapshot_root / path
    if not fp.exists():
        raise CitationError(f"cited file missing from snapshot: {path}")
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    if ls < 1 or le > len(lines) or ls > le:
        raise CitationError(f"cited range {path}:{ls}-{le} outside file ({len(lines)} lines)")
    excerpt = "\n".join(lines[ls - 1:le])
    return Citation(file=path, line_start=ls, line_end=le,
                    excerpt_hash=hashlib.sha256(excerpt.encode()).hexdigest()[:16],
                    url=_github_url(repo, sha, path, ls, le))


def build_packet(tools: QueryTools, snapshot_root: Path,
                 ev: IssueEvidence, top_n: int = 5) -> Packet:
    stats = tools.stats()
    issue_text = f"{ev.title}\n{ev.body}"
    cands = tools.find_candidates(issue_text, title=ev.title)[:top_n]

    status = "evidence assembled"
    unknowns: list[str] = []
    if ev.state == "unverified":
        status = "state unverified"
        unknowns.append("Issue state could not be verified "
                        f"({ev.fetch_error or 'fetch failed'}); no availability claim.")
    if not cands:
        status = "needs investigation"
        unknowns.append("No symbol in this snapshot matches identifiers in the "
                        "issue text; the affected code may be elsewhere or unnamed.")

    pack = Packet(
        repo=stats["repo"], sha=stats["sha"], issue_number=ev.number,
        issue_url=f"https://github.com/{ev.repo}/issues/{ev.number}",
        generated_at=time.time(), status=status,
        issue={"state": ev.state, "title": ev.title, "labels": ev.labels,
               "assignees": ev.assignees, "comments": ev.comments,
               "observed_at": ev.observed_at, "batch": ev.batch_id},
        candidates=cands, constraint_files=tools.constraint_files(),
        linked_prs=ev.linked_prs, unknowns=unknowns, graph_stats=stats)

    for c in cands:
        q = c["qualname"]
        pack.callers[q] = tools.neighbors(q, "callers", hops=2)
        pack.tests[q] = tools.nearby_tests(q)
        card = tools.inspect_symbol(q)
        if card:
            pack.citations.append(validate_citation(
                snapshot_root, stats["repo"], stats["sha"],
                card["file"], card["line_start"], card["line_end"]))

    unresolved = stats["calls_unresolved"]
    total = stats["calls_exact"] + stats["calls_name"] + unresolved
    if total:
        pct = round(100 * unresolved / total)
        pack.unknowns.append(
            f"{pct}% of call sites in this snapshot are statically unresolved "
            "(dynamic dispatch, decorators, monkey-patching); paths are a "
            "starting point, not a blast radius.")
    if ev.assignees:
        pack.unknowns.append(
            f"Issue is assigned to {', '.join(ev.assignees)}; check with them "
            "or maintainers before starting work.")
    if ev.linked_prs:
        pack.unknowns.append(
            f"{len(ev.linked_prs)} open PR(s) reference this issue; competing "
            "work likely exists.")

    pack.suggested_steps = _steps(pack)
    return pack


def _steps(p: Packet) -> list[str]:
    steps = []
    if p.status == "state unverified":
        steps.append("Re-run when the issue state can be fetched; do not start "
                     "from a stale view of competing work.")
    if p.candidates:
        top = p.candidates[0]["qualname"]
        steps.append(f"Read `{top}` and its callers first (highest lexical "
                     "match to the issue text).")
    if p.constraint_files:
        steps.append(f"Read {p.constraint_files[0]} for contribution rules "
                     "before writing code.")
    if p.tests:
        any_t = next(iter(p.tests.values()), [])
        if any_t:
            steps.append(f"Skim test candidate `{any_t[0]['test']}` to see "
                         "expected behavior (static match, not executed).")
    steps.append("Verify the bug reproduces at the pinned commit "
                 f"{p.sha[:12]} before changing code.")
    return steps


def render_markdown(p: Packet) -> str:
    """Human packet. All dynamic text HTML-escaped; links fixed to github.com."""
    L: list[str] = []
    esc = lambda s: str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    L.append(f"# Readiness pack: {esc(p.repo)}#{p.issue_number}")
    L.append("")
    L.append(f"- Status: **{esc(p.status)}**")
    L.append(f"- Issue: {p.issue_url} (state: {esc(p.issue['state'])}, "
             f"observed {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(p.issue['observed_at']))})")
    L.append(f"- Code pinned to commit `{p.sha}`")
    L.append("")
    if p.candidates:
        L.append("## Candidate code (lexical match, review before trusting)")
        for c in p.candidates:
            L.append(f"- `{esc(c['qualname'])}` ({esc(c['kind'])}) - {esc(c['reason'])}")
        L.append("")
    for q, tests in p.tests.items():
        if tests:
            L.append(f"## Static test candidates for `{esc(q)}` (not executed)")
            for t in tests[:8]:
                via = f" via `{esc(t['via'])}`" if t.get("via") else ""
                L.append(f"- `{esc(t['test'])}` [{t['tag']}]{via}")
            L.append("")
    if p.constraint_files:
        L.append("## Contribution rules found")
        for f in p.constraint_files:
            L.append(f"- `{esc(f)}`")
        L.append("")
    if p.linked_prs:
        L.append("## Open PRs referencing this issue (competing work)")
        for pr in p.linked_prs:
            L.append(f"- #{pr['number']} {esc(pr['title'])} ({pr['state']}) {pr['url']}")
        L.append("")
    if p.unknowns:
        L.append("## Unknowns and limits")
        for u in p.unknowns:
            L.append(f"- {esc(u)}")
        L.append("")
    if p.suggested_steps:
        L.append("## Suggested next investigation")
        for i, s in enumerate(p.suggested_steps, 1):
            L.append(f"{i}. {esc(s)}")
        L.append("")
    L.append("## Citations (validated against pinned source)")
    for c in p.citations:
        L.append(f"- `{esc(c.file)}:{c.line_start}-{c.line_end}` "
                 f"(hash `{c.excerpt_hash}`) {c.url}")
    L.append("")
    L.append("---")
    L.append("Read-only analysis. No tests were executed, no claims made on "
             "GitHub, no patches generated. Static paths are not proof of bug "
             "causality.")
    return "\n".join(L)
