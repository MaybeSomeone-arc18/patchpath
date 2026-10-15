import hashlib
import json
import time
from pathlib import Path

import httpx
import pytest

from patchpath.issues import IssueClient, IssueEvidence
from patchpath.packet import CitationError, validate_citation, build_packet


def test_citation_requires_stored_hash(tmp_path):
    (tmp_path / 'a.py').write_text('def f():\n    pass\n')
    with pytest.raises(CitationError, match='no stored file hash'):
        validate_citation(tmp_path, 'o/r', 'sha', 'a.py', 1, 2)


def test_citation_rejects_tamper(tmp_path):
    original=b'def f():\n    pass\n'
    (tmp_path / 'a.py').write_bytes(original)
    h=hashlib.sha256(original).hexdigest()
    assert validate_citation(tmp_path,'o/r','sha','a.py',1,2,h).excerpt_hash
    (tmp_path / 'a.py').write_text('def f():\n    print(42)\n')
    with pytest.raises(CitationError, match='hash mismatch'):
        validate_citation(tmp_path,'o/r','sha','a.py',1,2,h)


def test_citation_rejects_path_escape(tmp_path):
    with pytest.raises(CitationError,match='escapes'):
        validate_citation(tmp_path,'o/r','sha','../x',1,1,'x')


def client(tmp_path,handler):
    c=IssueClient(tmp_path)
    c.http=httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_prs_survive_cache(tmp_path):
    calls=[]
    def handle(req):
        calls.append(req.url.path)
        if req.url.path == '/search/issues':
            return httpx.Response(200,json={'items':[{'number':2,'state':'open','title':'fix','html_url':'https://github.com/o/r/pull/2'}]})
        return httpx.Response(200,headers={'ETag':'v1'},json={'state':'open','title':'f','body':'f'})
    c=client(tmp_path,handle)
    first=c.fetch('o/r',1)
    assert first.linked_prs[0]['number']==2
    second=c.fetch('o/r',1)
    assert second.linked_prs==first.linked_prs
    assert second.competing_work_status=='verified'
    assert len(calls)==2


def test_pr_failure_is_not_no_work(tmp_path):
    def handle(req):
        if req.url.path=='/search/issues': return httpx.Response(403,json={'message':'limit'})
        return httpx.Response(200,json={'state':'open','title':'f'})
    ev=client(tmp_path,handle).fetch('o/r',1)
    assert ev.state=='open'
    assert ev.competing_work_status=='unverified'
    assert ev.competing_work_error


def test_304_refreshes_prs(tmp_path):
    c=client(tmp_path,lambda req: httpx.Response(304) if req.url.path!='/search/issues' else httpx.Response(200,json={'items':[]}))
    old=IssueEvidence('o/r',1,time.time()-10000,state='open',linked_prs=[{'number':2}],competing_work_status='verified')
    c._save(c._cache_path('o/r',1),old,'v1')
    ev=c.fetch('o/r',1)
    assert ev.linked_prs==[]
    assert ev.competing_work_status=='verified'


def test_incomplete_pr_search_unverified(tmp_path):
    c=client(tmp_path,lambda req: httpx.Response(200,json={'incomplete_results':True,'items':[]}) if req.url.path=='/search/issues' else httpx.Response(200,json={'state':'open'}))
    assert c.fetch('o/r',1).competing_work_status=='unverified'


def test_empty_candidates_preserve_unverified(tmp_path):
    class T:
        def stats(self): return {'repo':'o/r','sha':'s','calls_exact':0,'calls_name':0,'calls_unresolved':0}
        def find_candidates(self,*args,**kwargs): return []
        def constraint_files(self): return []
    pack=build_packet(T(),tmp_path,IssueEvidence('o/r',1,time.time()))
    assert pack.status=='state unverified'
    assert any('Competing work unverified' in x for x in pack.unknowns)


def test_absolute_tar_path_rejected():
    from patchpath.acquire import _safe_member
    assert _safe_member('/root/app.py') is None
    assert _safe_member('root/app.py') == 'app.py'


def test_short_sha_is_resolved(monkeypatch):
    from patchpath.acquire import resolve_sha
    import patchpath.acquire as ac
    monkeypatch.setattr('subprocess.run',lambda *a,**k: type('R',(),{'returncode':0,'stdout':''})())
    def handler(req): return httpx.Response(200,json={'sha':'a'*40})
    monkeypatch.setattr(ac,'_client',lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert resolve_sha('o/r','aaaaaaa')[0]=='a'*40


def test_invalid_repo_rejected():
    from patchpath.acquire import resolve_sha, AcquireError
    with pytest.raises(AcquireError,match='owner/name'):
        resolve_sha('../escape')
