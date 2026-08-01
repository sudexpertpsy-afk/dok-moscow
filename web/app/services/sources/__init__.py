"""Загрузчики официальных источников НПА (W-17)."""

from app.services.sources.ips_loader import load_ips_document, normalize_ips_html
from app.services.sources.publication_api import PublicationClient

__all__ = [
    "PublicationClient",
    "load_ips_document",
    "normalize_ips_html",
]
