"""FalkorDB storage: one immutable staging graph per snapshot, then publish.

FalkorDB owns all relationship structure (files, symbols, tests, calls,
imports). SQLite (registry.py) owns only the pointer/metadata so a crash can
never expose a half-built graph: readers resolve the READY snapshot through
the registry, and a staging graph that fails validation is dropped.

All writes are batched, parameterized Cypher (no string-built values).
"""
from __future__ import annotations

from falkordb import FalkorDB

from . import ANALYZER_VERSION, SCHEMA_VERSION
from .analyze import EXACT, NAME_ONLY, UNRESOLVED, FileAnalysis, module_name, symbol_id
from .acquire import Snapshot

NODE_BATCH = 500
EDGE_BATCH = 2000


def staging_graph_name(snapshot_id: str) -> str:
    return f"pp_stg_{snapshot_id}_a{ANALYZER_VERSION}_s{SCHEMA_VERSION}".replace(".", "_").replace("-", "_")


def published_graph_name(snapshot_id: str) -> str:
    return f"pp_snap_{snapshot_id}_a{ANALYZER_VERSION}_s{SCHEMA_VERSION}".replace(".", "_").replace("-", "_")


class GraphStore:
    def __init__(self, host: str = "127.0.0.1", port: int = 6380):
        self.db = FalkorDB(host=host, port=port)

    # --- write path -------------------------------------------------------
    def build_staging(self, snap: Snapshot, analyses: list[FileAnalysis]) -> str:
        """Write the whole snapshot into a fresh staging graph. Returns name."""
        name = staging_graph_name(snap.snapshot_id)
        g = self.db.select_graph(name)
        # staging graphs are ours alone; drop any leftover from a crashed run
        try:
            g.delete()
        except Exception:
            pass
        g = self.db.select_graph(name)

        g.query(
            "CREATE (s:Snapshot {repo: $repo, sha: $sha, analyzer: $an, schema: $sc})",
            {"repo": snap.repo, "sha": snap.sha,
             "an": ANALYZER_VERSION, "sc": SCHEMA_VERSION})

        # File nodes (batch)
        files = [{"path": f.path, "hash": f.content_hash} for f in snap.files]
        for i in range(0, len(files), NODE_BATCH):
            g.query(
                "UNWIND $rows AS row "
                "MATCH (s:Snapshot {sha: $sha}) "
                "CREATE (f:File {path: row.path, hash: row.hash})-[:IN_SNAPSHOT]->(s)",
                {"rows": files[i:i + NODE_BATCH], "sha": snap.sha})

        # Module nodes: module-scope code has somewhere to hang CALLS edges
        mods = sorted({a.path for a in analyses})
        mod_rows = [{"id": symbol_id(snap.sha, p, module_name(p)),
                     "qual": module_name(p), "file": p} for p in mods]
        for i in range(0, len(mod_rows), NODE_BATCH):
            g.query(
                "UNWIND $rows AS row "
                "MATCH (f:File {path: row.file}) "
                "CREATE (n:Symbol {id: row.id, qualname: row.qual, name: row.qual, "
                "kind: 'module', line_start: 0, line_end: 0, is_test: false})"
                "-[:DEFINED_IN]->(f)",
                {"rows": mod_rows[i:i + NODE_BATCH]})

        # Symbol/Test nodes + DEFINES edges
        sym_rows = []
        for fa in analyses:
            for s in fa.symbols:
                sym_rows.append({
                    "id": symbol_id(snap.sha, s.file, s.qualname),
                    "qual": s.qualname, "name": s.name, "kind": s.kind,
                    "file": s.file, "ls": s.line_start, "le": s.line_end,
                    "test": s.is_test})
        for i in range(0, len(sym_rows), NODE_BATCH):
            g.query(
                "UNWIND $rows AS row "
                "MATCH (f:File {path: row.file}) "
                "CREATE (n:Symbol {id: row.id, qualname: row.qual, name: row.name, "
                "kind: row.kind, line_start: row.ls, line_end: row.le, is_test: row.test})"
                "-[:DEFINED_IN]->(f)",
                {"rows": sym_rows[i:i + NODE_BATCH]})

        # CALLS edges (tagged)
        call_rows = []
        for fa in analyses:
            for c in fa.calls:
                caller_id = symbol_id(snap.sha, fa.path, c.caller)
                row = {"caller": caller_id, "expr": c.target_expr,
                       "tag": c.tag, "line": c.line,
                       "target": symbol_id(snap.sha, _file_of(analyses, c.target_qual), c.target_qual)
                       if c.tag in (EXACT, NAME_ONLY) and c.target_qual else None}
                call_rows.append(row)
        for i in range(0, len(call_rows), EDGE_BATCH):
            batch = call_rows[i:i + EDGE_BATCH]
            # resolved targets: real edge to the target Symbol
            g.query(
                "UNWIND [r IN $rows WHERE r.target IS NOT NULL] AS row "
                "MATCH (a:Symbol {id: row.caller}), (b:Symbol {id: row.target}) "
                "CREATE (a)-[:CALLS {tag: row.tag, expr: row.expr, line: row.line}]->(b)",
                {"rows": batch})
            # unresolved: edge to an Unresolved node so gaps stay visible
            g.query(
                "UNWIND [r IN $rows WHERE r.target IS NULL] AS row "
                "MATCH (a:Symbol {id: row.caller}) "
                "MERGE (u:Unresolved {expr: row.expr}) "
                "CREATE (a)-[:CALLS {tag: row.tag, expr: row.expr, line: row.line}]->(u)",
                {"rows": batch})

        # Derived constructor edges: a call resolved to a class also calls
        # its __init__ (still exact static binding, marked derived)
        g.query(
            "MATCH (a:Symbol)-[c:CALLS]->(b:Symbol), (i:Symbol) "
            "WHERE b.kind = 'class' AND c.tag = 'EXACT' "
            "AND i.qualname = b.qualname + '.__init__' "
            "CREATE (a)-[:CALLS {tag: 'EXACT', expr: c.expr, line: c.line, "
            "derived: 'ctor'}]->(i)")

        # IMPORTS edges (module-level, internal only become File->File)
        imp_rows = []
        for fa in analyses:
            for imp in fa.imports:
                if imp.internal:
                    imp_rows.append({"src": fa.path, "dst_module": imp.imported, "line": imp.line})
        for i in range(0, len(imp_rows), EDGE_BATCH):
            g.query(
                "UNWIND $rows AS row "
                "MATCH (a:File {path: row.src}), (b:File) "
                "WHERE b.path <> a.path AND "
                "   (b.path = replace(row.dst_module, '.', '/') + '.py' "
                "   OR b.path = replace(row.dst_module, '.', '/') + '/__init__.py') "
                "CREATE (a)-[:IMPORTS {line: row.line}]->(b)",
                {"rows": imp_rows[i:i + EDGE_BATCH]})
        return name

    def create_indexes(self, staging: str) -> None:
        """Index lookup fields with the pinned release's syntax (FalkorDB 6.x)."""
        g = self.db.select_graph(staging)
        for label, prop in [("Symbol", "qualname"), ("Symbol", "id"),
                            ("Symbol", "name"), ("File", "path")]:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")

    # --- validation ----------------------------------------------------------
    def validate(self, staging: str, snap: Snapshot, analyses: list[FileAnalysis]) -> dict:
        """Count-level validation before publish. Returns measured stats."""
        g = self.db.select_graph(staging)
        def n(q, p=None):
            return g.query(q, p or {}).result_set[0][0]
        stats = {
            "files": n("MATCH (f:File) RETURN count(f)"),
            "symbols": n("MATCH (s:Symbol) WHERE s.kind <> 'module' RETURN count(s)"),
            "tests": n("MATCH (s:Symbol {is_test: true}) RETURN count(s)"),
            "calls_exact": n("MATCH ()-[c:CALLS {tag: 'EXACT'}]->() RETURN count(c)"),
            "calls_name": n("MATCH ()-[c:CALLS {tag: 'NAME_ONLY'}]->() RETURN count(c)"),
            "calls_unresolved": n("MATCH ()-[c:CALLS {tag: 'UNRESOLVED'}]->() RETURN count(c)"),
            "imports": n("MATCH ()-[i:IMPORTS]->() RETURN count(i)"),
            "parse_errors": sum(1 for a in analyses if a.parse_error),
        }
        expected_symbols = sum(len(a.symbols) for a in analyses)
        if stats["symbols"] != expected_symbols:
            raise ValueError(f"symbol count mismatch: graph {stats['symbols']} != analyzer {expected_symbols}")
        if stats["files"] != len(snap.files):
            raise ValueError(f"file count mismatch: graph {stats['files']} != snapshot {len(snap.files)}")
        return stats

    def publish(self, staging: str, snap: Snapshot) -> str:
        """Atomically-ish replace the published graph name with the staging one."""
        pub = published_graph_name(snap.snapshot_id)
        try:
            self.db.select_graph(pub).delete()
        except Exception:
            pass
        self.db.select_graph(staging).copy(pub)
        self.db.select_graph(staging).delete()
        return pub


def _file_of(analyses: list[FileAnalysis], qualname: str | None) -> str:
    if qualname is None:
        return ""
    # qualname is module.(nested.)Name; module maps back to its file
    best, best_len = "", -1
    for fa in analyses:
        m = module_name(fa.path)
        if m and (qualname == m or qualname.startswith(m + ".")) and len(m) > best_len:
            best, best_len = fa.path, len(m)
    return best
