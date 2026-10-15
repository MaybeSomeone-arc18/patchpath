"""Analyzer contract tests: resolution tags must be honest."""
from patchpath.analyze import EXACT, EXTERNAL, NAME_ONLY, UNRESOLVED, analyze_files, module_name, is_test_file

REPO = "testsha"


def _by_expr(out, path):
    fa = next(a for a in out if a.path == path)
    return {c.target_expr: c for c in fa.calls}


def test_module_name():
    assert module_name("pkg/__init__.py") == "pkg"
    assert module_name("pkg/sub/mod.py") == "pkg.sub.mod"
    assert is_test_file("tests/test_x.py")
    assert not is_test_file("src/core.py")


def test_exact_resolution_through_imports():
    out = analyze_files([
        ("pkg/__init__.py", ""),
        ("pkg/util.py", "def clean(x):\n    return x\n"),
        ("pkg/core.py", "from pkg.util import clean\ndef run():\n    return clean(1)\n"),
    ], REPO)
    calls = _by_expr(out, "pkg/core.py")
    assert calls["clean"].tag == EXACT
    assert calls["clean"].target_qual == "pkg.util.clean"


def test_relative_import_in_init():
    out = analyze_files([
        ("pkg/__init__.py", "from .core import go\n"),
        ("pkg/core.py", "def go():\n    return 1\ndef use():\n    return go()\n"),
    ], REPO)
    calls = _by_expr(out, "pkg/core.py")
    assert calls["go"].tag == EXACT


def test_unresolved_never_invented():
    out = analyze_files([
        ("a.py", "import os\ndef f():\n    return os.path.join('a','b')\n"),
    ], REPO)
    calls = _by_expr(out, "a.py")
    # os is imported and external: known third-party/stdlib, not a gap
    assert calls["os.path.join"].tag == EXTERNAL


def test_truly_unknown_stays_unresolved():
    out = analyze_files([
        ("a.py", "def f():\n    return mystery_thing()\n"),
        ("b.py", "def mystery_thing():\n    pass\ndef mystery_thing2():\n    pass\n"),
        ("c.py", "def mystery_thing():\n    pass\n"),
    ], REPO)
    calls = _by_expr(out, "a.py")
    assert calls["mystery_thing"].tag == UNRESOLVED


def test_builtin_is_external_not_unresolved():
    out = analyze_files([
        ("a.py", "def f(x):\n    return len(x)\n"),
    ], REPO)
    calls = _by_expr(out, "a.py")
    assert calls["len"].tag == EXTERNAL


def test_name_only_single_hit():
    out = analyze_files([
        ("a.py", "def unique_name_xyz():\n    pass\n"),
        ("b.py", "def caller():\n    return unique_name_xyz()\n"),
    ], REPO)
    calls = _by_expr(out, "b.py")
    assert calls["unique_name_xyz"].tag == NAME_ONLY
    assert calls["unique_name_xyz"].target_qual == "a.unique_name_xyz"


def test_ambiguous_name_not_claimed():
    out = analyze_files([
        ("a.py", "def dup():\n    pass\n"),
        ("c.py", "def dup():\n    pass\n"),
        ("b.py", "def caller():\n    return dup()\n"),
    ], REPO)
    calls = _by_expr(out, "b.py")
    assert calls["dup"].tag == UNRESOLVED


def test_parse_error_visible():
    out = analyze_files([("bad.py", "def broken(:\n")], REPO)
    assert out[0].parse_error is not None


def test_test_detection():
    out = analyze_files([
        ("tests/test_thing.py", "def test_it():\n    assert True\n"
                                "def helper():\n    pass\n"),
    ], REPO)
    syms = {s.qualname: s for s in out[0].symbols}
    assert syms["tests.test_thing.test_it"].is_test
    assert not syms["tests.test_thing.helper"].is_test


def test_self_method_resolution():
    out = analyze_files([
        ("pkg/eng.py", "class Engine:\n"
                       "    def run(self):\n"
                       "        return self.compute()\n"
                       "    def compute(self):\n"
                       "        return 42\n"),
    ], REPO)
    calls = _by_expr(out, "pkg/eng.py")
    assert calls["self.compute"].tag == EXACT
    assert calls["self.compute"].target_qual == "pkg.eng.Engine.compute"


def test_self_inherited_not_claimed_exact():
    out = analyze_files([
        ("pkg/eng.py", "class Engine(Base):\n"
                       "    def run(self):\n"
                       "        return self.compute()\n"),
        ("pkg/other.py", "class Unrelated:\n"
                         "    def compute(self):\n"
                         "        return 1\n"),
    ], REPO)
    calls = _by_expr(out, "pkg/eng.py")
    # single name hit allowed as NAME_ONLY suggestion, never EXACT
    assert calls["self.compute"].tag == NAME_ONLY


def test_src_layout_import_resolution():
    out = analyze_files([
        ("src/pkg/__init__.py", ""),
        ("src/pkg/core.py", "def clean(x):\n    return x\n"),
        ("tests/test_core.py", "from pkg.core import clean\n"
                               "def test_clean():\n    assert clean(1)\n"),
    ], REPO)
    calls = _by_expr(out, "tests/test_core.py")
    assert calls["clean"].tag == EXACT
    assert calls["clean"].target_qual == "src.pkg.core.clean"
    fa = next(a for a in out if a.path == "tests/test_core.py")
    assert fa.imports[0].internal


def test_inherited_method_resolves_exact():
    out = analyze_files([
        ("base.py", "class Base:\n    def helper(self):\n        pass\n"),
        ("sub.py", "from base import Base\nclass Sub(Base):\n    def run(self):\n        self.helper()\n"),
    ], REPO)
    calls = _by_expr(out, "sub.py")
    assert calls["self.helper"].tag == EXACT
    assert calls["self.helper"].target_qual == "base.Base.helper"


def test_unresolvable_base_stays_honest():
    out = analyze_files([
        ("sub.py", "import extlib\nclass Sub(extlib.Base):\n    def run(self):\n        self.helper()\n"),
    ], REPO)
    calls = _by_expr(out, "sub.py")
    assert calls["self.helper"].tag in (NAME_ONLY, UNRESOLVED)


def test_params_and_bases_recorded():
    out = analyze_files([
        ("m.py", "class Opt(Base, metaclass=M):\n    def __init__(self, flag_value, is_flag=False):\n        pass\n"),
    ], REPO)
    fa = next(a for a in out if a.path == "m.py")
    cls = next(s for s in fa.symbols if s.name == "Opt")
    init = next(s for s in fa.symbols if s.name == "__init__")
    assert cls.bases == ["Base", "metaclass=M"] or cls.bases == ["Base"]
    assert init.params == ["flag_value", "is_flag"]


def test_module_vars_recorded_not_symbols():
    out = analyze_files([
        ("m.py", "MAX_RETRIES = 3\nclass C:\n    KIND = 'x'\n    def f(self):\n        local_v = 1\n"),
    ], REPO)
    fa = next(a for a in out if a.path == "m.py")
    names = {v.qualname for v in fa.module_vars}
    assert "m.MAX_RETRIES" in names and "m.C.KIND" in names
    assert not any("local_v" in v for v in names)
    assert not any(s.name in ("MAX_RETRIES", "KIND") for s in fa.symbols)


def test_star_import_internal_expands():
    out = analyze_files([
        ("pkg/__init__.py", "__all__ = ['helper']\ndef helper():\n    pass\ndef _priv():\n    pass\n"),
        ("use.py", "from pkg import *\ndef go():\n    return helper()\n"),
    ], REPO)
    calls = _by_expr(out, "use.py")
    assert calls["helper"].tag == EXACT
    assert calls["helper"].target_qual == "pkg.helper"


def test_star_import_external_stays_external_or_unresolved():
    out = analyze_files([
        ("use.py", "from extmod import *\ndef go():\n    return whatever()\n"),
    ], REPO)
    calls = _by_expr(out, "use.py")
    assert calls["whatever"].tag in (NAME_ONLY, UNRESOLVED)


def test_refs_capture_defaults_decorators_assigns():
    out = analyze_files([
        ("m.py", "import hashlib\nfrom ext import registry\n\n"
                 "digest = hashlib.sha1\n\n"
                 "def helper():\n    pass\n\n"
                 "alias = helper\n\n"
                 "@registry.command\n"
                 "def f(x, digest_method=hashlib.sha256):\n    pass\n"),
    ], REPO)
    fa = next(a for a in out if a.path == "m.py")
    refs = {(r.referrer, r.expr, r.tag, r.target_qual) for r in fa.refs}
    assert ("m", "hashlib.sha1", EXTERNAL, None) in refs
    assert ("m.f", "hashlib.sha256", EXTERNAL, None) in refs
    assert ("m.f", "registry.command", EXTERNAL, None) in refs
    assert ("m", "helper", EXACT, "m.helper") in refs
    # calls are not double-recorded as refs; function bodies are not refs
    assert not any(r.expr == "registry.command" and r.referrer == "m" for r in fa.refs)


def test_refs_look_through_call_arguments():
    out = analyze_files([
        ("m.py", "import hashlib\nclass S:\n    default = staticmethod(hashlib.sha1)\n"),
    ], REPO)
    fa = next(a for a in out if a.path == "m.py")
    assert any(r.expr == "hashlib.sha1" and r.tag == EXTERNAL for r in fa.refs)


def test_package_reexport_resolves_exact():
    out = analyze_files([
        ("pkg/__init__.py", "from .utils import echo\n"),
        ("pkg/utils.py", "def echo():\n    pass\n"),
        ("use.py", "import pkg\ndef go():\n    pkg.echo()\n"),
    ], REPO)
    calls = _by_expr(out, "use.py")
    assert calls["pkg.echo"].tag == EXACT
    assert calls["pkg.echo"].target_qual == "pkg.utils.echo"


def test_package_reexport_class_method():
    out = analyze_files([
        ("pkg/__init__.py", "from .core import Group\n"),
        ("pkg/core.py", "class Group:\n    def get_command(self):\n        pass\n"),
        ("use.py", "import pkg\ndef go():\n    pkg.Group.get_command()\n"),
    ], REPO)
    calls = _by_expr(out, "use.py")
    assert calls["pkg.Group.get_command"].tag == EXACT
    assert calls["pkg.Group.get_command"].target_qual == "pkg.core.Group.get_command"


def test_local_constructor_inference():
    out = analyze_files([
        ("lib.py", "class Runner:\n    def invoke(self):\n        pass\n"),
        ("t.py", "from lib import Runner\ndef test_x():\n    runner = Runner()\n    runner.invoke()\n"),
    ], REPO)
    calls = _by_expr(out, "t.py")
    assert calls["runner.invoke"].tag == EXACT
    assert calls["runner.invoke"].target_qual == "lib.Runner.invoke"


def test_local_constructor_inherited_method():
    out = analyze_files([
        ("lib.py", "class Base:\n    def invoke(self):\n        pass\nclass Runner(Base):\n    pass\n"),
        ("t.py", "from lib import Runner\ndef test_x():\n    runner = Runner()\n    runner.invoke()\n"),
    ], REPO)
    calls = _by_expr(out, "t.py")
    assert calls["runner.invoke"].tag == EXACT
    assert calls["runner.invoke"].target_qual == "lib.Base.invoke"


def test_untracked_local_stays_honest():
    out = analyze_files([
        ("t.py", "def test_x(maker):\n    r = maker()\n    r.invoke()\n"),
    ], REPO)
    calls = _by_expr(out, "t.py")
    assert calls["r.invoke"].tag == UNRESOLVED


def test_ctor_bindings_do_not_leak_scopes():
    out = analyze_files([
        ("lib.py", "class A:\n    def go(self):\n        pass\nclass B:\n    pass\n"),
        ("t.py", "from lib import A, B\n"
                 "def t1():\n    x = A()\n    x.go()\n"
                 "def t2():\n    x = B()\n    x.go()\n"),
    ], REPO)
    calls = _by_expr(out, "t.py")
    assert calls["x.go"].tag == UNRESOLVED or calls["x.go"].target_qual != "lib.A.go"
    # t2's x is B which has no .go: must not resolve through t1's A binding
    t2calls = [c for c in calls.values()]
    assert not any(c.tag == EXACT and c.target_qual == "lib.A.go" and c.line > 4
                   for c in t2calls)


def test_src_layout_reexport_resolution():
    out = analyze_files([
        ("src/pkg/__init__.py", "from .utils import echo\n"),
        ("src/pkg/utils.py", "def echo():\n    pass\n"),
        ("tests/test_x.py", "import pkg\ndef t():\n    pkg.echo()\n"),
    ], REPO)
    calls = _by_expr(out, "tests/test_x.py")
    assert calls["pkg.echo"].tag == EXACT
    assert calls["pkg.echo"].target_qual == "src.pkg.utils.echo"


def test_fixture_param_binds_returned_class():
    out = analyze_files([
        ("lib.py", "class CliRunner:\n    def invoke(self):\n        pass\n"),
        ("conftest.py", "import pytest\nfrom lib import CliRunner\n\n\n@pytest.fixture(scope=\"function\")\ndef runner(request):\n    return CliRunner()\n"),
        ("tests/test_args.py", "def test_nargs(runner):\n    result = runner.invoke()\n"),
    ], REPO)
    calls = _by_expr(out, "tests/test_args.py")
    assert calls["runner.invoke"].tag == EXACT
    assert calls["runner.invoke"].target_qual == "lib.CliRunner.invoke"


def test_fixture_with_multiple_returns_stays_honest():
    out = analyze_files([
        ("lib.py", "class Runner:\n    def invoke(self):\n        pass\n"),
        ("conftest.py", "import pytest\nfrom lib import Runner\n\n\n@pytest.fixture\ndef runner(request):\n    if request.param:\n        return Runner()\n    return Runner()\n"),
        ("tests/test_x.py", "def test_a(runner):\n    runner.invoke()\n"),
    ], REPO)
    calls = _by_expr(out, "tests/test_x.py")
    assert calls["runner.invoke"].tag != EXACT


def test_fixture_name_shadowed_by_local_ctor():
    out = analyze_files([
        ("lib.py", "class A:\n    def go(self):\n        pass\nclass B:\n    def go(self):\n        pass\n"),
        ("conftest.py", "import pytest\nfrom lib import A\n\n\n@pytest.fixture\ndef x():\n    return A()\n"),
        ("t.py", "from lib import B\ndef test_y(x):\n    x = B()\n    x.go()\n"),
    ], REPO)
    calls = _by_expr(out, "t.py")
    # local assignment shadows the fixture binding
    assert calls["x.go"].tag == EXACT
    assert calls["x.go"].target_qual == "lib.B.go"
