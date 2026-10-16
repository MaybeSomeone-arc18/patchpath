"""FalkorDB connection target.

Defaults to the documented local setup: a FalkorDB container publishing its
port 6379 on loopback port 6380. Containers and remote graph servers point
elsewhere with PATCHPATH_FALKOR_HOST and PATCHPATH_FALKOR_PORT.
"""
from __future__ import annotations

import os


def falkor_address() -> tuple[str, int]:
    host = os.environ.get("PATCHPATH_FALKOR_HOST", "127.0.0.1")
    port = int(os.environ.get("PATCHPATH_FALKOR_PORT", "6380"))
    return host, port
