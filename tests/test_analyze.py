"""Analyzer contract tests: resolution tags must be honest."""
from patchpath.analyze import EXACT, NAME_ONLY, UNRESOLVED, analyze_files, module_name, is_test_file

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
    assert calls["os.path.join"].tag == UNRESOLVED


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
