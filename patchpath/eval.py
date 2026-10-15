"""Evaluation: does the pack surface the code a fix actually touched?

Method (planning doc sec.12): for real resolved issues, ingest the snapshot
at the PARENT of the merged fix commit, build the pack from the issue text
alone, and measure whether candidate files/symbols cover the files/symbols
the fix changed. The patch is never shown to candidate selection - it is
only the (imperfect) oracle. Numbers are reported as numerator/denominator
with per-case outcomes, including failures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx

from .cli import DATA, cmd_ingest
from .issues import IssueClient, IssueEvidence
from .packet import build_packet
from .tools_query import QueryTools
from .registry import Registry

API = "https://api.github.com"
UA = "patchpath-eval/0.1"
FIX_RE = re.compile(r"(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)", re.I)
DEF_RE = re.compile(r"^[+-]\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
HUNK_CTX_RE = re.compile(r"^@@.*@@\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


@dataclass
class Case:
    repo: str
    issue: int
    fix_sha: str
    parent_sha: str = ""
    fixed_files: list[str] = field(default_factory=list)
    fixed_symbols: list[str] = field(default_factory=list)
    fixed_test_files: list[str] = field(default_factory=list)


@dataclass
class Result:
    case: Case
    status: str
    cand_files: list[str] = field(default_factory=list)
    cand_symbols: list[str] = field(default_factory=list)
    file_hit: bool = False
    symbol_hit: bool = False
    test_file_hit: bool = False
    unresolved_pct: int = 0
    pack_seconds: float = 0.0
    error: str | None = None


def curate(repo: str, limit: int, http: httpx.Client) -> list[Case]:
    """Merged PRs whose body says fixes/closes #N, newest first."""
    r = http.get(f"{API}/search/issues", params={
        "q": f"repo:{repo} is:pr is:merged fixes in:body "
             "-author:app/dependabot -author:app/github-actions "
             "-author:app/pre-commit-ci",
        "sort": "updated", "order": "desc", "per_page": 30})
    r.raise_for_status()
    cases: list[Case] = []
    seen: set[int] = set()
    for item in r.json().get("items", []):
        body = item.get("body") or ""
        for m in FIX_RE.finditer(body):
            n = int(m.group(1))
            if n in seen:
                continue
            pr = http.get(f"{API}/repos/{repo}/pulls/{item['number']}")
            if pr.status_code != 200:
                continue
            merge_sha = pr.json().get("merge_commit_sha")
            if not merge_sha:
                continue
            cases.append(Case(repo=repo, issue=n, fix_sha=merge_sha))
            seen.add(n)
            break
        if len(cases) >= limit:
            break
    return cases


def fill_fix_details(case: Case, http: httpx.Client) -> None:
    r = http.get(f"{API}/repos/{case.repo}/commits/{case.fix_sha}")
    r.raise_for_status()
    d = r.json()
    parents = d.get("parents", [])
    if not parents:
        raise ValueError("fix commit has no parent")
    case.parent_sha = parents[0]["sha"]
    case.fixed_files = []
    case.fixed_symbols = []
    case.fixed_test_files = []
    for f in d.get("files", []):
        if not f["filename"].endswith(".py"):
            continue
        if "/test" in f["filename"] or f["filename"].startswith("test"):
            case.fixed_test_files.append(f["filename"])  # secondary oracle
            continue  # tests change too; primary oracle is the product code
        case.fixed_files.append(f["filename"])
        patch = f.get("patch") or ""
        case.fixed_symbols.extend(DEF_RE.findall(patch))
        case.fixed_symbols.extend(HUNK_CTX_RE.findall(patch))
    case.fixed_symbols = sorted(set(case.fixed_symbols))


def run_case(case: Case, issue_client: IssueClient) -> Result:
    res = Result(case=case, status="error")
    try:
        # snapshot at parent of fix: "analyze code before the fixing commit"
        rc = cmd_ingest(argparse.Namespace(repo=case.repo, ref=case.parent_sha))
        if rc != 0:
            res.error = "ingest failed"
            return res
        snap_id = hashlib.sha256(
            f"{case.repo}@{case.parent_sha}".encode()).hexdigest()[:16]
        ready = Registry(DATA / "registry.db").get_ready(snap_id)
        if not ready:
            res.error = "snapshot not READY"
            return res
        tools = QueryTools(ready["graph"])
        ev = issue_client.fetch(case.repo, case.issue)
        t0 = time.perf_counter()
        pack = build_packet(tools, DATA / "snapshots" / snap_id, ev, top_n=5)
        res.pack_seconds = round(time.perf_counter() - t0, 2)
        res.status = pack.status
        res.cand_symbols = [c["qualname"] for c in pack.candidates]
        files = set()
        for c in pack.candidates:
            if c.get("file"):
                files.add(c["file"])
                continue
            card = tools.inspect_symbol(c["qualname"])
            if card:
                files.add(card["file"])
        res.cand_files = sorted(files)
        res.file_hit = any(f in res.cand_files for f in case.fixed_files)
        res.test_file_hit = bool(case.fixed_test_files) and \
            any(f in res.cand_files for f in case.fixed_test_files)
        cand_names = {q.rsplit(".", 1)[-1] for q in res.cand_symbols}
        # candidates' 1-hop neighbors count as "reachable" evidence
        for q in res.cand_symbols:
            for nb in tools.neighbors(q, "callers", hops=1) + tools.neighbors(q, "callees", hops=1):
                cand_names.add(nb["qualname"].rsplit(".", 1)[-1])
        res.symbol_hit = any(s in cand_names for s in case.fixed_symbols)
        gs = pack.graph_stats
        tot = gs["calls_exact"] + gs["calls_name"] + gs["calls_unresolved"]
        res.unresolved_pct = round(100 * gs["calls_unresolved"] / tot) if tot else 0
    except Exception as e:
        res.error = f"{e.__class__.__name__}: {e}"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", default="pypa/packaging,psf/requests,pallets/click")
    ap.add_argument("--per-repo", type=int, default=4)
    ap.add_argument("--curate-only", action="store_true")
    ap.add_argument("--cases", help="JSON file of explicit cases")
    ap.add_argument("--issue-evidence", help="directory of recorded issue cache JSON; replay text, not live availability")
    args = ap.parse_args()

    http = httpx.Client(headers={"User-Agent": UA,
                                 "Accept": "application/vnd.github+json"},
                        timeout=30.0)
    if args.cases:
        cases = [Case(**c) for c in json.loads(Path(args.cases).read_text())]
    else:
        cases = []
        for repo in args.repos.split(","):
            print(f"[curate] {repo}", file=sys.stderr)
            cases.extend(curate(repo.strip(), args.per_repo, http))
    print(f"[curate] {len(cases)} cases", file=sys.stderr)
    if args.curate_only:
        print(json.dumps([asdict(c) for c in cases], indent=2))
        return 0

    # secondary test-file oracle: fetched once, cached, committed alongside
    test_oracle_path = DATA / "test_oracle_cache.json"
    test_oracle: dict = json.loads(test_oracle_path.read_text()) \
        if test_oracle_path.exists() else {}
    for case in cases:
        key = f"{case.repo}#{case.issue}"
        if not case.fixed_test_files and key in test_oracle:
            case.fixed_test_files = test_oracle[key]

    issue_client = IssueClient(DATA / "issue_cache")
    if args.issue_evidence:
        class RecordedIssueClient:
            def fetch(self, repo, number):
                fp = Path(args.issue_evidence) / (repo.replace("/", "__") + f"__{number}.json")
                return IssueEvidence(**json.loads(fp.read_text())["evidence"])
        issue_client = RecordedIssueClient()
    results: list[Result] = []
    fetched_oracle = False
    for i, case in enumerate(cases, 1):
        try:
            if not case.parent_sha:
                fill_fix_details(case, http)
            elif not case.fixed_test_files:
                key = f"{case.repo}#{case.issue}"
                if key not in test_oracle:
                    before = len(case.fixed_test_files)
                    fill_fix_details(case, http)
                    test_oracle[key] = case.fixed_test_files
                    fetched_oracle = fetched_oracle or \
                        len(case.fixed_test_files) != before or True
        except Exception as e:
            print(f"[{i}/{len(cases)}] {case.repo}#{case.issue}: fix details failed: {e}",
                  file=sys.stderr)
            results.append(Result(case=case, status="error", error=f"fix details failed: {e}"))
            continue
        print(f"[{i}/{len(cases)}] {case.repo}#{case.issue} "
              f"(parent {case.parent_sha[:8]}, files {case.fixed_files})",
                  file=sys.stderr)
        results.append(run_case(case, issue_client))

    test_oracle_path.write_text(json.dumps(test_oracle, indent=2, sort_keys=True))
    n = len(results)
    ok = [r for r in results if not r.error]
    file_hits = sum(1 for r in ok if r.file_hit)
    sym_hits = sum(1 for r in ok if r.file_hit and r.symbol_hit)
    no_product = [r for r in ok if not r.case.fixed_files]
    test_oracle_hits = sum(1 for r in no_product if r.test_file_hit)
    report = {
        "generated_at": time.time(),
        "total": n, "ran": len(ok), "errors": n - len(ok),
        "file_hit_at_5": f"{file_hits}/{n}",
        "file_and_symbol_hit": f"{sym_hits}/{n}",
        "test_oracle_hits_among_no_product_oracle": \
            f"{test_oracle_hits}/{len(no_product)}",
        "cases_without_product_python_oracle": [
            {"repo": r.case.repo, "issue": r.case.issue}
            for r in results if not r.case.fixed_files],
        "method": "Any changed product-Python file in first five candidate symbols; symbol diagnostic uses bare names plus one-hop neighbors, not recall.",
        "results": [asdict(r) for r in results],
    }
    out = DATA / "packets" / f"eval_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    for r in results:
        mark = "ERR" if r.error else ("FILE+SYM" if r.file_hit and r.symbol_hit
                                      else ("FILE" if r.file_hit else
                                            ("SYM" if r.symbol_hit else
                                             ("TEST-FILE" if r.test_file_hit else "MISS"))))
        print(f"  {mark:9} {r.case.repo}#{r.case.issue} {r.error or ''}")
    print(f"[eval] wrote {out}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
