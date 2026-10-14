"""Fixed, bounded, parameterized query tools over a READY snapshot graph.

No arbitrary Cypher from UI or models. Every tool is read-only, bounded in
hops and results, and separates EXACT structure from NAME_ONLY suggestion.
"""
from __future__ import annotations

import re
from falkordb import FalkorDB

MAX_HOPS = 3
MAX_CANDIDATES = 25
MAX_RESULTS = 50

IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")
PATH_RE = re.compile(r"\b([\w./-]+\.py)\b")


def identifiers(text: str) -> list[str]:
    """Candidate identifiers from issue text: dotted names, paths, words."""
    out: list[str] = []
    seen = set()
    for m in PATH_RE.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1)); out.append(m.group(1))
    for m in IDENT_RE.finditer(text):
        t = m.group(1)
        if len(t) < 3 or t in seen:
            continue
        seen.add(t); out.append(t)
    return out[:200]


class QueryTools:
    def __init__(self, graph_name: str, host: str = "127.0.0.1", port: int = 6380):
        self.g = FalkorDB(host=host, port=port).select_graph(graph_name)

    def _rows(self, q: str, p: dict | None = None):
        return self.g.query(q, p or {}).result_set

    # -- tool 1: candidate symbols for issue text ---------------------------
    def find_candidates(self, issue_text: str) -> list[dict]:
        """Deterministic lexical candidates, expanded one graph hop.

        EXACT-path matches first (dotted qualnames, file paths), then
        unique-name matches. Never claims relevance; reports why returned.
        """
        idents = identifiers(issue_text)
        if not idents:
            return []
        rows = self._rows(
            "UNWIND $ids AS id "
            "MATCH (s:Symbol) "
            "WHERE s.kind <> 'module' AND "
            "      (s.qualname = id OR s.qualname ENDS WITH '.' + id OR s.name = id) "
            "RETURN DISTINCT s.id, s.qualname, s.kind, s.name LIMIT $lim",
            {"ids": idents, "lim": MAX_CANDIDATES})
        cands = [{"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                  "reason": f"name '{r[3]}' appears in issue text"} for r in rows]
        # file-path mentions resolve to all symbols in that file (capped)
        paths = [i for i in idents if i.endswith(".py")]
        if paths:
            rows = self._rows(
                "UNWIND $ps AS p "
                "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE s.kind <> 'module' AND "
                "      (f.path = p OR f.path ENDS WITH '/' + p) "
                "RETURN DISTINCT s.id, s.qualname, s.kind, f.path LIMIT $lim",
                {"ps": paths, "lim": MAX_CANDIDATES})
            for r in rows:
                if not any(c["symbol_id"] == r[0] for c in cands):
                    cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                                  "reason": f"defined in file '{r[3]}' named in issue text"})
        return cands[:MAX_CANDIDATES]

    # -- tool 2: one symbol, full card ---------------------------------------
    def inspect_symbol(self, qualname: str) -> dict | None:
        rows = self._rows(
            "MATCH (s:Symbol {qualname: $q})-[:DEFINED_IN]->(f:File) "
            "RETURN s.id, s.kind, s.line_start, s.line_end, f.path LIMIT 1",
            {"q": qualname})
        if not rows:
            return None
        r = rows[0]
        return {"symbol_id": r[0], "qualname": qualname, "kind": r[1],
                "line_start": r[2], "line_end": r[3], "file": r[4]}

    # -- tool 3: bounded callers/callees -------------------------------------
    def neighbors(self, qualname: str, direction: str = "callers",
                  hops: int = 1, exact_only: bool = False) -> list[dict]:
        hops = max(1, min(hops, MAX_HOPS))
        if direction == "callers":
            pattern = "(o:Symbol)-[c:CALLS]->(s:Symbol {qualname: $q})"
        else:
            pattern = "(s:Symbol {qualname: $q})-[c:CALLS]->(o:Symbol)"
        where = " AND c.tag = 'EXACT'" if exact_only else ""
        # bounded: hop expansion done iteratively, not variable-length, so the
        # traversal cost is explicit
        seen, frontier = {qualname}, [qualname]
        out: list[dict] = []
        for depth in range(1, hops + 1):
            nxt = []
            for q in frontier:
                rows = self._rows(
                    f"MATCH {pattern} WHERE o.kind <> 'module'{where} "
                    "RETURN DISTINCT o.qualname, c.tag LIMIT $lim",
                    {"q": q, "lim": MAX_RESULTS})
                for r in rows:
                    if r[0] in seen:
                        continue
                    seen.add(r[0]); nxt.append(r[0])
                    out.append({"qualname": r[0], "tag": r[1], "depth": depth})
                    if len(out) >= MAX_RESULTS:
                        return out
            frontier = nxt
        return out

    # -- tool 4: tests near a symbol ------------------------------------------
    def nearby_tests(self, qualname: str) -> list[dict]:
        """Static test candidates: tests that call this symbol (directly or
        within MAX_HOPS), tagged by edge honesty. Not proof of execution."""
        rows = self._rows(
            "MATCH (t:Symbol {is_test: true})-[c:CALLS]->(s:Symbol {qualname: $q}) "
            "RETURN DISTINCT t.qualname, c.tag LIMIT $lim",
            {"q": qualname, "lim": MAX_RESULTS})
        direct = [{"test": r[0], "tag": r[1], "depth": 1} for r in rows]
        # indirect: tests calling callers
        indirect: list[dict] = []
        seen = {d["test"] for d in direct}
        for caller in self.neighbors(qualname, "callers", hops=MAX_HOPS, exact_only=True):
            for r in self._rows(
                    "MATCH (t:Symbol {is_test: true})-[c:CALLS]->(s:Symbol {qualname: $q}) "
                    "RETURN DISTINCT t.qualname, c.tag LIMIT 10",
                    {"q": caller["qualname"]}):
                if r[0] not in seen:
                    seen.add(r[0])
                    indirect.append({"test": r[0], "tag": r[1],
                                     "depth": caller["depth"] + 1,
                                     "via": caller["qualname"]})
                if len(direct) + len(indirect) >= MAX_RESULTS:
                    return direct + indirect
        return direct + indirect

    # -- tool 5: repo constraint files -----------------------------------------
    def constraint_files(self) -> list[str]:
        rows = self._rows(
            "MATCH (f:File) WHERE f.path =~ '(?i)(^|.*/)(contributing|agents|"
            "code_of_conduct|pull_request_template|issue_template|style|"
            "development|hacking)(\\..*)?$' RETURN f.path LIMIT 20")
        return [r[0] for r in rows]

    # -- tool 6: graph stats for pack header -----------------------------------
    def stats(self) -> dict:
        def n(q):
            return self.g.query(q).result_set[0][0]
        snap = self.g.query("MATCH (s:Snapshot) RETURN s.repo, s.sha LIMIT 1").result_set[0]
        return {"repo": snap[0], "sha": snap[1],
                "symbols": n("MATCH (s:Symbol) WHERE s.kind <> 'module' RETURN count(s)"),
                "calls_exact": n("MATCH ()-[c:CALLS {tag:'EXACT'}]->() RETURN count(c)"),
                "calls_name": n("MATCH ()-[c:CALLS {tag:'NAME_ONLY'}]->() RETURN count(c)"),
                "calls_unresolved": n("MATCH ()-[c:CALLS {tag:'UNRESOLVED'}]->() RETURN count(c)")}
