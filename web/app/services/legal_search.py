"""Поисковый движок нормативных актов (W-22).

Индекс: legal_search_docs (статья/фрагмент), tsvector russian + pg_trgm.
Публичный API: search_legal(query, filters, limit).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from pathlib import Path

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ActFragment,
    ActVersion,
    ActVersionStatus,
    LegalAct,
    LegalActCategory,
    LegalActMode,
    LegalActStatus,
    LegalSearchDoc,
    utcnow,
)

# Короткие обозначения кодексов → slug в реестре
CODE_SLUGS: dict[str, str] = {
    "упк": "upk-ekspertiza",
    "упк рф": "upk-ekspertiza",
    "гпк": "gpk-ekspertiza",
    "гпк рф": "gpk-ekspertiza",
    "апк": "apk-ekspertiza",
    "апк рф": "apk-ekspertiza",
    "кас": "kas-ekspertiza",
    "каас": "kas-ekspertiza",
    "кас рф": "kas-ekspertiza",
    "коап": "koap-ekspertiza",
    "коап рф": "koap-ekspertiza",
    "ук": "uk-otvetstvennost-eksperta",
    "ук рф": "uk-otvetstvennost-eksperta",
}

REQUISITE_ARTICLE_RE = re.compile(
    r"(?is)^\s*(?:ст\.?|статья)\s*(?P<article>\d+(?:\.\d+)?)"
    r"(?:\s+(?P<code>упк|гпк|апк|кас|каас|коап|ук)(?:\s*рф)?)?\s*$"
)
REQUISITE_FZ_RE = re.compile(r"(?is)^\s*(?P<num>\d+)\s*-\s*фз\s*$")
ARTICLE_SPLIT_RE = re.compile(
    r"(?is)(?:^|\n|\r|<h[1-6][^>]*>|<p[^>]*>)\s*(?:статья|ст\.)\s*"
    r"(?P<num>\d+(?:\.\d+)?)[.\s:–—\-]*(?P<title>[^\n<]{0,200})"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


@dataclass
class LegalSearchFilters:
    category: LegalActCategory | str | None = None
    authority: str | None = None
    status: LegalActStatus | str | None = None  # active / repealed
    revision_from: date | None = None
    revision_to: date | None = None
    include_repealed: bool = False


@dataclass
class LegalSearchHit:
    act: LegalAct
    article_ref: str
    heading: str
    snippet: str
    rank: float
    version_id: int | None = None
    fragment_id: int | None = None
    doc_id: int | None = None
    direct: bool = False

    @property
    def url(self) -> str:
        base = f"/zakon/{self.act.slug}"
        if self.article_ref:
            return f"{base}#{article_anchor(self.article_ref)}"
        return base


@dataclass
class LegalSearchResult:
    hits: list[LegalSearchHit] = field(default_factory=list)
    total: int = 0
    groups: list[tuple[LegalAct, list[LegalSearchHit]]] = field(default_factory=list)
    direct: LegalSearchHit | None = None
    query_time_ms: float = 0.0
    used_trgm: bool = False
    explain: str | None = None


def article_anchor(article_ref: str) -> str:
    slug = re.sub(r"[^\wа-яё]+", "-", (article_ref or "").casefold(), flags=re.I)
    slug = slug.strip("-") or "art"
    return f"art-{slug}"


def html_to_text(html: str) -> str:
    text_val = HTML_TAG_RE.sub(" ", html or "")
    text_val = unescape(text_val)
    return WS_RE.sub(" ", text_val).strip()


def parse_requisite_query(query: str) -> dict | None:
    """Распознать «ст. 195 УПК», «73-ФЗ» и т.п."""
    q = (query or "").strip()
    if not q:
        return None
    m = REQUISITE_ARTICLE_RE.match(q)
    if m:
        code = (m.group("code") or "").casefold().replace("каас", "кас")
        return {
            "kind": "article",
            "article": m.group("article"),
            "code": code or None,
            "slug": CODE_SLUGS.get(code) if code else None,
        }
    m = REQUISITE_FZ_RE.match(q)
    if m:
        return {"kind": "fz", "number": f"{m.group('num')}-ФЗ"}
    return None


def split_html_articles(body_html: str) -> list[tuple[str, str, str]]:
    """Разбить полный текст на статьи → (article_ref, heading, body_text)."""
    plain_source = body_html or ""
    matches = list(ARTICLE_SPLIT_RE.finditer(plain_source))
    if not matches:
        text_val = html_to_text(plain_source)
        if text_val:
            return [("", "", text_val)]
        return []

    parts: list[tuple[str, str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plain_source)
        chunk_html = plain_source[start:end]
        num = m.group("num")
        title = WS_RE.sub(" ", (m.group("title") or "").strip(" .:—–-"))
        ref = f"ст. {num}"
        heading = f"{ref}" + (f". {title}" if title else "")
        parts.append((ref, heading, html_to_text(chunk_html)))
    # преамбула до первой статьи
    preamble = html_to_text(plain_source[: matches[0].start()])
    if preamble and len(preamble) > 40:
        parts.insert(0, ("", "Преамбула", preamble))
    return parts


def build_requisites(act: LegalAct, article_ref: str) -> str:
    bits = [act.number or "", act.act_kind or "", act.title or ""]
    if article_ref:
        bits.append(article_ref)
        # «ст. 79 ГПК»
        short = (act.number or "").replace(" РФ", "").strip()
        if short:
            bits.append(f"{article_ref} {short}")
            bits.append(f"{article_ref} {short.split()[0]}")
    return " · ".join(b for b in bits if b)


def rebuild_act_index(db: Session, act_id: int) -> int:
    """Пересобрать поисковые строки акта по опубликованной редакции."""
    act = db.get(LegalAct, act_id)
    if act is None:
        return 0

    db.execute(delete(LegalSearchDoc).where(LegalSearchDoc.act_id == act_id))
    db.flush()

    version = db.scalar(
        select(ActVersion).where(
            ActVersion.act_id == act_id,
            ActVersion.status == ActVersionStatus.published,
        )
    )
    if version is None:
        db.flush()
        return 0

    # Неиндексируемый скан PDF
    if version.text_origin == "pdf_unrecognized":
        db.flush()
        return 0

    rows: list[LegalSearchDoc] = []

    if act.mode == LegalActMode.fragments:
        frags = db.scalars(
            select(ActFragment)
            .where(ActFragment.act_id == act_id)
            .order_by(ActFragment.sort_order, ActFragment.id)
        ).all()
        for frag in frags:
            body = html_to_text(frag.body_html)
            # пустой stub не индексируем (нет полнотекста), кроме реквизитов заголовка
            heading = f"{act.title}. {frag.title or frag.article_ref}".strip(". ")
            if not body and not frag.article_ref:
                continue
            # даже пустой body индексируем по заголовку/реквизитам статьи
            rows.append(
                LegalSearchDoc(
                    act_id=act.id,
                    version_id=version.id,
                    fragment_id=frag.id,
                    article_ref=frag.article_ref or "",
                    heading=heading,
                    body_text=body or frag.title or frag.article_ref,
                    requisites=build_requisites(act, frag.article_ref or ""),
                    indexed_at=utcnow(),
                )
            )
        # если у fragments пустые тела — дополнительно индексируем полный body редакции
        if version.body_html and html_to_text(version.body_html):
            if not any(html_to_text(f.body_html) for f in frags):
                for ref, heading, body in split_html_articles(version.body_html):
                    rows.append(
                        LegalSearchDoc(
                            act_id=act.id,
                            version_id=version.id,
                            fragment_id=None,
                            article_ref=ref,
                            heading=f"{act.title}. {heading or act.title}".strip(". "),
                            body_text=body,
                            requisites=build_requisites(act, ref),
                            indexed_at=utcnow(),
                        )
                    )
    elif act.mode == LegalActMode.card:
        note = (act.notes or act.title or "").strip()
        if note:
            rows.append(
                LegalSearchDoc(
                    act_id=act.id,
                    version_id=version.id,
                    fragment_id=None,
                    article_ref="",
                    heading=act.title,
                    body_text=note,
                    requisites=build_requisites(act, ""),
                    indexed_at=utcnow(),
                )
            )
    else:
        parts = split_html_articles(version.body_html)
        for ref, heading, body in parts:
            rows.append(
                LegalSearchDoc(
                    act_id=act.id,
                    version_id=version.id,
                    fragment_id=None,
                    article_ref=ref,
                    heading=f"{act.title}. {heading or act.title}".strip(". "),
                    body_text=body,
                    requisites=build_requisites(act, ref),
                    indexed_at=utcnow(),
                )
            )

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        # INSERT без search_vector — его заполняет BEFORE-триггер (тип tsvector)
        for row in rows:
            db.execute(
                text(
                    """
                    INSERT INTO legal_search_docs
                      (act_id, version_id, fragment_id, article_ref, heading, body_text, requisites, indexed_at)
                    VALUES
                      (:act_id, :version_id, :fragment_id, :article_ref, :heading, :body_text, :requisites, :indexed_at)
                    """
                ),
                {
                    "act_id": row.act_id,
                    "version_id": row.version_id,
                    "fragment_id": row.fragment_id,
                    "article_ref": row.article_ref,
                    "heading": row.heading,
                    "body_text": row.body_text,
                    "requisites": row.requisites,
                    "indexed_at": row.indexed_at or utcnow(),
                },
            )
        db.flush()
    else:
        for row in rows:
            row.search_vector = f"{row.heading} {row.body_text}".casefold()
            db.add(row)
        db.flush()

    return len(rows)


def rebuild_all_indexes(db: Session) -> int:
    act_ids = db.scalars(select(LegalAct.id)).all()
    total = 0
    for act_id in act_ids:
        total += rebuild_act_index(db, act_id)
    return total


def extract_text_from_pdf(path: Path | str) -> tuple[str, bool]:
    """Извлечь текст из PDF. Возвращает (text, recognized)."""
    pdf_path = Path(path)
    if not pdf_path.is_file():
        return "", False
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        text_val = WS_RE.sub(" ", "\n".join(chunks)).strip()
    except Exception:
        return "", False

    # эвристика: мало букв → скан без текстового слоя
    letters = sum(1 for ch in text_val if ch.isalpha())
    recognized = letters >= 40
    return (text_val if recognized else ""), recognized


def ingest_pdf_version(
    db: Session,
    *,
    act_id: int,
    pdf_path: str,
    revision_date: date | None = None,
    change_basis: str | None = None,
    loaded_by_user_id: int | None = None,
) -> ActVersion:
    """Создать черновик редакции из PDF: текст → body_html, пометка распознавания."""
    from app.services.legal_registry import create_draft_version

    text_val, ok = extract_text_from_pdf(pdf_path)
    if ok:
        paras = [p.strip() for p in re.split(r"\n{2,}", text_val) if p.strip()]
        if not paras:
            paras = [text_val]
        body = "".join(f"<p>{_escape_html(p)}</p>" for p in paras)
        origin = "pdf_extracted"
        basis = change_basis or "распознано из PDF"
        if "распознано" not in basis.casefold():
            basis = f"{basis} · распознано"
    else:
        body = ""
        origin = "pdf_unrecognized"
        basis = change_basis or "PDF без текстового слоя (нераспознаваемый скан)"

    version = create_draft_version(
        db,
        act_id=act_id,
        body_html=body,
        revision_date=revision_date,
        change_basis=basis,
        loaded_by_user_id=loaded_by_user_id,
        pdf_path=pdf_path,
    )
    version.text_origin = origin
    db.flush()
    return version


def _escape_html(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _normalize_filters(filters: LegalSearchFilters | None) -> LegalSearchFilters:
    return filters or LegalSearchFilters()


def _category_value(raw) -> str | None:
    if raw is None:
        return None
    return raw.value if hasattr(raw, "value") else str(raw)


def _status_value(raw) -> str | None:
    if raw is None:
        return None
    return raw.value if hasattr(raw, "value") else str(raw)


def _direct_requisite_hit(
    db: Session, parsed: dict, filters: LegalSearchFilters
) -> LegalSearchHit | None:
    if parsed["kind"] == "fz":
        act = db.scalar(
            select(LegalAct).where(LegalAct.number.ilike(parsed["number"]))
        )
        if act is None:
            return None
        return LegalSearchHit(
            act=act,
            article_ref="",
            heading=act.title,
            snippet=act.title,
            rank=100.0,
            direct=True,
        )

    article = parsed["article"]
    slug = parsed.get("slug")
    stmt = select(LegalSearchDoc).options(joinedload(LegalSearchDoc.act))
    stmt = stmt.join(LegalAct, LegalAct.id == LegalSearchDoc.act_id)
    stmt = stmt.where(
        or_(
            LegalSearchDoc.article_ref.ilike(f"%ст. {article}%"),
            LegalSearchDoc.article_ref.ilike(f"%статья {article}%"),
            LegalSearchDoc.requisites.ilike(f"%ст. {article}%"),
        )
    )
    if slug:
        stmt = stmt.where(LegalAct.slug == slug)
    elif parsed.get("code"):
        # по number кодекса
        code = parsed["code"].upper()
        stmt = stmt.where(LegalAct.number.ilike(f"%{code}%"))
    stmt = _apply_act_filters(stmt, filters)
    stmt = stmt.limit(5)
    doc = db.scalars(stmt).first()
    if doc is None and slug:
        # фрагмент-заглушка без индекса — прямая ссылка на акт+статью
        act = db.scalar(select(LegalAct).where(LegalAct.slug == slug))
        if act is None:
            return None
        return LegalSearchHit(
            act=act,
            article_ref=f"ст. {article}",
            heading=f"ст. {article}",
            snippet=f"ст. {article} · {act.title}",
            rank=100.0,
            direct=True,
        )
    if doc is None:
        return None
    return LegalSearchHit(
        act=doc.act,
        article_ref=doc.article_ref,
        heading=doc.heading,
        snippet=doc.heading or doc.article_ref,
        rank=100.0,
        version_id=doc.version_id,
        fragment_id=doc.fragment_id,
        doc_id=doc.id,
        direct=True,
    )


def _apply_act_filters(stmt, filters: LegalSearchFilters):
    cat = _category_value(filters.category)
    if cat:
        stmt = stmt.where(LegalAct.category == cat)
    if filters.authority:
        stmt = stmt.where(LegalAct.authority.ilike(f"%{filters.authority.strip()}%"))
    status = _status_value(filters.status)
    if status:
        stmt = stmt.where(LegalAct.status == status)
    elif not filters.include_repealed:
        stmt = stmt.where(LegalAct.status == LegalActStatus.active)
    if filters.revision_from is not None or filters.revision_to is not None:
        stmt = stmt.join(ActVersion, ActVersion.id == LegalSearchDoc.version_id)
        stmt = stmt.where(ActVersion.status == ActVersionStatus.published)
        if filters.revision_from is not None:
            stmt = stmt.where(ActVersion.revision_date >= filters.revision_from)
        if filters.revision_to is not None:
            stmt = stmt.where(ActVersion.revision_date <= filters.revision_to)
    return stmt


def search_legal(
    db: Session,
    query: str,
    filters: LegalSearchFilters | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    with_explain: bool = False,
) -> LegalSearchResult:
    """Внутренний поисковый API НПА (zakon / глобальный поиск / шаблоны)."""
    started = time.perf_counter()
    filters = _normalize_filters(filters)
    q = (query or "").strip()
    result = LegalSearchResult()
    if len(q) < 2:
        result.query_time_ms = (time.perf_counter() - started) * 1000
        return result

    parsed = parse_requisite_query(q)
    if parsed is not None:
        direct = _direct_requisite_hit(db, parsed, filters)
        if direct is not None:
            result.direct = direct
            result.hits = [direct]
            result.total = 1
            result.groups = [(direct.act, [direct])]
            result.query_time_ms = (time.perf_counter() - started) * 1000
            return result

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        hits, total, used_trgm, explain = _search_postgres(
            db, q, filters, limit=limit, offset=offset, with_explain=with_explain
        )
    else:
        hits, total, used_trgm, explain = _search_sqlite(
            db, q, filters, limit=limit, offset=offset
        )

    result.hits = hits
    result.total = total
    result.used_trgm = used_trgm
    result.explain = explain
    result.groups = _group_by_act(hits)
    result.query_time_ms = (time.perf_counter() - started) * 1000
    return result


def _group_by_act(hits: list[LegalSearchHit]) -> list[tuple[LegalAct, list[LegalSearchHit]]]:
    order: list[int] = []
    buckets: dict[int, list[LegalSearchHit]] = {}
    acts: dict[int, LegalAct] = {}
    for hit in hits:
        if hit.act.id not in buckets:
            order.append(hit.act.id)
            buckets[hit.act.id] = []
            acts[hit.act.id] = hit.act
        buckets[hit.act.id].append(hit)
    return [(acts[i], buckets[i]) for i in order]


def _search_postgres(
    db: Session,
    q: str,
    filters: LegalSearchFilters,
    *,
    limit: int,
    offset: int,
    with_explain: bool,
) -> tuple[list[LegalSearchHit], int, bool, str | None]:
    # websearch_to_tsquery понимает кавычки и минус-слова
    ts_sql = "websearch_to_tsquery('russian', :q)"
    try:
        db.execute(text(f"SELECT {ts_sql}"), {"q": q}).scalar()
    except Exception:
        ts_sql = "plainto_tsquery('russian', :q)"

    where_extra = ["d.search_vector @@ " + ts_sql]
    params: dict = {"q": q, "limit": limit, "offset": offset}

    cat = _category_value(filters.category)
    if cat:
        where_extra.append("a.category = :category")
        params["category"] = cat
    if filters.authority:
        where_extra.append("a.authority ILIKE :authority")
        params["authority"] = f"%{filters.authority.strip()}%"
    status = _status_value(filters.status)
    if status:
        where_extra.append("a.status = :status")
        params["status"] = status
    elif not filters.include_repealed:
        where_extra.append("a.status = 'active'")

    join_version = ""
    if filters.revision_from is not None or filters.revision_to is not None:
        join_version = "JOIN act_versions v ON v.id = d.version_id AND v.status = 'published'"
        if filters.revision_from is not None:
            where_extra.append("v.revision_date >= :rev_from")
            params["rev_from"] = filters.revision_from.isoformat()
        if filters.revision_to is not None:
            where_extra.append("v.revision_date <= :rev_to")
            params["rev_to"] = filters.revision_to.isoformat()

    fts_where = " AND ".join(where_extra)
    rank_expr = f"ts_rank_cd(d.search_vector, {ts_sql}, 32)"
    headline_expr = (
        f"ts_headline('russian', d.body_text, {ts_sql}, "
        "'StartSel=<mark>, StopSel=</mark>, MaxWords=40, MinWords=12, ShortWord=2')"
    )

    sql = f"""
        SELECT d.id, d.act_id, d.version_id, d.fragment_id, d.article_ref, d.heading,
               {rank_expr} AS rank, {headline_expr} AS snippet
        FROM legal_search_docs d
        JOIN legal_acts a ON a.id = d.act_id
        {join_version}
        WHERE {fts_where}
        ORDER BY rank DESC, a.sort_order ASC, d.id ASC
        LIMIT :limit OFFSET :offset
    """
    count_sql = f"""
        SELECT count(*) FROM legal_search_docs d
        JOIN legal_acts a ON a.id = d.act_id
        {join_version}
        WHERE {fts_where}
    """

    rows = db.execute(text(sql), params).mappings().all()
    total = int(db.execute(text(count_sql), params).scalar() or 0)
    used_trgm = False

    # Если FTS пуст — pg_trgm по реквизитам/заголовку (опечатки, «73-ФЗ»)
    if not rows:
        used_trgm = True
        trgm_where = [
            "(d.requisites % :q OR d.heading % :q OR a.title % :q OR a.number % :q "
            "OR d.requisites ILIKE :like OR d.heading ILIKE :like OR a.number ILIKE :like)"
        ]
        params_t = dict(params)
        params_t["like"] = f"%{q}%"
        # переиспользовать фильтры без fts
        base_filters = [w for w in where_extra if "search_vector" not in w]
        trgm_where = base_filters + trgm_where
        tw = " AND ".join(trgm_where) if trgm_where else "TRUE"
        trgm_sql = f"""
            SELECT d.id, d.act_id, d.version_id, d.fragment_id, d.article_ref, d.heading,
                   greatest(similarity(d.requisites, :q), similarity(d.heading, :q),
                            similarity(a.title, :q), similarity(a.number, :q)) AS rank,
                   left(d.body_text, 240) AS snippet
            FROM legal_search_docs d
            JOIN legal_acts a ON a.id = d.act_id
            {join_version}
            WHERE {tw}
            ORDER BY rank DESC, a.sort_order ASC
            LIMIT :limit OFFSET :offset
        """
        rows = db.execute(text(trgm_sql), params_t).mappings().all()
        total = len(rows)

    explain = None
    if with_explain:
        plan = db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql), params)
        explain = "\n".join(r[0] for r in plan)

    act_ids = {int(r["act_id"]) for r in rows}
    acts = {
        a.id: a
        for a in db.scalars(select(LegalAct).where(LegalAct.id.in_(act_ids))).all()
    } if act_ids else {}

    hits: list[LegalSearchHit] = []
    for r in rows:
        act = acts.get(int(r["act_id"]))
        if act is None:
            continue
        snip = r.get("snippet") or r.get("heading") or ""
        hits.append(
            LegalSearchHit(
                act=act,
                article_ref=r["article_ref"] or "",
                heading=r["heading"] or "",
                snippet=str(snip),
                rank=float(r["rank"] or 0),
                version_id=r["version_id"],
                fragment_id=r["fragment_id"],
                doc_id=int(r["id"]),
            )
        )
    return hits, total, used_trgm, explain


def _search_sqlite(
    db: Session,
    q: str,
    filters: LegalSearchFilters,
    *,
    limit: int,
    offset: int,
) -> tuple[list[LegalSearchHit], int, bool, str | None]:
    """Упрощённый поиск для SQLite-тестов (без морфологии PG)."""
    # кавычки: "точная фраза"
    phrase = None
    m = re.search(r'"([^"]+)"', q)
    if m:
        phrase = m.group(1).strip()
    # минус-слова
    minus = re.findall(r"(?<!\w)-(\w{2,})", q)
    positive = re.sub(r'"[^"]+"', " ", q)
    positive = re.sub(r"(?<!\w)-\w+", " ", positive)
    tokens = [t for t in re.split(r"\s+", positive.strip()) if len(t) >= 2]

    stmt = select(LegalSearchDoc).options(joinedload(LegalSearchDoc.act))
    stmt = stmt.join(LegalAct, LegalAct.id == LegalSearchDoc.act_id)
    stmt = _apply_act_filters(stmt, filters)
    docs = db.scalars(stmt).unique().all()

    scored: list[LegalSearchHit] = []
    for doc in docs:
        blob = f"{doc.heading}\n{doc.body_text}\n{doc.requisites}".casefold()
        if any(m.casefold() in blob for m in minus):
            continue
        rank = 0.0
        if phrase and phrase.casefold() in blob:
            rank += 5.0
        for tok in tokens:
            t = tok.casefold()
            if t in (doc.heading or "").casefold():
                rank += 3.0  # вес A
            elif t in blob:
                rank += 1.0  # вес B
            # грубая «опечатка»: общий префикс
            elif any(t[:4] and t[:4] in part for part in blob.split() if len(part) > 3):
                rank += 0.4
        if rank <= 0 and phrase is None and not tokens:
            continue
        if rank <= 0:
            continue
        snip = _sqlite_snippet(doc.body_text or doc.heading, phrase or (tokens[0] if tokens else q))
        scored.append(
            LegalSearchHit(
                act=doc.act,
                article_ref=doc.article_ref,
                heading=doc.heading,
                snippet=snip,
                rank=rank,
                version_id=doc.version_id,
                fragment_id=doc.fragment_id,
                doc_id=doc.id,
            )
        )
    scored.sort(key=lambda h: (-h.rank, h.act.sort_order, h.act.title))
    total = len(scored)
    return scored[offset : offset + limit], total, False, None


def _sqlite_snippet(text_val: str, needle: str, radius: int = 80) -> str:
    plain = text_val or ""
    if not plain:
        return ""
    idx = plain.casefold().find((needle or "").casefold())
    if idx < 0:
        return plain[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(plain), idx + len(needle) + radius)
    chunk = plain[start:end]
    if start:
        chunk = "…" + chunk
    if end < len(plain):
        chunk = chunk + "…"
    # подсветка
    pattern = re.compile(re.escape(needle), re.I)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", chunk)
