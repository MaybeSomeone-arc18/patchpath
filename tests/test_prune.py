from patchpath.cli import select_prune_targets


def test_drops_staging_leftovers():
    staging, superseded, orphans = select_prune_targets(
        ["pp_stg_abc123_a0_7_1_s3"], {})
    assert staging == ["pp_stg_abc123_a0_7_1_s3"]
    assert superseded == [] and orphans == []


def test_drops_superseded_and_failed_keeps_ready():
    names = ["pp_snap_aaa_a0_7_0_s3", "pp_snap_bbb_a0_7_1_s3", "pp_snap_ccc_a0_7_1_s3"]
    registered = {
        "aaa": ("READY", "pp_snap_aaa_a0_7_1_s3"),   # re-ingested: old graph superseded
        "bbb": ("READY", "pp_snap_bbb_a0_7_1_s3"),   # current: keep
        "ccc": ("FAILED", "pp_snap_ccc_a0_7_1_s3"),  # failed: drop
    }
    staging, superseded, orphans = select_prune_targets(names, registered)
    assert superseded == ["pp_snap_aaa_a0_7_0_s3", "pp_snap_ccc_a0_7_1_s3"]
    assert orphans == []


def test_orphans_reported_never_dropped():
    staging, superseded, orphans = select_prune_targets(
        ["pp_snap_unknown_a0_1_0_s1"], {})
    assert orphans == ["pp_snap_unknown_a0_1_0_s1"]
    assert staging == [] and superseded == []
