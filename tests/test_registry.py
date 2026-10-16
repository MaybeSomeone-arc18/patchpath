import time

from patchpath.registry import Registry


def _reg(tmp_path):
    return Registry(tmp_path / "reg.sqlite")


def test_empty_registry_has_no_ready_snapshot(tmp_path):
    r = _reg(tmp_path)
    assert r.latest_ready() is None
    assert r.get_ready("nope") is None


def test_building_snapshot_is_not_readable(tmp_path):
    r = _reg(tmp_path)
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    assert r.get_ready("s1") is None
    assert r.latest_ready() is None


def test_ready_roundtrip_returns_stats(tmp_path):
    r = _reg(tmp_path)
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    r.ready("s1", {"edges": 5})
    assert r.get_ready("s1") == {"repo": "o/r", "sha": "abc", "graph": "g1",
                                 "stats": {"edges": 5}}


def test_failed_snapshot_is_not_ready_and_keeps_error(tmp_path):
    r = _reg(tmp_path)
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    r.fail("s1", "boom")
    assert r.get_ready("s1") is None
    row = r.conn.execute("SELECT status, error FROM snapshots").fetchone()
    assert row == ("FAILED", "boom")


def test_ready_clears_earlier_error(tmp_path):
    r = _reg(tmp_path)
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    r.fail("s1", "boom")
    r.ready("s1", {})
    assert r.conn.execute("SELECT error FROM snapshots").fetchone() == (None,)


def test_latest_ready_picks_most_recently_updated(tmp_path):
    r = _reg(tmp_path)
    r.begin("old", "o/r", "a1", "0.7", "3", "g_old")
    r.ready("old", {})
    time.sleep(0.01)
    r.begin("new", "o/r", "b2", "0.7", "3", "g_new")
    r.ready("new", {})
    assert r.latest_ready()["graph"] == "g_new"


def test_begin_again_resets_a_ready_snapshot_to_building(tmp_path):
    r = _reg(tmp_path)
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    r.ready("s1", {})
    r.begin("s1", "o/r", "abc", "0.7", "3", "g1")
    assert r.get_ready("s1") is None


def test_empty_path_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        Registry("")
