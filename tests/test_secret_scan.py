from openlocal.sandbox.secret_scan import REDACTION, scan


def test_redacts_aws_key():
    r = scan("key AKIAIOSFODNN7EXAMPLE here")
    assert "AKIA" not in r.redacted_text
    assert REDACTION in r.redacted_text
    assert r.had_secrets


def test_redacts_groq_and_openai():
    r = scan("gsk_abcdefghijklmnopqrstuvwx and sk-abcdefghijklmnopqrstuvwx")
    assert "gsk_abcdefghijklmnop" not in r.redacted_text
    assert "sk-abcdefghijklmnop" not in r.redacted_text


def test_redacts_env_assignment():
    r = scan("API_SECRET_KEY=supersecretvalue123456")
    assert "supersecretvalue123456" not in r.redacted_text
    assert any(f.kind == "env_assignment" for f in r.findings)


def test_redacts_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nAAAA\nBBBB\n-----END RSA PRIVATE KEY-----"
    r = scan(pem)
    assert "AAAA" not in r.redacted_text
    assert any(f.kind == "pem_private_key" for f in r.findings)


def test_high_entropy_token_flagged():
    r = scan("value: b3f9Zk2Qw7Lp0Xr8Nt4Yv6HsAaBbCc")
    assert r.had_secrets


def test_plain_text_untouched():
    text = "the quick brown fox jumps over the lazy dog"
    r = scan(text)
    assert r.redacted_text == text
    assert not r.had_secrets


def test_preview_is_not_reversible():
    r = scan("AKIAIOSFODNN7EXAMPLE")
    for f in r.findings:
        assert REDACTION not in f.preview
        assert len(f.preview) < 12
