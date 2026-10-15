"""Static Python analysis: symbols, calls, imports, test candidates.

AST-only. Never imports or executes analyzed code. Every relationship is
tagged EXACT (resolved to a definition in this snapshot), NAME_ONLY (same
unqualified name exists; plausible, not proven) or UNRESOLVED. Dynamic
dispatch, decorators, monkey-patching and import side effects defeat static
analysis; misses stay visible as UNRESOLVED, never invented.

Resolution coverage:
- direct local/module definitions and import-map bindings (EXACT)
- self./cls. attributes on the enclosing class and its statically
  resolvable ancestors (EXACT); unresolved inheritance stays honest
- star imports from internal modules, bounded by __all__ when defined
- module- and class-level assignments are recorded as variables for
  candidate search; they are not symbols and do not affect symbol counts
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

EXACT, NAME_ONLY, UNRESOLVED, EXTERNAL = "EXACT", "NAME_ONLY", "UNRESOLVED", "EXTERNAL"

import builtins as _builtins_mod
BUILTINS = set(dir(_builtins_mod))


def is_internal_module(mapped: str, modules: set[str], src_prefix: str) -> bool:
    """Does an imported dotted name resolve inside this snapshot?"""
    return (mapped in modules
            or any(m.startswith(mapped + ".") for m in modules)
            or (src_prefix and ((src_prefix + mapped) in modules or any(
                m.startswith(src_prefix + mapped + ".") for m in modules))))


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
    params: list[str] = field(default_factory=list)   # functions/methods
    bases: list[str] = field(default_factory=list)    # classes, as written

    @property
    def name(self) -> str:
        return self.qualname.rsplit(".", 1)[-1]


@dataclass
class ModuleVar:
    """Module- or class-level assignment target (constants, flags, aliases)."""
    qualname: str       # module.NAME or module.Class.NAME
    name: str
    file: str
    line: int


@dataclass
class Call:
    caller: str          # qualname of enclosing symbol ("" = module scope)
    target_expr: str     # as written, e.g. "os.path.join" or "helper"
    target_qual: str | None   # resolved internal qualname if EXACT
    tag: str             # EXACT | NAME_ONLY | UNRESOLVED
    line: int


@dataclass
class Ref:
    """Non-call reference to a resolved name: decorator, parameter default,
    or module/class-level assignment value (e.g. digest_method=hashlib.sha1).
    Only resolved targets are recorded; unresolved references are omitted."""
    referrer: str        # qualname of enclosing symbol (module qual at top level)
    expr: str            # as written
    target_qual: str | None  # internal qualname if resolved
    tag: str             # EXACT | EXTERNAL
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
    refs: list[Ref] = field(default_factory=list)
    module_vars: list[ModuleVar] = field(default_factory=list)
    parse_error: str | None = None


def is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "/tests/" in path or "/test/" in path


def _expr_str(node) -> str | None:
    """Dotted string for Name/Attribute chains (also through Subscript)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    if isinstance(node, ast.Subscript):
        return _expr_str(node.value)
    return None


def _func_params(node) -> list[str]:
    a = node.args
    names = [x.arg for x in list(getattr(a, "posonlyargs", [])) + list(a.args)
             + list(a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return [n for n in names if n not in ("self", "cls")]


def _resolve_expr(module: str, expr: str, import_map: dict[str, str],
                  known: set[str], src_prefix: str) -> str | None:
    """Resolve a dotted expression to a known qualname, or None."""
    candidates = []
    if "." not in expr:
        candidates.append(f"{module}.{expr}")
    root = expr.split(".")[0]
    mapped = import_map.get(root)
    if mapped:
        rest = expr[len(root):].lstrip(".")
        full = f"{mapped}.{rest}" if rest else mapped
        candidates.append(full)
        if src_prefix and not full.startswith(src_prefix):
            candidates.append(src_prefix + full)
    for cand in candidates:
        if cand in known:
            return cand
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, known_qualnames: set[str], by_name: dict[str, list[str]],
                 src_prefix: str, class_bases: dict[str, list[str]],
                 class_module: dict[str, str], import_maps: dict[str, dict[str, str]],
                 modules: set[str], reexports: dict[str, dict[str, str]] | None = None,
                 class_qualnames: set[str] | None = None,
                 fixture_types: dict[str, str] | None = None):
        self.path = path
        self.module = module_name(path)
        self.known = known_qualnames
        self.by_name = by_name
        self.src_prefix = src_prefix   # "src." when the repo uses src/ layout
        self.class_bases = class_bases
        self.class_module = class_module
        self.import_maps = import_maps
        self.modules = modules
        self.reexports = reexports or {}
        self.class_qualnames = class_qualnames or set()
        self.fixture_types = fixture_types or {}
        self.var_types: dict[str, str] = {}
        self.out = FileAnalysis(path=path)
        self.scope: list[str] = []       # nesting of def/class names
        self.scope_kind: list[str] = []  # parallel: "class" | "function"
        self.import_map: dict[str, str] = import_maps.get(self.module, {})
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

    def _add_symbol(self, node, kind: str) -> None:
        # symbols come from the pass-1 universe; nothing to emit here
        return None

    # --- imports ----------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.out.imports.append(ImportEdge(self.module, a.name, False, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._importfrom_base(node)
        for a in node.names:
            if a.name == "*":
                continue
            self.out.imports.append(ImportEdge(self.module, base, False, node.lineno))

    def _importfrom_base(self, node: ast.ImportFrom) -> str:
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
        return base

    # --- non-call references -------------------------------------------------
    def _ref(self, node, lineno, referrer: str | None = None) -> None:
        # values passed through a call in a captured context are still
        # references: staticmethod(hashlib.sha1), partial(mod.func), ...
        if isinstance(node, ast.Call):
            for a in list(node.args) + [k.value for k in node.keywords]:
                self._ref(a, lineno, referrer)
            return
        expr = _expr_str(node)
        if not expr:
            return
        root = expr.split(".")[0]
        if root in ("self", "cls") or root in BUILTINS:
            return
        if referrer is None:
            referrer = ".".join([self.module, *self.scope]) if self.scope else self.module
        tq = _resolve_expr(self.module, expr, self.import_map,
                           self.known, self.src_prefix)
        if tq:
            self.out.refs.append(Ref(referrer, expr, tq, EXACT, lineno))
        elif root in self.import_map and not is_internal_module(
                self.import_map[root], self.modules, self.src_prefix):
            self.out.refs.append(Ref(referrer, expr, None, EXTERNAL, lineno))

    def _visit_func(self, node) -> None:
        kind = "method" if self.scope else "function"
        self._add_symbol(node, kind)
        func_qual = self._qual(node.name)
        for d in node.decorator_list:
            self._ref(d if not isinstance(d, ast.Call) else d.func, node.lineno,
                      referrer=func_qual)
        for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
            self._ref(d, node.lineno, referrer=func_qual)
        # local constructor bindings do not leak across function scopes;
        # fixture-named parameters bind to the fixture's returned class
        outer_types = self.var_types
        self.var_types = {p: self.fixture_types[p] for p in _func_params(node)
                          if p in self.fixture_types}
        self.scope.append(node.name)
        self.scope_kind.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kind.pop()
        self.var_types = outer_types

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.scope_kind and self.scope_kind[-1] == "function":
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self._track_ctor(node.targets[0].id, node.value)
        elif node.value:
            self._ref(node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.scope_kind and self.scope_kind[-1] == "function":
            if isinstance(node.target, ast.Name) and node.value:
                self._track_ctor(node.target.id, node.value)
        elif node.value:
            self._ref(node.value, node.lineno)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                self._track_ctor(item.optional_vars.id, item.context_expr)
        self.generic_visit(node)

    def _track_ctor(self, name: str, value) -> None:
        """x = SomeClass(...) / with SomeClass() as x: remember the static
        class binding so x.method() can resolve EXACT. Only constructor
        calls that resolve to a class in this snapshot count."""
        if not isinstance(value, ast.Call):
            return
        expr = _expr_str(value.func)
        if not expr:
            return
        tq = _resolve_expr(self.module, expr, self.import_map,
                           self.known, self.src_prefix)
        if not tq:
            rest = expr.split(".", 1)
            if len(rest) == 2:
                m = self.import_map.get(rest[0], "")
                table = self.reexports.get(m) or self.reexports.get(self.src_prefix + m, {})
                target = table.get(rest[1])
                tq = target if target in self.known else None
        if tq and tq in self.class_qualnames:
            self.var_types[name] = tq

    # --- calls --------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        expr, root = None, None
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            expr = _expr_str(node.func)
            root = expr.split(".")[0] if expr else None
        if expr:
            caller = ".".join([self.module, *self.scope]) if self.scope else self.module
            self.out.calls.append(self._resolve(caller, expr, root, node.lineno))
        self.generic_visit(node)

    def _lookup_in_hierarchy(self, cls_qual: str, attr: str,
                             visited: set[str], depth: int = 0) -> str | None:
        """attr on cls_qual or a statically resolvable ancestor (EXACT only)."""
        if cls_qual in visited or depth > 6:
            return None
        visited.add(cls_qual)
        direct = cls_qual + "." + attr
        if direct in self.known:
            return direct
        for base_expr in self.class_bases.get(cls_qual, []):
            mod = self.class_module.get(cls_qual, self.module)
            bq = _resolve_expr(mod, base_expr, self.import_maps.get(mod, {}),
                               self.known, self.src_prefix)
            if not bq:
                # unresolved base expression: unique-name fallback for the
                # base itself (the hop is still a static binding candidate)
                leaf = base_expr.rsplit(".", 1)[-1]
                hits = [q for q in self.by_name.get(leaf, [])
                        if q in self.class_bases or q.rsplit(".", 1)[-1] == leaf]
                bq = hits[0] if len(hits) == 1 else None
            if bq:
                r = self._lookup_in_hierarchy(bq, attr, visited, depth + 1)
                if r:
                    return r
        return None

    def _resolve(self, caller: str, expr: str, root: str, line: int) -> Call:
        # 0) self.attr / cls.attr: enclosing class, then resolvable ancestors
        if root in ("self", "cls") and "." in expr:
            attr = expr.split(".", 1)[1]
            for i in range(len(self.scope) - 1, -1, -1):
                if self.scope_kind[i] == "class":
                    cls_qual = ".".join([self.module, *self.scope[:i + 1]])
                    hit = self._lookup_in_hierarchy(cls_qual, attr, set())
                    if hit:
                        return Call(caller, expr, hit, EXACT, line)
            # not found in any enclosing class hierarchy: dynamic; honest
            leaf = expr.rsplit(".", 1)[-1]
            hits = self.by_name.get(leaf, [])
            if len(hits) == 1:
                return Call(caller, expr, hits[0], NAME_ONLY, line)
            return Call(caller, expr, None, UNRESOLVED, line)
        # 0b) local variable bound to a known constructor: x.method()
        if root in self.var_types and "." in expr:
            cls = self.var_types[root]
            rest = expr[len(root) + 1:]
            if f"{cls}.{rest}" in self.known:
                return Call(caller, expr, f"{cls}.{rest}", EXACT, line)
            hit = self._lookup_in_hierarchy(cls, rest, set())
            if hit:
                return Call(caller, expr, hit, EXACT, line)
            # bound var whose class lacks the member: never guess an
            # unrelated same-named method; plain UNRESOLVED
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
            # 2b) package re-exports: click.echo -> src.click.utils.echo
            if rest:
                first, _, tail = rest.partition(".")
                table = self.reexports.get(mapped) or \
                    self.reexports.get(self.src_prefix + mapped, {})
                target = table.get(first)
                if target:
                    re_full = f"{target}.{tail}" if tail else target
                    candidates.append(re_full)
        for cand in candidates:
            if cand in self.known:
                return Call(caller, expr, cand, EXACT, line)
        # 3) external binding: builtin or imported third-party/stdlib root.
        # Not a gap in the snapshot; classified separately from UNRESOLVED.
        if root in BUILTINS:
            return Call(caller, expr, None, EXTERNAL, line)
        if mapped and not is_internal_module(mapped, self.modules, self.src_prefix):
            return Call(caller, expr, None, EXTERNAL, line)
        # 4) name-only fallback: unqualified final name matches exactly one symbol
        leaf = expr.rsplit(".", 1)[-1]
        hits = self.by_name.get(leaf, [])
        if len(hits) == 1:
            return Call(caller, expr, hits[0], NAME_ONLY, line)
        return Call(caller, expr, None, UNRESOLVED, line)


def _const_str_list(node) -> list[str] | None:
    """['a', 'b'] / ('a',) literal -> list of strings, else None."""
    if isinstance(node, (ast.List, ast.Tuple)):
        out = []
        for el in node.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                out.append(el.value)
            else:
                return None
        return out
    return None


class _Collector(ast.NodeVisitor):
    """Pass 1 per file: symbols (with params/bases), variables, imports."""

    def __init__(self, path: str):
        self.path = path
        self.module = module_name(path)
        self.test_file = is_test_file(path)
        self.symbols: list[Symbol] = []
        self.module_vars: list[ModuleVar] = []
        self.import_map: dict[str, str] = {}
        self.star_bases: list[str] = []
        self.all_list: list[str] | None = None
        self.scope: list[str] = []
        self.scope_kind: list[str] = []
        # pytest fixtures: name -> constructor expr of its single return call
        self.fixture_return_exprs: dict[str, str] = {}

    def _qual(self, name: str) -> str:
        return ".".join([self.module, *self.scope, name])

    @staticmethod
    def _is_fixture_deco(d) -> bool:
        f = d.func if isinstance(d, ast.Call) else d
        s = _expr_str(f)
        return bool(s) and s.rsplit(".", 1)[-1] == "fixture"

    @staticmethod
    def _fixture_return_expr(node) -> str | None:
        rets = [s.value.func for s in ast.walk(node)
                if isinstance(s, ast.Return) and isinstance(s.value, ast.Call)]
        if len(rets) == 1:
            return _expr_str(rets[0])
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [b for b in (_expr_str(x) for x in node.bases) if b]
        self.symbols.append(Symbol(
            self._qual(node.name), "class", self.path, node.lineno,
            getattr(node, "end_lineno", node.lineno),
            is_test=self.test_file and node.name.startswith(("test_", "Test")),
            bases=bases))
        self.scope.append(node.name)
        self.scope_kind.append("class")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kind.pop()

    def visit_FunctionDef(self, node) -> None:
        self._func(node)

    def visit_AsyncFunctionDef(self, node) -> None:
        self._func(node)

    def _func(self, node) -> None:
        kind = "method" if self.scope else "function"
        self.symbols.append(Symbol(
            self._qual(node.name), kind, self.path, node.lineno,
            getattr(node, "end_lineno", node.lineno),
            is_test=self.test_file and node.name.startswith(("test_", "Test")),
            params=_func_params(node)))
        if any(self._is_fixture_deco(d) for d in node.decorator_list):
            rexpr = self._fixture_return_expr(node)
            if rexpr:
                self.fixture_return_exprs[node.name] = rexpr
        self.scope.append(node.name)
        self.scope_kind.append("function")
        self.generic_visit(node)
        self.scope.pop()
        self.scope_kind.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self._assign(node.targets, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assign([node.target], node.value, node.lineno)
        self.generic_visit(node)

    def _assign(self, targets, value, lineno) -> None:
        # module- and class-level names only; function bodies are locals
        if self.scope_kind and self.scope_kind[-1] == "function":
            return
        names: list[str] = []
        for t in targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                names.extend(el.id for el in t.elts if isinstance(el, ast.Name))
        for n in names:
            if n == "__all__":
                vals = _const_str_list(value) if value is not None else None
                if vals is not None:
                    self.all_list = vals
                continue
            self.module_vars.append(ModuleVar(self._qual(n), n, self.path, lineno))

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            local = a.asname or a.name.split(".")[0]
            self.import_map[local] = a.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:
            parts = self.module.split(".")
            if not self.path.endswith("__init__.py"):
                parts = parts[:-1]
            if node.level > 1:
                parts = parts[:-(node.level - 1)]
            base = ".".join([*parts, base]) if base else ".".join(parts)
        for a in node.names:
            if a.name == "*":
                if base:
                    self.star_bases.append(base)
                continue
            local = a.asname or a.name
            full = f"{base}.{a.name}" if base else a.name
            self.import_map[local] = full


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

    # pass 1: symbol universe, variables, per-module import maps
    known: set[str] = set()
    by_name: dict[str, list[str]] = {}
    modules: set[str] = set()
    collectors: dict[str, _Collector] = {}
    for path, tree, err in parsed:
        modules.add(module_name(path))
        if tree is None:
            continue
        c = _Collector(path)
        c.visit(tree)
        collectors[path] = c
        for s in c.symbols:
            known.add(s.qualname)
            by_name.setdefault(s.name, []).append(s.qualname)

    # src-layout: top-level packages live under "src."
    src_prefix = ""
    tops = {m.split(".")[0] for m in modules}
    if "src" in tops and any(m.startswith("src.") for m in modules):
        src_prefix = "src."

    # expand star imports from internal modules (bounded by __all__)
    for path, c in collectors.items():
        for base in c.star_bases:
            real = base if any(m == base or m.startswith(base + ".") for m in modules) \
                else (src_prefix + base if src_prefix and any(
                    m == src_prefix + base or m.startswith(src_prefix + base + ".")
                    for m in modules) else None)
            if not real:
                continue  # external star import: cannot resolve statically
            src_mod = real
            exp = collectors  # collectors keyed by path; find module's file
            all_list = None
            for p2, c2 in exp.items():
                if module_name(p2) == src_mod and c2.all_list is not None:
                    all_list = c2.all_list
            for q in list(known):
                if not q.startswith(src_mod + "."):
                    continue
                rest = q[len(src_mod) + 1:]
                if "." in rest:
                    continue  # top-level names only
                if all_list is not None and rest not in all_list:
                    continue
                if all_list is None and rest.startswith("_"):
                    continue
                c.import_map.setdefault(rest, q)

    import_maps = {module_name(p): c.import_map for p, c in collectors.items()}

    # package re-exports: 'from .utils import echo' in click/__init__.py makes
    # 'click.echo' an alias for 'src.click.utils.echo'. One static hop, applied
    # repeatedly so re-exported modules chain (A re-exports B re-exports C).
    reexports: dict[str, dict[str, str]] = {}
    for mod, imap in import_maps.items():
        for local, full in imap.items():
            if full in known or any(m == full or m.startswith(full + ".")
                                    for m in modules):
                reexports.setdefault(mod, {})[local] = full
    for _ in range(4):
        changed = False
        for mod, table in reexports.items():
            for local, full in list(table.items()):
                nxt = reexports.get(full, {})
                for k2, v2 in nxt.items():
                    if k2 not in table:
                        table[k2] = f"{full}.{k2}" if not v2.startswith(full + ".") else v2
                        changed = True
        if not changed:
            break
    class_bases: dict[str, list[str]] = {}
    class_module: dict[str, str] = {}
    for p, c in collectors.items():
        for s in c.symbols:
            if s.kind == "class":
                class_bases[s.qualname] = s.bases
                class_module[s.qualname] = module_name(p)

    class_qualnames = {q for q, bases in class_bases.items()}

    # pytest fixtures: a test-function parameter named after a fixture whose
    # body is a single 'return SomeClass(...)' binds that parameter to the
    # class. Static, one-hop, EXACT-only - the same bar as _track_ctor.
    fixture_types: dict[str, str] = {}
    for p, c in collectors.items():
        if not c.fixture_return_exprs:
            continue
        mod = module_name(p)
        for fname, rexpr in c.fixture_return_exprs.items():
            tq = _resolve_expr(mod, rexpr, c.import_map, known, src_prefix)
            if not tq and "." in rexpr:
                r0, _, r1 = rexpr.partition(".")
                m = c.import_map.get(r0, "")
                table = reexports.get(m) or reexports.get(src_prefix + m, {})
                t = table.get(r1)
                tq = t if t in known else None
            if tq and tq in class_qualnames:
                fixture_types.setdefault(fname, tq)

    # pass 2: full visit with resolution against the complete universe
    out: list[FileAnalysis] = []
    for path, tree, err in parsed:
        v = _Visitor(path, known, by_name, src_prefix, class_bases,
                     class_module, import_maps, modules, reexports,
                     class_qualnames, fixture_types)
        if tree is not None:
            v.visit(tree)
        c = collectors.get(path)
        if c:
            v.out.symbols = c.symbols
            v.out.module_vars = c.module_vars
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


def analyze_snapshot(files: list[tuple[str, str]], repo_sha: str) -> list[FileAnalysis]:
    return analyze_files(files, repo_sha)
