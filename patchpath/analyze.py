"""Static Python analysis: symbols, calls, imports, test candidates.

AST-only. Never imports or executes analyzed code. Every relationship is
tagged EXACT (resolved to a definition in this snapshot), NAME_ONLY (same
unqualified name exists; plausible, not proven) or UNRESOLVED. Dynamic
dispatch, decorators, monkey-patching and import side effects defeat static
analysis; misses stay visible as UNRESOLVED, never invented.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

EXACT, NAME_ONLY, UNRESOLVED = "EXACT", "NAME_ONLY", "UNRESOLVED"


def symbol_id(repo_sha: str, path: str, qualname: str) -> str:
    return hashlib.sha256(f"{repo_sha}:{path}:{qualname}".encode()).hexdigest()[:16]


def module_name(path: str) -> str:
    """pkg/sub/mod.py -> pkg.sub.mod ; pkg/__init__.py -> pkg"""
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class Symbol:
    qualname: str
    kind: str           # "function" | "class" | "method"
    file: str
    line_start: int
    line_end: int
    is_test: bool = False

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


@dataclass
class Call:
    caller: str          # qualname of enclosing symbol ("" = module scope)
    target_expr: str     # as written, e.g. "os.path.join" or "helper"
    target_qual: str | None   # resolved internal qualname if EXACT
    tag: str             # EXACT | NAME_ONLY | UNRESOLVED
    line: int


@dataclass
class ImportEdge:
    importer: str        # module qualname prefix (file module)
    imported: str        # module name as resolved absolutely if internal
    internal: bool
    line: int


@dataclass
class FileAnalysis:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    imports: list[ImportEdge] = field(default_factory=list)
    parse_error: str | None = None


def is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in path or "/test/" in path


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, known_qualnames: set[str], by_name: dict[str, list[str]],
                 src_prefix: str = ""):
        self.path = path
        self.module = module_name(path)
        self.known = known_qualnames
        self.by_name = by_name
        self.src_prefix = src_prefix   # "src." when the repo uses src/ layout
        self.out = FileAnalysis(path=path)
        self.scope: list[str] = []       # nesting of def/class names
        self.scope_kind: list[str] = []  # parallel: "class" | "function"
        self.import_map: dict[str, str] = {}  # local alias -> absolute module or module.name
        self.test_file = is_test_file(path)

    # --- scope tracking -------------------------------------------------
    def _qual(self, name: str) -> str:
        return ".".join([self.module, *self.scope, name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node, "class")
        self.scope.append(node.name)
        self.scope_kind.append("class")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kind.pop()

    def visit_FunctionDef(self, node) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node) -> None:
        self._visit_func(node)

    def _visit_func(self, node) -> None:
        kind = "method" if self.scope else "function"
        sym = self._add_symbol(node, kind)
        if self.test_file and (node.name.startswith("test_") or node.name.startswith("Test")):
            sym.is_test = True
        self.scope.append(node.name)
        self.scope_kind.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kind.pop()

    def _add_symbol(self, node, kind: str) -> Symbol:
        sym = Symbol(
            qualname=self._qual(node.name), kind=kind, file=self.path,
            line_start=node.lineno, line_end=getattr(node, "end_lineno", node.lineno),
            is_test=self.test_file and node.name.startswith(("test_", "Test")))
        self.out.symbols.append(sym)
        return sym

    # --- imports ----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            local = a.asname or a.name.split(".")[0]
            self.import_map[local] = a.name
            self.out.imports.append(ImportEdge(self.module, a.name, False, node.lineno))
        # internal marking fixed up later (needs full module set)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:  # relative import: resolve against this module's package
            parts = self.module.split(".")
            # for __init__.py the module name IS the package; others drop the
            # module leaf, then climb (level - 1) more packages
            if not self.path.endswith("__init__.py"):
                parts = parts[:-1]
            if node.level > 1:
                parts = parts[:-(node.level - 1)]
            base = ".".join([*parts, base]) if base else ".".join(parts)
        for a in node.names:
            if a.name == "*":
                continue
            local = a.asname or a.name
            full = f"{base}.{a.name}" if base else a.name
            # alias may point at a module OR at a name inside a module
            self.import_map[local] = full
            self.out.imports.append(ImportEdge(self.module, base, False, node.lineno))

    # --- calls --------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        expr, root = self._expr(node.func)
        if expr:
            caller = ".".join([self.module, *self.scope]) if self.scope else self.module
            self.out.calls.append(self._resolve(caller, expr, root, node.lineno))
        self.generic_visit(node)

    def _expr(self, func) -> tuple[str | None, str | None]:
        """Return (dotted expression as written, root name)."""
        if isinstance(func, ast.Name):
            return func.id, func.id
        if isinstance(func, ast.Attribute):
            parts = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                return ".".join(reversed(parts)), cur.id
        return None, None

    def _resolve(self, caller: str, expr: str, root: str, line: int) -> Call:
        # 0) self.attr / cls.attr: resolve within the nearest enclosing class
        if root in ("self", "cls") and "." in expr:
            attr = expr.split(".", 1)[1]
            for i in range(len(self.scope) - 1, -1, -1):
                if self.scope_kind[i] == "class":
                    qual = ".".join([self.module, *self.scope[:i + 1], attr])
                    if qual in self.known:
                        return Call(caller, expr, qual, EXACT, line)
            # not found in any enclosing class: inherited or dynamic; honest
            leaf = expr.rsplit(".", 1)[-1]
            hits = self.by_name.get(leaf, [])
            if len(hits) == 1:
                return Call(caller, expr, hits[0], NAME_ONLY, line)
            return Call(caller, expr, None, UNRESOLVED, line)
        # 1) direct local/nested definition
        candidates = []
        if "." not in expr:
            candidates.append(".".join([self.module, *self.scope, expr]) if self.scope else f"{self.module}.{expr}")
            candidates.append(f"{self.module}.{expr}")
        # 2) through import map
        mapped = self.import_map.get(root)
        if mapped:
            rest = expr[len(root):].lstrip(".")
            full = f"{mapped}.{rest}" if rest else mapped
            candidates.append(full)
            if self.src_prefix and not full.startswith(self.src_prefix):
                candidates.append(self.src_prefix + full)
            # 'from pkg import mod' then mod.func() -> pkg.mod.func
            # 'from pkg import func' then func() -> pkg.func
        for cand in candidates:
            if cand in self.known:
                return Call(caller, expr, cand, EXACT, line)
        # 3) name-only fallback: unqualified final name matches exactly one symbol
        leaf = expr.rsplit(".", 1)[-1]
        hits = self.by_name.get(leaf, [])
        if len(hits) == 1:
            return Call(caller, expr, hits[0], NAME_ONLY, line)
        return Call(caller, expr, None, UNRESOLVED, line)


def analyze_files(files: list[tuple[str, str]], repo_sha: str) -> list[FileAnalysis]:
    """Two-pass analysis: collect all symbols, then resolve calls.

    files: list of (path, source_text). repo_sha: pins symbol IDs.
    """
    parsed: list[tuple[str, ast.AST | None, str | None]] = []
    for path, text in files:
        try:
            parsed.append((path, ast.parse(text, filename=path), None))
        except SyntaxError as e:
            parsed.append((path, None, f"{e.__class__.__name__}: {e}"))

    # pass 1: symbol universe
    known: set[str] = set()
    by_name: dict[str, list[str]] = {}
    modules: set[str] = set()
    presymbols: dict[str, list[Symbol]] = {}
    for path, tree, err in parsed:
        modules.add(module_name(path))
        if tree is None:
            continue
        syms = _collect_symbols(path, tree)
        presymbols[path] = syms
        for s in syms:
            known.add(s.qualname)
            by_name.setdefault(s.name, []).append(s.qualname)

    # pass 2: full visit with resolution against the complete universe
    # src-layout: top-level packages live under "src."
    src_prefix = ""
    tops = {m.split(".")[0] for m in modules}
    if "src" in tops and any(m.startswith("src.") for m in modules):
        src_prefix = "src."

    out: list[FileAnalysis] = []
    for path, tree, err in parsed:
        v = _Visitor(path, known, by_name, src_prefix)
        if tree is not None:
            v.visit(tree)
        for imp in v.out.imports:
            imp.internal = (
                imp.imported in modules
                or any(m.startswith(imp.imported + ".") for m in modules)
                or (src_prefix and (src_prefix + imp.imported) in modules)
                or (src_prefix and any(
                    m.startswith(src_prefix + imp.imported + ".") for m in modules)))
        v.out.parse_error = err
        out.append(v.out)
    return out


def _collect_symbols(path: str, tree: ast.AST) -> list[Symbol]:
    """Symbol-only walk so pass 1 needs no resolution state."""
    syms: list[Symbol] = []
    mod = module_name(path)
    test_file = is_test_file(path)

    class S(ast.NodeVisitor):
        def __init__(self):
            self.scope: list[str] = []

        def _add(self, node, kind):
            qual = ".".join([mod, *self.scope, node.name])
            syms.append(Symbol(qual, kind, path, node.lineno,
                               getattr(node, "end_lineno", node.lineno),
                               is_test=test_file and node.name.startswith(("test_", "Test"))))
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_ClassDef(self, n):
            self._add(n, "class")

        def visit_FunctionDef(self, n):
            self._add(n, "method" if self.scope else "function")

        def visit_AsyncFunctionDef(self, n):
            self._add(n, "method" if self.scope else "function")

    S().visit(tree)
    return syms


def analyze_snapshot(files: list[tuple[str, str]], repo_sha: str) -> list[FileAnalysis]:
    return analyze_files(files, repo_sha)
