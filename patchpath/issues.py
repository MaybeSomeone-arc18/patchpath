"""Issue + competing-work evidence from the public GitHub API.

Cached with ETag; respects rate limits; on any failure the pack says
"state unverified" and makes no availability claim. Evidence is a batch with
an observed timestamp and a representation hash - it changes faster than
code, so it is pinned separately from the snapshot.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import httpx

API = "https://api.github.com"
UA = "patchpath/0.1"


@dataclass
class IssueEvidence:
    repo: str
    number: int
    observed_at: float
    state: str = "unverified"          # open | closed | unverified
    title: str = ""
    body: str = ""
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    comments: int = 0
    linked_prs: list[dict] = field(default_factory=list)  # number, state, title, url
    competing_work_status: str = "unverified"
    competing_work_error: str | None = None
    competing_work_observed_at: float | None = None
    fetch_error: str | None = None

    @property
    def batch_id(self) -> str:
        h = hashlib.sha256(f"{self.repo}#{self.number}@{self.observed_at}".encode())
        return h.hexdigest()[:12]

    @property
    def rep_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


class IssueClient:
    def __init__(self, cache_dir: Path, token: str | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(headers=headers, timeout=30.0)

    def _cache_path(self, repo: str, number: int) -> Path:
        safe = repo.replace("/", "__")
        return self.cache_dir / f"{safe}__{number}.json"

    def fetch(self, repo: str, number: int, max_age: float = 900.0) -> IssueEvidence:
        """Fetch fresh issue evidence; serve cache if fresh; never invent."""
        cp = self._cache_path(repo, number)
        cached = None
        etag = None
        if cp.exists():
            try:
                saved = json.loads(cp.read_text())
                cached = IssueEvidence(**saved["evidence"])
                etag = saved.get("etag")
                if (cached.competing_work_status == "verified" and
                        time.time() - cached.observed_at < max_age):
                    return cached
            except Exception:
                cached = None

        ev = IssueEvidence(repo=repo, number=number, observed_at=time.time())
        try:
            headers = {"If-None-Match": etag} if etag else {}
            r = self.http.get(f"{API}/repos/{repo}/issues/{number}", headers=headers)
            if r.status_code == 304 and cached:
                cached.observed_at = time.time()
                self._fetch_linked_prs(cached)
                self._save(cp, cached, etag)
                return cached
            if r.status_code == 403 and "rate limit" in r.text.lower():
                ev.fetch_error = "github api rate limit; state unverified"
                return ev
            if r.status_code == 404:
                ev.fetch_error = "issue not found (wrong number, or private repo)"
                return ev
            r.raise_for_status()
            d = r.json()
            if "pull_request" in d:
                ev.fetch_error = f"#{number} is a pull request, not an issue"
                return ev
            ev.state = d.get("state", "unverified")
            ev.title = d.get("title") or ""
            ev.body = (d.get("body") or "")[:20_000]
            ev.labels = [l["name"] for l in d.get("labels", [])][:20]
            ev.assignees = [a["login"] for a in d.get("assignees", [])][:10]
            ev.comments = d.get("comments", 0)
            self._fetch_linked_prs(ev)
            self._save(cp, ev, r.headers.get("ETag"))
            return ev
        except httpx.HTTPError as e:
            ev.fetch_error = f"{e.__class__.__name__}: network failure; state unverified"
            return ev

    def _fetch_linked_prs(self, ev: IssueEvidence) -> None:
        """Open PRs that reference this issue (search API, one call)."""
        ev.linked_prs = []
        ev.competing_work_status = "unverified"
        ev.competing_work_error = None
        ev.competing_work_observed_at = time.time()
        try:
            r = self.http.get(
                f"{API}/search/issues",
                params={"q": f"repo:{ev.repo} is:pr is:open {ev.number} in:body",
                        "per_page": 10})
            r.raise_for_status()
            data = r.json()
            if data.get("incomplete_results"):
                raise ValueError("GitHub search returned incomplete results")
            ev.competing_work_status = "verified"
            for item in data.get("items", []):
                url = item.get("html_url", "")
                if not url.startswith("https://github.com/"):
                    url = f"https://github.com/{ev.repo}/pull/{item['number']}"
                ev.linked_prs.append({
                    "number": item["number"], "state": item["state"],
                    "title": item.get("title", ""), "url": url})
        except (httpx.HTTPError, ValueError, KeyError) as e:
            ev.competing_work_status = "unverified"
            ev.competing_work_error = f"{e.__class__.__name__}: competing-PR search failed"

    def _save(self, cp: Path, ev: IssueEvidence, etag: str | None) -> None:
        cp.write_text(json.dumps({"etag": etag, "evidence": asdict(ev)}))
