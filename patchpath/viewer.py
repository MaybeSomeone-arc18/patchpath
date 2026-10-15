"""Local packet viewer. Loopback only, random per-start token, autoescaped
templates, fixed github.com links. Single operator, not multi-tenant.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import DictLoader, Environment

from .cli import DATA
from .registry import Registry

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>PatchPath</title><style>
body{font-family:ui-monospace,monospace;max-width:60em;margin:2em auto;padding:0 1em;color:#1a1a1a}
h1{font-size:1.4em} h2{font-size:1.05em;margin-top:1.6em;border-bottom:1px solid #ccc}
table{border-collapse:collapse} td,th{border:1px solid #ddd;padding:.3em .6em;text-align:left;font-size:.9em}
code{background:#f4f4f4;padding:.1em .3em}
.status{font-weight:bold} .warn{color:#8a5a00} .ok{color:#1a6b1a}
li{margin:.25em 0} input{padding:.4em;margin:.2em} button{padding:.4em 1em}
.tag{font-size:.75em;border:1px solid #999;padding:0 .3em;border-radius:3px}
</style></head><body>
<h1>PatchPath - readiness evidence</h1>
{% if not authed %}
<form method="post" action="/auth"><input name="token" placeholder="access token" autofocus>
<button>enter</button></form>
{% else %}
<h2>New pack</h2>
<form method="post" action="/pack">
<input type="hidden" name="csrf" value="{{ csrf }}">
<input name="repo" placeholder="owner/repo" size="24" required>
<input name="issue" placeholder="issue #" size="6" required>
<button>build pack</button></form>
<h2>Ready snapshots</h2>
<table><tr><th>repo</th><th>sha</th><th>symbols</th><th>exact/name/unres</th></tr>
{% for s in snaps %}<tr><td>{{ s.repo }}</td><td><code>{{ s.sha[:12] }}</code></td>
<td>{{ s.stats.symbols }}</td><td>{{ s.stats.calls_exact }}/{{ s.stats.calls_name }}/{{ s.stats.calls_unresolved }}</td></tr>
{% endfor %}</table>
<h2>Packs</h2>
{% for p in packs %}<div><a href="/pack/{{ p }}">{{ p }}</a></div>{% endfor %}
{% endif %}
</body></html>"""

PACK_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>pack {{ p.issue_number }}</title><style>
body{font-family:ui-monospace,monospace;max-width:60em;margin:2em auto;padding:0 1em;color:#1a1a1a}
h1{font-size:1.2em} h2{font-size:1em;margin-top:1.4em;border-bottom:1px solid #ccc}
li{margin:.25em 0} .warn{color:#8a5a00} code{background:#f4f4f4;padding:.1em .3em}
</style></head><body>
<h1>{{ p.repo }}#{{ p.issue_number }} - <span class="warn">{{ p.status }}</span></h1>
<p><a href="{{ p.issue_url }}">issue</a> | pinned <code>{{ p.sha[:12] }}</code> |
observed {{ p.issue.observed_at }}</p>
{% if p.candidates %}<h2>candidates</h2><ul>
{% for c in p.candidates %}<li><code>{{ c.qualname }}</code> ({{ c.kind }}) - {{ c.reason }}</li>{% endfor %}</ul>{% endif %}
{% for q, ts in p.tests.items() %}{% if ts %}<h2>static test candidates: {{ q }}</h2><ul>
{% for t in ts[:8] %}<li><code>{{ t.test }}</code> <b>[{{ t.tag }}]</b>{% if t.via %} via <code>{{ t.via }}</code>{% endif %}</li>
{% endfor %}</ul>{% endif %}{% endfor %}
{% if p.constraint_files %}<h2>contribution rules</h2><ul>
{% for f in p.constraint_files %}<li><code>{{ f }}</code></li>{% endfor %}</ul>{% endif %}
{% if p.linked_prs %}<h2>open PRs referencing this issue</h2><ul>
{% for pr in p.linked_prs %}<li>#{{ pr.number }} {{ pr.title }} ({{ pr.state }})</li>{% endfor %}</ul>{% endif %}
{% if p.unknowns %}<h2>unknowns and limits</h2><ul>
{% for u in p.unknowns %}<li class="warn">{{ u }}</li>{% endfor %}</ul>{% endif %}
{% if p.suggested_steps %}<h2>next investigation</h2><ol>
{% for s in p.suggested_steps %}<li>{{ s }}</li>{% endfor %}</ol>{% endif %}
<h2>citations (validated)</h2><ul>
{% for c in p.citations %}<li><code>{{ c.file }}:{{ c.line_start }}-{{ c.line_end }}</code>
(hash {{ c.excerpt_hash }}) <a href="{{ c.url }}">source</a></li>{% endfor %}</ul>
<p><small>Read-only analysis. No tests executed, no claims made on GitHub,
no patches generated.</small></p>
<p><a href="/">back</a></p></body></html>"""


def make_app(token: str) -> FastAPI:
    env = Environment(loader=DictLoader({"page": PAGE, "pack": PACK_PAGE}),
                      autoescape=True)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    csrf = secrets.token_urlsafe(16)

    def authed(req: Request) -> bool:
        return req.cookies.get("pp_token") == token

    @app.get("/", response_class=HTMLResponse)
    def home(req: Request):
        if not authed(req):
            return env.get_template("page").render(authed=False)
        reg = Registry(DATA / "registry.db")
        rows = reg.conn.execute(
            "SELECT repo, sha, stats_json FROM snapshots WHERE status='READY'").fetchall()
        snaps = [{"repo": r[0], "sha": r[1], "stats": json.loads(r[2] or "{}")}
                 for r in rows]
        pdir = DATA / "packets"
        packs = sorted(p.stem for p in pdir.glob("pack_*.json")) if pdir.exists() else []
        return env.get_template("page").render(authed=True, csrf=csrf,
                                               snaps=snaps, packs=packs)

    @app.post("/auth")
    def auth(token_in: str = Form(..., alias="token")):
        resp = RedirectResponse("/", status_code=303)
        if secrets.compare_digest(token_in, token):
            resp.set_cookie("pp_token", token, httponly=True, samesite="strict")
        return resp

    @app.post("/pack")
    def build(req: Request, repo: str = Form(...), issue: int = Form(...),
              csrf_in: str = Form(..., alias="csrf")):
        if not authed(req) or not secrets.compare_digest(csrf_in, csrf):
            raise HTTPException(403)
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "patchpath.cli", "pack", repo, str(issue)],
            capture_output=True, text=True, timeout=300,
            cwd=str(Path(__file__).resolve().parent.parent))
        if r.returncode != 0:
            return JSONResponse({"error": r.stderr[-2000:]}, status_code=500)
        return RedirectResponse(f"/pack/pack_{repo.replace('/', '__')}__{issue}",
                                status_code=303)

    @app.get("/pack/{name}", response_class=HTMLResponse)
    def show(req: Request, name: str):
        if not authed(req):
            raise HTTPException(403)
        if "/" in name or ".." in name:
            raise HTTPException(400)
        fp = DATA / "packets" / f"{name}.json"
        if not fp.exists():
            raise HTTPException(404)
        return env.get_template("pack").render(p=json.loads(fp.read_text()))

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8471)
    args = ap.parse_args()
    token = secrets.token_urlsafe(18)
    print(f"PatchPath viewer on http://127.0.0.1:{args.port}")
    print(f"access token (this run only): {token}")
    import uvicorn
    uvicorn.run(make_app(token), host="127.0.0.1", port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
