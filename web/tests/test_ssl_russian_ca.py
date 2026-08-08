"""W-45/G-08: Russian Trusted CA в контексте verify."""

from __future__ import annotations

import ssl

from app.ssl_util import RUSSIAN_BUNDLE, ssl_verify_context


def test_russian_ca_bundle_present():
    assert RUSSIAN_BUNDLE.is_file()
    text = RUSSIAN_BUNDLE.read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in text
    assert text.count("BEGIN CERTIFICATE") >= 2


def test_ssl_verify_context_loads_russian_ca():
    ctx = ssl_verify_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
