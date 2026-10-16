"""PatchPath CLI. Day 1: ingest a public repo snapshot into FalkorDB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .acquire import AcquireError, fetch_snapshot, resolve_sha
from .analyze import analyze_snapshot
from .dbconn import falkor_address
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
    host, port = falkor_address()
    try:
        gs = GraphStore()
        gs.db.connection.ping()
    except Exception:
        print(f"error: FalkorDB is not reachable on {host}:{port} - start it "
              "(docker run -d --name falkordb -p 127.0.0.1:6380:6379 "
              "falkordb/falkordb:6.0.2) and retry. "
              "No new investigation is possible while the graph is down; "
              "previously built packs in data/packets/ are historical.",
              file=sys.stderr)
        return 2
    reg.begin(snap.snapshot_id, repo, sha, ANALYZER_VERSION, SCHEMA_VERSION,
              "(pending)")
    try:
        staging = gs.build_staging(snap, analyses)
        gs.create_indexes(staging)
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
    host, port = falkor_address()
    try:
        from falkordb import FalkorDB as _F
        _F(host=host, port=port).connection.ping()
    except Exception:
        print(f"error: FalkorDB is not reachable on {host}:{port}; cannot "
              "build a pack while the graph is down. Existing packs in "
              "data/packets/ are historical.",
              file=sys.stderr)
        return 2

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




def select_prune_targets(graph_names, registered):
    """Choose graphs safe to drop. `registered` maps snapshot_id ->
    (status, ready_graph_name) from the registry, the source of truth.

    Drops: staging graphs (crash leftovers; do not run while ingesting)
    and published graphs the registry has superseded (re-ingested with a
    different graph name) or marked FAILED. Orphans (no registry row)
    are reported, never dropped: the registry is the only authority on
    which graph a reader resolves.
    """
    staging, superseded, orphans = [], [], []
    for name in graph_names:
        if name.startswith("pp_stg_"):
            staging.append(name)
        elif name.startswith("pp_snap_"):
            snap_id = name[len("pp_snap_"):].split("_a")[0]
            row = registered.get(snap_id)
            if row is None:
                orphans.append(name)
            else:
                status, ready_graph = row
                if status == "FAILED" or (status == "READY" and ready_graph != name):
                    superseded.append(name)
    return staging, superseded, orphans


def cmd_prune(args) -> int:
    from falkordb import FalkorDB
    reg = Registry(DATA / "registry.db")
    rows = reg.conn.execute(
        "SELECT snapshot_id, status, graph_name FROM snapshots").fetchall()
    registered = {r[0]: (r[1], r[2]) for r in rows}
    host, port = falkor_address()
    try:
        names = FalkorDB(host=host, port=port).list_graphs()
    except Exception:
        print(f"error: FalkorDB is not reachable on {host}:{port}", file=sys.stderr)
        return 2
    staging, superseded, orphans = select_prune_targets(names, registered)
    if args.dry_run:
        for label, group in (("staging", staging), ("superseded", superseded),
                             ("orphan (kept)", orphans)):
            for g in group:
                print(f"[prune:dry-run] {label}: {g}")
        print(f"[prune:dry-run] would drop {len(staging) + len(superseded)} "
              f"of {len(names)} graphs")
        return 0
    db = FalkorDB(host=host, port=port)
    dropped = 0
    for g in staging + superseded:
        db.select_graph(g).delete()
        dropped += 1
    print(f"[prune] dropped {dropped} of {len(names)} graphs "
          f"({len(orphans)} orphans kept, {len(names) - dropped - len(orphans)} live)")
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
    pr = sub.add_parser("prune", help="drop staging and superseded graphs (not while ingesting)")
    pr.add_argument("--dry-run", action="store_true", help="list targets without dropping")
    pr.set_defaults(fn=cmd_prune)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
