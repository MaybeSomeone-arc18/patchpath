"""PatchPath CLI. Day 1: ingest a public repo snapshot into FalkorDB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .acquire import AcquireError, fetch_snapshot, resolve_sha
from .analyze import analyze_snapshot
from .graphstore import GraphStore
from .registry import Registry

DATA = Path(__file__).resolve().parent.parent / "data"


def cmd_ingest(args) -> int:
    repo = args.repo
    try:
        sha, default_branch = resolve_sha(repo, args.ref)
    except AcquireError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"[acquire] {repo}@{sha[:12]} (branch: {default_branch})")
    try:
        snap = fetch_snapshot(repo, sha)
    except AcquireError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    py_files = [f for f in snap.files if f.path.endswith(".py")]
    print(f"[acquire] {len(snap.files)} files kept "
          f"({len(py_files)} python), {len(snap.skipped)} skipped")

    # snapshot source text persists for citation validation (files own text)
    snap_dir = DATA / "snapshots" / snap.snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    for f in snap.files:
        p = snap_dir / f.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.content)

    sources = []
    for f in py_files:
        try:
            sources.append((f.path, f.content.decode("utf-8")))
        except UnicodeDecodeError:
            pass
    analyses = analyze_snapshot(sources, snap.sha)
    nsym = sum(len(a.symbols) for a in analyses)
    ncall = sum(len(a.calls) for a in analyses)
    nerr = sum(1 for a in analyses if a.parse_error)
    print(f"[analyze] {nsym} symbols, {ncall} calls, {nerr} parse errors")

    DATA.mkdir(exist_ok=True)
    reg = Registry(DATA / "registry.db")
    gs = GraphStore()
    reg.begin(snap.snapshot_id, repo, sha, ANALYZER_VERSION, SCHEMA_VERSION,
              "(pending)")
    try:
        staging = gs.build_staging(snap, analyses)
        stats = gs.validate(staging, snap, analyses)
        pub = gs.publish(staging, snap)
        reg.conn.execute("UPDATE snapshots SET graph_name=? WHERE snapshot_id=?",
                         (pub, snap.snapshot_id))
        reg.ready(snap.snapshot_id, stats)
    except Exception as e:  # validation/construction failure: keep FAILED, no half data
        reg.fail(snap.snapshot_id, f"{e.__class__.__name__}: {e}")
        print(f"error: staging failed: {e}", file=sys.stderr)
        return 1
    print(f"[graph] READY {pub}")
    print(json.dumps(stats, indent=2))
    return 0


def cmd_pack(args) -> int:
    from .acquire import Snapshot
    from .graphstore import published_graph_name
    from .issues import IssueClient
    from .packet import build_packet, render_markdown
    import hashlib

    # resolve the snapshot: reuse READY, else ingest first
    from .acquire import resolve_sha
    try:
        sha, _ = resolve_sha(args.repo, args.ref)
    except AcquireError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    snap_id = hashlib.sha256(f"{args.repo}@{sha}".encode()).hexdigest()[:16]
    reg = Registry(DATA / "registry.db")
    ready = reg.get_ready(snap_id)
    if not ready:
        print(f"[pack] no READY snapshot for {args.repo}@{sha[:12]}; ingesting first")
        rc = cmd_ingest(argparse.Namespace(repo=args.repo, ref=args.ref))
        if rc != 0:
            return rc
        ready = reg.get_ready(snap_id)
    graph = ready["graph"]
    print(f"[pack] using snapshot {graph}")

    ev = IssueClient(DATA / "issue_cache").fetch(args.repo, args.issue)
    if ev.fetch_error:
        print(f"[issue] {ev.fetch_error}")
    else:
        print(f"[issue] #{args.issue} state={ev.state} comments={ev.comments} "
              f"linked_prs={len(ev.linked_prs)}")

    from .tools_query import QueryTools
    tools = QueryTools(graph)
    snap_root = DATA / "snapshots" / snap_id
    try:
        pack = build_packet(tools, snap_root, ev, top_n=args.top)
    except Exception as e:
        print(f"error: pack failed citation validation: {e}", file=sys.stderr)
        return 1
    out_dir = DATA / "packets"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"pack_{args.repo.replace('/', '__')}__{args.issue}"
    base.with_suffix(".json").write_text(pack.to_json())
    md = render_markdown(pack)
    base.with_suffix(".md").write_text(md)
    print(f"[pack] status: {pack.status}")
    print(f"[pack] wrote {base.with_suffix('.md')}")
    print()
    print(md)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="patchpath")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="ingest a public GitHub repo snapshot")
    ing.add_argument("repo", help="owner/name")
    ing.add_argument("--ref", help="branch/tag/SHA (default: default branch)")
    ing.set_defaults(fn=cmd_ingest)
    pk = sub.add_parser("pack", help="build a readiness pack for an issue")
    pk.add_argument("repo", help="owner/name")
    pk.add_argument("issue", type=int, help="issue number")
    pk.add_argument("--ref", help="branch/tag/SHA (default: default branch)")
    pk.add_argument("--top", type=int, default=5, help="max candidate symbols")
    pk.set_defaults(fn=cmd_pack)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
