"""Unit tests for the pure ranking helpers behind candidate ordering."""
from patchpath.tools_query import _issue_tokens, _name_parts, _code_like


def test_issue_tokens_lowercases_and_strips_plural():
    toks = _issue_tokens("Presigned URLs for S3 keys")
    assert "url" in toks          # naive singular of 'urls'
    assert "urls" in toks         # original form kept too
    assert "key" in toks
    assert "s3" not in toks       # letters-only by design; 's3' carries a digit


def test_issue_tokens_drops_stopwords_and_short_words():
    toks = _issue_tokens("This version fails when it is used")
    assert "version" not in toks  # plain-English stopword
    assert "fails" not in toks
    assert "is" not in toks       # below the 3-letter minimum
    assert not toks & {"the", "and", "with"}


def test_name_parts_splits_snake_case():
    assert _name_parts("request_url") == {"request", "url"}
    assert _name_parts("__init__") == {"init"}
    assert _name_parts("content") == {"content"}


def test_code_like_gate_for_variable_tier():
    # the variable tier ignores bare lowercase dictionary words
    assert _code_like("_SIMPLE_VERSION_INDICATORS")
    assert _code_like("CONTENT_CHUNK_SIZE")
    assert _code_like("ConnectionError")
    assert _code_like("requests.get")
    assert not _code_like("packages")
    assert not _code_like("project")
