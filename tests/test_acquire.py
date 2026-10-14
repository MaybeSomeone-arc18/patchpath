"""Acquisition safety: hostile archives must be rejected or neutralized."""
import io
import tarfile

import pytest

from patchpath.acquire import _unpack


def _tar(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(f"repo-sha123/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_traversal_rejected():
    raw = _tar([("../evil.py", b"x = 1"), ("ok.py", b"y = 2")])
    snap = _unpack("o/r", "sha", raw)
    assert [f.path for f in snap.files] == ["ok.py"]
    assert ("repo-sha123/../evil.py", "unsafe-path") in snap.skipped


def test_vendored_skipped():
    raw = _tar([("vendor/lib.py", b"x=1"), ("src/app.py", b"y=2")])
    snap = _unpack("o/r", "sha", raw)
    assert [f.path for f in snap.files] == ["src/app.py"]


def test_expanded_cap():
    big = b"x" * (1024 * 1024)
    raw = _tar([(f"f{i}.py", big) for i in range(60)])  # 60MB > 50MB cap
    with pytest.raises(Exception, match="too large"):
        _unpack("o/r", "sha", raw)


def test_symlink_never_followed():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("repo-sha123/link.py")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
        info2 = tarfile.TarInfo("repo-sha123/real.py")
        data = b"z = 1"
        info2.size = len(data)
        tf.addfile(info2, io.BytesIO(data))
    snap = _unpack("o/r", "sha", buf.getvalue())
    assert [f.path for f in snap.files] == ["real.py"]
    assert any(reason == "link" for _, reason in snap.skipped)
