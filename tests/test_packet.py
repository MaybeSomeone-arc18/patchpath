import hashlib

import pytest

from patchpath.packet import (Citation, CitationError, Packet, render_markdown,
                              validate_citation, _steps)


def _snapshot(tmp_path, text="a\nb\nc\nd\n"):
    (tmp_path / "pkg").mkdir()
    f = tmp_path / "pkg" / "m.py"
    f.write_text(text)
    return f, hashlib.sha256(f.read_bytes()).hexdigest()


def test_valid_citation_builds_pinned_url(tmp_path):
    _, h = _snapshot(tmp_path)
    c = validate_citation(tmp_path, "o/r", "abc123", "pkg/m.py", 2, 3, h)
    assert c.url == "https://github.com/o/r/blob/abc123/pkg/m.py#L2-L3"
    assert c.excerpt_hash == hashlib.sha256(b"b\nc").hexdigest()[:16]


def test_single_line_citation_uses_short_anchor(tmp_path):
    _, h = _snapshot(tmp_path)
    c = validate_citation(tmp_path, "o/r", "abc", "pkg/m.py", 2, 2, h)
    assert c.url.endswith("#L2")


def test_missing_hash_is_rejected(tmp_path):
    _snapshot(tmp_path)
    with pytest.raises(CitationError, match="no stored file hash"):
        validate_citation(tmp_path, "o/r", "abc", "pkg/m.py", 1, 1, None)


def test_changed_file_is_rejected(tmp_path):
    f, h = _snapshot(tmp_path)
    f.write_text("changed\n")
    with pytest.raises(CitationError, match="hash mismatch"):
        validate_citation(tmp_path, "o/r", "abc", "pkg/m.py", 1, 1, h)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(CitationError, match="missing"):
        validate_citation(tmp_path, "o/r", "abc", "pkg/gone.py", 1, 1, "deadbeef")


def test_path_escape_is_rejected(tmp_path):
    root = tmp_path / "snap"
    root.mkdir()
    (tmp_path / "secret.py").write_text("x\n")
    with pytest.raises(CitationError, match="escapes snapshot"):
        validate_citation(root, "o/r", "abc", "../secret.py", 1, 1, "x")


@pytest.mark.parametrize("ls,le", [(0, 1), (3, 2), (1, 99)])
def test_bad_ranges_are_rejected(tmp_path, ls, le):
    _, h = _snapshot(tmp_path)
    with pytest.raises(CitationError, match="outside file"):
        validate_citation(tmp_path, "o/r", "abc", "pkg/m.py", ls, le, h)


def _pack(**kw):
    base = dict(repo="o/r", sha="a" * 40, issue_number=7,
                issue_url="https://github.com/o/r/issues/7", generated_at=0.0,
                status="evidence assembled",
                issue={"state": "open", "observed_at": 0})
    base.update(kw)
    return Packet(**base)


def test_steps_always_end_with_repro_check_at_pinned_sha():
    steps = _steps(_pack())
    assert steps == [f"Verify the bug reproduces at the pinned commit {'a' * 12} "
                     "before changing code."]


def test_steps_for_unverified_state_come_first():
    steps = _steps(_pack(status="state unverified"))
    assert steps[0].startswith("Re-run when the issue state")


def test_steps_name_top_candidate_and_test():
    p = _pack(candidates=[{"qualname": "m.f"}],
              tests={"m.f": [{"test": "tests/test_m.py::test_f"}]},
              constraint_files=["CONTRIBUTING.md"])
    text = "\n".join(_steps(p))
    assert "`m.f`" in text and "CONTRIBUTING.md" in text
    assert "tests/test_m.py::test_f" in text and "not executed" in text


def test_markdown_escapes_html_in_dynamic_text():
    p = _pack(candidates=[{"qualname": "<script>x</script>", "kind": "function",
                           "reason": "a & b"}],
              unknowns=["<img src=x>"])
    md = render_markdown(p)
    assert "<script>" not in md and "<img" not in md
    assert "&lt;script&gt;" in md and "a &amp; b" in md


def test_markdown_never_claims_ready_and_lists_citations():
    c = Citation("pkg/m.py", 1, 2, "h" * 16, "https://github.com/o/r/blob/x/pkg/m.py#L1-L2")
    md = render_markdown(_pack(citations=[c]))
    assert "ready to claim" not in md.lower()
    assert "`pkg/m.py:1-2`" in md and "Read-only analysis" in md


def test_markdown_flags_unverified_competing_work_by_default():
    assert "Competing-PR search: unverified" in render_markdown(_pack())
