"""Bounded, path-safe acquisition of public GitHub repo snapshots.

Fetches a tarball of a repo at an exact commit SHA over HTTPS from the fixed
GitHub hosts only. Enforces compressed/expanded byte caps, file-count caps,
and rejects absolute paths, parent traversal, links and non-regular files.
Never executes, installs or imports anything from the archive.
"""
from __future__ import annotations

import hashlib
import io
import re
import tarfile
from dataclasses import dataclass, field

import httpx

# Admission limits (planning doc sec.6; tune on measured hardware, never
# silently truncate and call it complete).
MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_FILES = 20_000
MAX_PY_FILES = 2_000
MAX_FILE_BYTES = 2 * 1024 * 1024
API_BASE = "https://api.github.com"
CODLOAD_BASE = "https://codeload.github.com"
UA = "patchpath/0.1 (+https://github.com/graph-hacks-patchpath)"


class AcquireError(Exception):
    """Expected, user-reportable acquisition failure."""


@dataclass
class SnapshotFile:
    path: str            # normalized relative path
    content: bytes
    content_hash: str


@dataclass
class Snapshot:
    repo: str            # "owner/name"
    sha: str             # exact commit
    files: list[SnapshotFile] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    @property
    def snapshot_id(self) -> str:
        h = hashlib.sha256(f"{self.repo}@{self.sha}".encode()).hexdigest()
        return h[:16]


def _client() -> httpx.Client:
    # Fixed egress: GitHub API + codeload only, redirects re-validated.
    return httpx.Client(
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"},
        timeout=httpx.Timeout(30.0, read=120.0),
        follow_redirects=False,
    )


def _check_redirect_host(url: str) -> None:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    allowed = {"api.github.com", "codeload.github.com", "github.com",
               "objects.githubusercontent.com"}
    if host not in allowed and not host.endswith(".githubusercontent.com"):
        raise AcquireError(f"redirect to unexpected host: {host}")


def resolve_sha(repo: str, ref: str | None = None) -> tuple[str, str]:
    """Resolve repo + optional ref to an exact commit SHA via git protocol.

    Uses `git ls-remote` (no GitHub API quota). Returns (sha, default_branch).
    """
    import subprocess
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
            part in (".", "..") for part in repo.split("/")):
        raise AcquireError("repo must be owner/name on public GitHub")
    env = {"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    url = f"https://github.com/{repo}.git"

    def ls(*patterns: str) -> list[str]:
        r = subprocess.run(["git", "ls-remote", "--symref", url, *patterns],
                           capture_output=True, text=True, timeout=60, env=env)
        if r.returncode != 0:
            raise AcquireError(f"repository not found or not public: {repo}")
        return [l for l in r.stdout.splitlines() if l.strip()]

    default_branch = "main"
    sha = None
    if ref is None:
        lines = ls("HEAD")
        for l in lines:
            if l.startswith("ref: refs/heads/"):
                default_branch = l.split("refs/heads/")[1].split("\t")[0]
            elif l.endswith("\tHEAD"):
                sha = l.split("\t")[0]
        if not sha:
            raise AcquireError("could not resolve HEAD")
        return sha, default_branch

    lines = ls(ref, f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}")
    peeled = None
    for l in lines:
        if l.startswith("ref:"):
            continue
        sha_part, name = l.split("\t")
        if name.endswith("^{}"):
            peeled = sha_part
        elif name in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}"):
            sha = sha_part
    sha = peeled or sha
    if not sha or not all(c in "0123456789abcdef" for c in sha.lower()) or len(sha) != 40:
        # maybe the ref IS a sha already; verify it exists in the repo
        if ref and len(ref) >= 7 and all(c in "0123456789abcdef" for c in ref.lower()):
            if len(ref) == 40:
                return ref.lower(), default_branch
            with _client() as client:
                try:
                    r = client.get(f"{API_BASE}/repos/{repo}/commits/{ref}")
                    r.raise_for_status()
                    resolved = r.json()["sha"]
                except (httpx.HTTPError, ValueError, KeyError) as e:
                    raise AcquireError(f"could not resolve abbreviated SHA: {ref}") from e
            if not re.fullmatch(r"[0-9a-f]{40}", resolved):
                raise AcquireError("GitHub did not return a full commit SHA")
            return resolved, default_branch
        raise AcquireError(f"ref not found: {ref}")
    return sha, default_branch


def fetch_snapshot(repo: str, sha: str) -> Snapshot:
    """Download and safely unpack the repo tarball at an exact SHA."""
    url = f"{CODLOAD_BASE}/{repo}/tar.gz/{sha}"
    with _client() as c:
        r = c.get(url)
        hops = 0
        while r.is_redirect:
            hops += 1
            if hops > 3:
                raise AcquireError("too many redirects")
            loc = r.headers["location"]
            _check_redirect_host(loc)
            r = c.get(loc)
        if r.status_code == 404:
            raise AcquireError(f"snapshot not found: {repo}@{sha}")
        r.raise_for_status()
        raw = r.content
    if len(raw) > MAX_COMPRESSED_BYTES:
        raise AcquireError(
            f"archive too large ({len(raw)} bytes > {MAX_COMPRESSED_BYTES}); "
            "narrow the repo or raise the cap deliberately")
    return _unpack(repo, sha, raw)


def _safe_member(name: str) -> str | None:
    """Normalize a tar member path; None means reject."""
    # Strip the leading "<repo>-<sha>/" root folder GitHub adds.
    if name.startswith("/") or "\\" in name:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if len(parts) < 2:
        return None
    parts = parts[1:]
    if any(p == ".." for p in parts):
        return None
    norm = "/".join(parts)
    if norm.startswith("/") or "\\" in norm:
        return None
    return norm or None


_SKIP_DIRS = ("vendor/", "third_party/", "node_modules/", ".git/")


def _looks_generated(path: str, head: bytes) -> bool:
    return b"generated by" in head[:512].lower() or path.endswith(".min.py")


def _unpack(repo: str, sha: str, raw: bytes) -> Snapshot:
    snap = Snapshot(repo=repo, sha=sha)
    total = 0
    nfiles = 0
    npy = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf:
            if not m.isfile():
                if m.issym() or m.islnk():
                    snap.skipped.append((m.name, "link"))
                continue
            rel = _safe_member(m.name)
            if rel is None:
                snap.skipped.append((m.name, "unsafe-path"))
                continue
            if any(rel.startswith(d) or f"/{d}" in rel for d in _SKIP_DIRS):
                snap.skipped.append((rel, "vendored"))
                continue
            nfiles += 1
            if nfiles > MAX_FILES:
                raise AcquireError(f"too many files (>{MAX_FILES})")
            if m.size > MAX_FILE_BYTES:
                snap.skipped.append((rel, "file-too-large"))
                continue
            total += m.size
            if total > MAX_EXPANDED_BYTES:
                raise AcquireError(
                    f"expanded content too large (>{MAX_EXPANDED_BYTES} bytes)")
            is_py = rel.endswith(".py")
            if is_py:
                npy += 1
                if npy > MAX_PY_FILES:
                    raise AcquireError(f"too many Python files (>{MAX_PY_FILES})")
            data = tf.extractfile(m).read()  # size-capped above
            if is_py and _looks_generated(rel, data):
                snap.skipped.append((rel, "generated"))
                continue
            snap.files.append(SnapshotFile(
                path=rel, content=data,
                content_hash=hashlib.sha256(data).hexdigest()))
    return snap
