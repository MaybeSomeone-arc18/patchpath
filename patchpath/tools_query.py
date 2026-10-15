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
# Python traceback frames: 'File "x/y.py", line 12, in func_name'
FRAME_RE = re.compile(r'File "([^"]+\.py)", line \d+, in ([A-Za-z_][A-Za-z0-9_]*)')

# Plain-English words that appear in code too; matching them as identifiers
# is how "local" becomes a candidate. Code-like tokens (snake_case with _,
# camelCase, ALL_CAPS, dotted) skip this list entirely.
STOPWORDS = {
    "the", "and", "for", "with", "when", "this", "that", "from", "into",
    "error", "fails", "fail", "failed", "bug", "issue", "should", "would",
    "could", "does", "not", "but", "are", "was", "were", "been", "have",
    "has", "had", "use", "used", "using", "version", "versions", "local",
    "parse", "test", "tests", "code", "file", "files", "value", "values",
    "name", "type", "string", "list", "dict", "data", "true", "false",
    "none", "null", "return", "returns", "raise", "raises", "instead",
    "wrong", "seems", "like", "also", "see", "example", "expected",
    "actual", "currently", "however", "because", "after", "before",
    "https", "http", "com", "github", "www", "org", "python",
    "true", "false", "none", "yes", "maybe", "really", "actually",
    "something", "anything", "everything", "nothing", "someone", "anyone",
    "trying", "tried", "going", "getting", "running", "working", "looking",
    "happens", "happened", "getting", "making", "getting", "seems",
    "problem", "problems", "question", "questions", "thanks", "please",
    "hello", "sorry", "sure", "think", "thought", "know", "wanted",
    "needed", "needs", "try", "way", "ways", "thing", "things", "stuff",
    "people", "users", "maintainers", "anymore", "instead", "rather",
    "quite", "pretty", "still", "even", "just", "only", "also", "very",
    "much", "many", "more", "most", "less", "least", "same", "different",
    "another", "other", "others", "between", "without", "within", "during",
    "while", "though", "although", "either", "neither", "both", "every",
    "each", "any", "all", "some", "such", "own", "new", "old", "first",
    "last", "latest", "recent", "recently", "following", "follows",
    "above", "below", "here", "there", "where", "when", "what", "which",
    "who", "whom", "whose", "why", "how", "then", "than", "that", "this",
    "these", "those", "them", "they", "their", "theirs", "itself",
    "install", "installed", "installing", "installation", "update",
    "updated", "updating", "upgrade", "upgraded", "environment",
    "message", "messages", "seeing", "building", "build", "built",
    "several", "various", "certain", "specific", "particular", "given",
    "based", "related", "similar", "different", "possible", "possibly",
    "probably", "likely", "unlikely", "obviously", "clearly", "simply",
    "basically", "essentially", "generally", "usually", "normally",
    "typically", "especially", "particularly", "mostly", "mainly",
    "exactly", "completely", "entirely", "totally", "fully", "highly",
    "managed", "yesterday", "today", "tomorrow", "week", "month",
}


def _code_like(token: str) -> bool:
    if "." in token or "/" in token or "_" in token:
        return True
    if token[0].isupper():                    # ClassName convention
        return True
    if any(c.isupper() for c in token[1:]):   # camelCase
        return True
    return False


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
        if t in ("True", "False", "None"):
            continue
        if not _code_like(t) and t.lower() in STOPWORDS:
            continue
        # a bare lowercase dictionary word is a weak signal; keep only if
        # it is long enough to be distinctive
        if not _code_like(t) and len(t) < 6:
            continue
        seen.add(t); out.append(t)
    return out[:200]


def frames(text: str) -> list[tuple[str, str]]:
    """(path_tail, function) pairs from traceback lines, most recent last."""
    out: list[tuple[str, str]] = []
    seen = set()
    for m in FRAME_RE.finditer(text):
        path, func = m.group(1), m.group(2)
        if func == "<module>":
            continue
        key = (path, func)
        if key not in seen:
            seen.add(key)
            out.append((path, func))
    return out[:20]


class QueryTools:
    def __init__(self, graph_name: str, host: str = "127.0.0.1", port: int = 6380):
        self.g = FalkorDB(host=host, port=port).select_graph(graph_name)

    def _rows(self, q: str, p: dict | None = None):
        return self.g.query(q, p or {}).result_set

    # -- tool 1: candidate symbols for issue text ---------------------------
    def find_candidates(self, issue_text: str, title: str = "",
                        linked_pr_files: list[str] | None = None) -> list[dict]:
        """Deterministic lexical candidates, expanded one graph hop.

        Ranking: traceback frame > named in the issue title > defined in a file changed by a
        linked PR > full dotted name in body > matching module/class
        variable > bare name > parameter name > defined in a named file
        > most-called symbols of a variable-implicated file > external
        touch point. The surfacing tier stays weak: variable names are
        common enough ('packages', 'default') that amplifying an
        implicated file's central symbols above the lexical tiers
        flooded the top five and regressed the frozen sample (measured,
        reverted). It only fills packets the direct signals could not. Within a tier, candidates closer (by
        call edges, up to 2 hops) to a traceback-frame seed sort first.
        Never claims relevance; reports why returned. The linked-PR tier ties the full-dotted-name
        tier (a human linked the PR; its changed files are curated
        evidence) and sits above module variables, bare names,
        parameter names, and the noisy external touch point. It fires
        only when the issue links a same-repo PR, so cases without one
        are unaffected.
        """
        title_idents = set(identifiers(title)) if title else set()
        idents = identifiers(issue_text)
        cands: list[dict] = []

        def _have(key: str) -> bool:
            return any(c["symbol_id"] == key for c in cands)

        # 0) traceback frames: strongest signal, code the reporter ran
        for fpath, func in frames(issue_text):
            tail = "/".join(fpath.split("/")[-2:])
            rows = self._rows(
                "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE s.kind <> 'module' AND s.name = $fn AND "
                "      (f.path = $p OR f.path ENDS WITH $tail OR "
                "       f.path ENDS WITH '/' + $p) "
                "RETURN DISTINCT s.id, s.qualname, s.kind, f.path "
                "ORDER BY s.qualname LIMIT 5",
                {"fn": func, "p": fpath, "tail": tail})
            for r in rows:
                if not _have(r[0]):
                    cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                                  "file": r[3],
                                  "reason": f"traceback frame '{func}' in '{r[3]}'",
                                  "_rank": 0})
        # files changed in PRs the issue links (regression bridge): a
        # human linked the PR; its files are curated evidence, ranked
        # with the full-dotted-name tier and inserted first within it
        if linked_pr_files:
            py = [p for p in linked_pr_files if p.endswith(".py")][:20]
            if py:
                rows = self._rows(
                    "UNWIND $ps AS p "
                    "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                    "WHERE s.kind <> 'module' AND "
                    "      (f.path = p OR f.path ENDS WITH '/' + p) "
                    "RETURN DISTINCT s.id, s.qualname, s.kind, f.path "
                    "ORDER BY s.qualname LIMIT 10",
                    {"ps": py})
                for r in rows:
                    if not _have(r[0]):
                        cands.append({"symbol_id": r[0], "qualname": r[1],
                                      "kind": r[2], "file": r[3],
                                      "reason": f"defined in '{r[3]}', changed in a PR linked from the issue",
                                      "_rank": 2})
        if not idents:
            cands.sort(key=lambda c: c["_rank"])
            for c in cands:
                c.pop("_rank", None)
            return cands[:MAX_CANDIDATES]

        rows = self._rows(
            "UNWIND $ids AS id "
            "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
            "WHERE s.kind <> 'module' AND "
            "      (s.qualname = id OR s.qualname ENDS WITH '.' + id OR s.name = id) "
            "RETURN DISTINCT s.id, s.qualname, s.kind, s.name, f.path "
            "ORDER BY s.qualname LIMIT $lim",
            {"ids": idents, "lim": MAX_CANDIDATES})
        dotted = {i for i in idents if "." in i}
        for r in rows:
            in_title = r[3] in title_idents or r[1] in title_idents or \
                any(r[1].endswith("." + t) for t in title_idents)
            exact = r[1] in dotted or any(r[1].endswith("." + d) for d in dotted)
            if in_title:
                rank, reason = 1, f"name '{r[3]}' appears in the issue title"
            elif exact:
                rank, reason = 2, f"full dotted name '{r[1]}' in issue text"
            else:
                rank, reason = 3, f"name '{r[3]}' appears in issue text"
            if not _have(r[0]):
                cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                              "file": r[4], "reason": reason, "_rank": rank})
            elif in_title or exact:
                for c in cands:
                    if c["symbol_id"] == r[0] and c["_rank"] > rank:
                        c["_rank"], c["reason"] = rank, reason

        # module-/class-level variables named in the issue (constants, flags);
        # dotted mentions match on the root: '_CONSTANT.method' -> '_CONSTANT'
        var_ids = list(idents)
        var_ids += [i.split(".")[0] for i in idents if "." in i
                    and not i.endswith(".py")]
        rows = self._rows(
            "UNWIND $ids AS id "
            "MATCH (v:ModuleVar)-[:DEFINED_IN]->(f:File) "
            "WHERE v.name = id "
            "RETURN DISTINCT v.id, v.qualname, v.name, f.path, v.line "
            "ORDER BY v.qualname LIMIT $lim",
            {"ids": var_ids, "lim": MAX_CANDIDATES})
        var_files: dict[str, str] = {}
        for r in rows:
            if not _have(r[0]):
                cands.append({"symbol_id": r[0], "qualname": r[1], "kind": "variable",
                              "file": r[3], "line_start": r[4], "line_end": r[4],
                              "reason": f"module/class variable '{r[2]}' appears in issue text",
                              "_rank": 2})
            var_files.setdefault(r[3], r[2])

        # a named variable implicates its file but surfaces none of the
        # file's functions; add the file's most-called symbols (caller
        # in-degree, top 3 per file, deterministic) as a weak tier above
        # only external touch points
        if var_files:
            rows = self._rows(
                "UNWIND $fs AS fp "
                "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE f.path = fp AND s.kind <> 'module' "
                "OPTIONAL MATCH (caller:Symbol)-[:CALLS]->(s) "
                "WITH fp, s, count(DISTINCT caller) AS deg "
                "ORDER BY deg DESC, s.qualname "
                "WITH fp, collect({id: s.id, q: s.qualname, k: s.kind, "
                "deg: deg})[0..3] AS top "
                "UNWIND top AS t "
                "RETURN t.id, t.q, t.k, fp, t.deg "
                "ORDER BY fp, t.deg DESC, t.q",
                {"fs": sorted(var_files)})
            for r in rows:
                if not _have(r[0]):
                    cands.append({"symbol_id": r[0], "qualname": r[1],
                                  "kind": r[2], "file": r[3],
                                  "reason": f"among the most-called symbols in '{r[3]}' "
                                            f"(implicated by variable "
                                            f"'{var_files[r[3]]}'), {r[4]} callers",
                                  "_rank": 6})

        # external references: code that touches a named third-party/stdlib
        # name (e.g. 'hashlib.sha1' in a FIPS issue) - dependency touch points
        ext_ids = [i for i in idents if not i.endswith(".py")]
        if ext_ids:
            rows = self._rows(
                "UNWIND $ids AS id "
                "MATCH (e:External)<-[r]-(s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE (e.expr = id OR e.expr ENDS WITH '.' + id) "
                "RETURN DISTINCT s.id, s.qualname, s.kind, id, f.path, type(r) "
                "ORDER BY s.qualname LIMIT $lim",
                {"ids": ext_ids, "lim": MAX_CANDIDATES})
            for r in rows:
                if not _have(r[0]):
                    # weakest signal: a touch point, not the fault site; keep
                    # below name/parameter/file matches so it never displaces
                    cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                                  "file": r[4],
                                  "reason": f"{r[5]} external '{r[3]}' named in issue text",
                                  "_rank": 7})

        # parameter names: 'flag_value' style hints toward the function
        param_ids = [i for i in idents if "_" in i and "." not in i]
        if param_ids:
            rows = self._rows(
                "UNWIND $ids AS id "
                "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE s.kind <> 'module' AND id IN s.params "
                "RETURN DISTINCT s.id, s.qualname, s.kind, id, f.path "
                "ORDER BY s.qualname LIMIT $lim",
                {"ids": param_ids, "lim": MAX_CANDIDATES})
            for r in rows:
                if not _have(r[0]):
                    cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                                  "file": r[4],
                                  "reason": f"parameter '{r[3]}' appears in issue text",
                                  "_rank": 4})

        # file-path mentions resolve to symbols in that file (capped)
        paths = [i for i in idents if i.endswith(".py")]
        if paths:
            rows = self._rows(
                "UNWIND $ps AS p "
                "MATCH (s:Symbol)-[:DEFINED_IN]->(f:File) "
                "WHERE s.kind <> 'module' AND "
                "      (f.path = p OR f.path ENDS WITH '/' + p) "
                "RETURN DISTINCT s.id, s.qualname, s.kind, f.path "
                "ORDER BY s.qualname LIMIT 10",
                {"ps": paths})
            for r in rows:
                if not _have(r[0]):
                    cands.append({"symbol_id": r[0], "qualname": r[1], "kind": r[2],
                                  "file": r[3],
                                  "reason": f"defined in file '{r[3]}' named in issue text",
                                  "_rank": 5})
        # tie-break within a tier: graph distance to a traceback seed.
        # A fault usually sits in the neighborhood the reporter executed;
        # candidates 1-2 calls from a traceback frame outrank same-tier
        # candidates with no path. Cases without traceback frames are
        # unaffected (no seeds, original order kept).
        seeds = [c["symbol_id"] for c in cands if c["_rank"] == 0]
        dist: dict[str, int] = {}
        others = [c["symbol_id"] for c in cands if c["_rank"] > 0]
        if seeds and others:
            rows = self._rows(
                "MATCH p = (a:Symbol)-[:CALLS*1..2]-(b:Symbol) "
                "WHERE a.id IN $seeds AND b.id IN $others "
                "RETURN b.id, min(length(p))",
                {"seeds": seeds, "others": others})
            dist = {r[0]: r[1] for r in rows}
        cands.sort(key=lambda c: (c["_rank"], dist.get(c["symbol_id"], 99)))
        for c in cands:
            c.pop("_rank", None)
        return cands[:MAX_CANDIDATES]

    # -- tool 2: one symbol, full card ---------------------------------------
    def inspect_symbol(self, qualname: str) -> dict | None:
        rows = self._rows(
            "MATCH (s:Symbol {qualname: $q})-[:DEFINED_IN]->(f:File) "
            "RETURN s.id, s.kind, s.line_start, s.line_end, f.path, f.hash LIMIT 1",
            {"q": qualname})
        if not rows:
            return None
        r = rows[0]
        return {"symbol_id": r[0], "qualname": qualname, "kind": r[1],
                "line_start": r[2], "line_end": r[3], "file": r[4], "file_hash": r[5]}

    def inspect_variable(self, qualname: str) -> dict | None:
        rows = self._rows(
            "MATCH (v:ModuleVar {qualname: $q})-[:DEFINED_IN]->(f:File) "
            "RETURN v.id, v.line, f.path, f.hash LIMIT 1",
            {"q": qualname})
        if not rows:
            return None
        r = rows[0]
        return {"symbol_id": r[0], "qualname": qualname, "kind": "variable",
                "line_start": r[1], "line_end": r[1], "file": r[2], "file_hash": r[3]}

    # -- tool 3: bounded callers/callees -------------------------------------
    def neighbors(self, qualname: str, direction: str = "callers",
                  hops: int = 1, exact_only: bool = False) -> list[dict]:
        hops = max(1, min(hops, MAX_HOPS))
        if direction == "callers":
            pattern = "(o:Symbol)-[:CALLS*1..%d]->(s:Symbol {qualname: $q})" % hops
        else:
            pattern = "(s:Symbol {qualname: $q})-[:CALLS*1..%d]->(o:Symbol)" % hops
        exact = " AND ALL(c IN relationships(p) WHERE c.tag = 'EXACT')" \
            if exact_only else ""
        # one graph-native bounded traversal: the same reachable set as BFS,
        # depth = shortest-path length, tag = final edge of a shortest path.
        # The 3-hop cap and result cap keep the cost explicit. Deterministic:
        # shortest paths prefer the weakest final-edge tag (conservative) and
        # the capped output is ordered by depth then qualname, so reruns of
        # the same snapshot return the same callers in the same order.
        rows = self._rows(
            f"MATCH p = {pattern} WHERE o.kind <> 'module'{exact} "
            "WITH o, p ORDER BY length(p), "
            "relationships(p)[length(p) - 1].tag DESC "
            "WITH o, head(collect(p)) AS sp "
            "RETURN o.qualname, length(sp) AS depth, "
            "relationships(sp)[length(sp) - 1].tag AS tag "
            "ORDER BY depth, o.qualname LIMIT $lim",
            {"q": qualname, "lim": MAX_RESULTS})
        return [{"qualname": r[0], "depth": r[1], "tag": r[2]} for r in rows]

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

    # -- tool 7: who touches an external name ----------------------------------
    def external_usage(self, name: str) -> list[dict]:
        """Internal symbols calling or referencing an external (stdlib or
        third-party) dotted name, e.g. 'hashlib.sha1'. Bounded; EXACT/CALLS
        and REFS relations only. Useful for dependency-impact questions."""
        rows = self._rows(
            "MATCH (e:External)<-[r]-(s:Symbol)-[:DEFINED_IN]->(f:File) "
            "WHERE e.expr = $n OR e.expr ENDS WITH '.' + $n "
            "RETURN DISTINCT s.qualname, type(r), r.tag, f.path LIMIT $lim",
            {"n": name, "lim": MAX_RESULTS})
        return [{"qualname": r[0], "via": r[1], "tag": r[2], "file": r[3]}
                for r in rows]

    # -- tool 6: graph stats for pack header -----------------------------------
    def stats(self) -> dict:
        def n(q):
            return self.g.query(q).result_set[0][0]
        snap = self.g.query("MATCH (s:Snapshot) RETURN s.repo, s.sha LIMIT 1").result_set[0]
        return {"repo": snap[0], "sha": snap[1],
                "symbols": n("MATCH (s:Symbol) WHERE s.kind <> 'module' RETURN count(s)"),
                "calls_exact": n("MATCH ()-[c:CALLS {tag:'EXACT'}]->() RETURN count(c)"),
                "calls_name": n("MATCH ()-[c:CALLS {tag:'NAME_ONLY'}]->() RETURN count(c)"),
                "calls_unresolved": n("MATCH ()-[c:CALLS {tag:'UNRESOLVED'}]->() RETURN count(c)"),
            "calls_external": n("MATCH ()-[c:CALLS {tag:'EXTERNAL'}]->() RETURN count(c)")}
