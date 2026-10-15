"""Optional MCP (Model Context Protocol) stdio server exposing PatchPath's
six read-only bounded tools to any MCP-capable agent client.

Install: pip install -r requirements-mcp.txt
Run:     python -m patchpath.mcp_server [--snapshot SNAP_ID]

The server is read-only: it can only query the local snapshot graphs. It
cannot ingest, write, or touch the network.
"""
import argparse
import json

from mcp.server.fastmcp import FastMCP

from .registry import Registry
from .tools_query import QueryTools

mcp = FastMCP("patchpath")
_tools: QueryTools | None = None


def _qt() -> QueryTools:
    assert _tools is not None
    return _tools


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True)


@mcp.tool()
def snapshot_status() -> str:
    """Repo, pinned commit SHA, symbol counts and EXACT/NAME_ONLY/UNRESOLVED
    call-edge totals for the bound snapshot."""
    return _dump(_qt().stats())


@mcp.tool()
def find_candidates(issue_text: str, title: str = "") -> str:
    """Ranked candidate symbols/files for issue text (lexical match -
    always review before trusting). Pass the issue title in `title` -
    title matches rank highest."""
    return _dump(_qt().find_candidates(issue_text, title=title))


@mcp.tool()
def inspect_symbol(qualname: str) -> str:
    """Definition card for one symbol: file, lines, signature, docstring,
    call-edge tag counts."""
    return _dump(_qt().inspect_symbol(qualname))


@mcp.tool()
def neighbors(qualname: str, direction: str = "callers", hops: int = 1,
              exact_only: bool = False) -> str:
    """Bounded traversal (max 3 hops) from a symbol. direction: callers or
    callees. exact_only traverses EXACT edges only."""
    return _dump(_qt().neighbors(qualname, direction, hops=hops,
                                 exact_only=exact_only))


@mcp.tool()
def nearby_tests(qualname: str) -> str:
    """Test symbols with static paths to the given symbol (not executed -
    candidates only)."""
    return _dump(_qt().nearby_tests(qualname))


@mcp.tool()
def constraint_files() -> str:
    """Repo files that constrain contributions (CONTRIBUTING, CI configs,
    lint/test config) detected in the snapshot."""
    return _dump(_qt().constraint_files())


def main():
    global _tools
    p = argparse.ArgumentParser(description="PatchPath MCP read-only tool server")
    p.add_argument("--snapshot", help="snapshot id (pp_snap_...)")
    p.add_argument("--repo", help="owner/name; binds the latest READY snapshot for that repo")
    p.add_argument("--registry", default=None)
    args = p.parse_args()
    from .cli import DATA
    reg = Registry(args.registry or (DATA / "registry.db"))
    if args.snapshot:
        snap = args.snapshot
    else:
        rows = reg.conn.execute(
            "SELECT graph_name, repo, updated_at FROM snapshots "
            "WHERE status='READY' ORDER BY updated_at DESC").fetchall()
        if args.repo:
            rows = [r for r in rows if r[1] == args.repo]
        if not rows:
            raise SystemExit("No READY snapshot" +
                             (f" for {args.repo}" if args.repo else "") +
                             ". Ingest first (see README).")
        repos = {r[1] for r in rows}
        if not args.repo and len(repos) > 1:
            listing = "\n".join(f"  {r[0]}  ({r[1]})" for r in rows[:10])
            raise SystemExit(
                "Multiple READY snapshots across repos; pass --repo or "
                "--snapshot to choose:\n" + listing)
        snap = rows[0][0]
        print(f"[patchpath-mcp] bound to {snap} ({rows[0][1]})",
              file=__import__("sys").stderr)
    _tools = QueryTools(snap)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
