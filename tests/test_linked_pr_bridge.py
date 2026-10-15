import time

import httpx

from patchpath.issues import IssueClient, IssueEvidence, referenced_pr_numbers
from patchpath.packet import build_packet


def test_same_repo_pr_url_extracted():
    text = "broke in https://github.com/o/r/pull/6644 see also https://github.com/o/r/pull/6644"
    assert referenced_pr_numbers('o/r', text) == [6644]


def test_foreign_repo_and_shorthand_rejected():
    text = ("https://github.com/other/r/pull/5 and #123 and "
            "https://github.com/o/r/issues/9")
    assert referenced_pr_numbers('o/r', text) == []
    assert referenced_pr_numbers('O/R', "https://github.com/o/r/pull/7") == [7]


def test_pr_number_cap():
    text = " ".join(f"https://github.com/o/r/pull/{n}" for n in range(1, 9))
    assert referenced_pr_numbers('o/r', text) == [1, 2, 3, 4, 5]


def _client(tmp_path, handler):
    c = IssueClient(tmp_path)
    c.http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_linked_pr_files_filled_and_cached(tmp_path):
    calls = []

    def handle(req):
        calls.append(req.url.path)
        if req.url.path == '/search/issues':
            return httpx.Response(200, json={'items': []})
        if req.url.path == '/repos/o/r/pulls/2/files':
            return httpx.Response(200, json=[{'filename': 'src/o/a.py'},
                                             {'filename': 'README.md'}])
        return httpx.Response(200, json={'state': 'open', 'title': 't',
                                         'body': 'see https://github.com/o/r/pull/2'})

    c = _client(tmp_path, handle)
    ev = c.fetch('o/r', 1)
    assert ev.linked_pr_files == ['src/o/a.py', 'README.md']
    pr_calls = calls.count('/repos/o/r/pulls/2/files')
    assert pr_calls == 1
    c2 = IssueClient(tmp_path)  # fresh client, disk cache present
    assert c2.fetch_pr_files('o/r', 2) == ['src/o/a.py', 'README.md']


def test_pr_files_failure_leaves_empty(tmp_path):
    def handle(req):
        if req.url.path == '/search/issues':
            return httpx.Response(200, json={'items': []})
        if '/pulls/' in req.url.path:
            return httpx.Response(404, json={'message': 'gone'})
        return httpx.Response(200, json={'state': 'open', 'title': 't',
                                         'body': 'see https://github.com/o/r/pull/2'})

    ev = _client(tmp_path, handle).fetch('o/r', 1)
    assert ev.state == 'open'
    assert ev.linked_pr_files == []


def test_packet_forwards_linked_pr_files(tmp_path):
    seen = {}

    class T:
        def stats(self):
            return {'repo': 'o/r', 'sha': 's', 'calls_exact': 0,
                    'calls_name': 0, 'calls_unresolved': 0}

        def find_candidates(self, *args, **kwargs):
            seen.update(kwargs)
            return []

        def constraint_files(self):
            return []

    ev = IssueEvidence('o/r', 1, time.time(), state='closed',
                       linked_pr_files=['src/o/a.py'])
    build_packet(T(), tmp_path, ev)
    assert seen.get('linked_pr_files') == ['src/o/a.py']
