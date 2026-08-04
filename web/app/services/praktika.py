"""W-44: раздел «Практика» — статьи из markdown в web/app/content/praktika/."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from markupsafe import Markup

from app.services.cms import safe_markdown

_CONTENT_DIR = Path(__file__).resolve().parents[1] / "content" / "praktika"


@dataclass(frozen=True)
class PraktikaArticle:
    slug: str
    title: str
    description: str
    date: str
    body_html: Markup
    related_obraztsy: tuple[str, ...]
    related_zakon: tuple[str, ...]


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta_block = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, str] = {}
    for line in meta_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, body


def _split_csv(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in (raw or "").replace(";", ",").split(",")]
    return tuple(p for p in parts if p)


@lru_cache(maxsize=1)
def list_praktika_articles() -> tuple[PraktikaArticle, ...]:
    if not _CONTENT_DIR.is_dir():
        return ()
    items: list[PraktikaArticle] = []
    for path in sorted(_CONTENT_DIR.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title") or path.stem.replace("-", " ")
        items.append(
            PraktikaArticle(
                slug=path.stem,
                title=title,
                description=meta.get("description") or title,
                date=meta.get("date") or "",
                body_html=safe_markdown(body),
                related_obraztsy=_split_csv(meta.get("obraztsy") or ""),
                related_zakon=_split_csv(meta.get("zakon") or ""),
            )
        )
    # новые сверху, если есть дата
    items.sort(key=lambda a: a.date or "", reverse=True)
    return tuple(items)


def get_praktika_article(slug: str) -> PraktikaArticle | None:
    slug_n = (slug or "").strip()
    if not slug_n or "/" in slug_n or ".." in slug_n:
        return None
    for art in list_praktika_articles():
        if art.slug == slug_n:
            return art
    return None


def invalidate_praktika_cache() -> None:
    list_praktika_articles.cache_clear()


def all_praktika_paths() -> list[str]:
    paths = ["/praktika"]
    for art in list_praktika_articles():
        paths.append(f"/praktika/{art.slug}")
    return paths
