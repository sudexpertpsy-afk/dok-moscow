"""TLS-контекст для исходящих HTTPS (W-45/G-08).

Т-Банк и ряд российских сайтов отдают цепочку с
Russian Trusted Root CA (Минцифры). Её нет в Mozilla/certifi —
без доп. корня httpx падает с CERTIFICATE_VERIFY_FAILED /
«self-signed certificate in certificate chain».

Добавляем официальные корни точечно; глобальное отключение проверки запрещено.
"""

from __future__ import annotations

import ssl
from functools import lru_cache
from pathlib import Path

CERTS_DIR = Path(__file__).resolve().parent / "certs"
RUSSIAN_BUNDLE = CERTS_DIR / "russian_trusted_ca_bundle.pem"


@lru_cache(maxsize=1)
def ssl_verify_context() -> ssl.SSLContext | bool:
    """SSLContext: certifi + Russian Trusted CA. Fallback: True (системные CA)."""
    try:
        import certifi
    except ImportError:
        return True

    ctx = ssl.create_default_context(cafile=certifi.where())
    if RUSSIAN_BUNDLE.is_file():
        ctx.load_verify_locations(cafile=str(RUSSIAN_BUNDLE))
    else:
        for name in (
            "russian_trusted_root_ca.pem",
            "russian_trusted_sub_ca.pem",
            "russian_trusted_sub_ca_ssl.pem",
        ):
            path = CERTS_DIR / name
            if path.is_file():
                ctx.load_verify_locations(cafile=str(path))
    return ctx
