"""Начальный реестр НПА (Приложение А ТЗ_законодательство) и публикация редакций."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActFragment,
    ActVersion,
    ActVersionStatus,
    LegalAct,
    LegalActCategory,
    LegalActMode,
    LegalActStatus,
    utcnow,
)

# Официальные точки входа (точные редакции подтянет W-17).
IPS = "https://pravo.gov.ru/ips/"
PUBLICATION = "https://publication.pravo.gov.ru/"
VSRF = "https://vsrf.ru/"
MINJUST = "https://minjust.gov.ru/"
MINZDRAV = "https://minzdrav.gov.ru/"
MVD = "https://мвд.рф/"
GOST = "https://www.gostinfo.ru/"


INITIAL_REGISTRY: list[dict] = [
    {
        "slug": "73-fz-sudebno-ekspertnaya-deyatelnost",
        "category": LegalActCategory.law,
        "act_kind": "Федеральный закон",
        "number": "73-ФЗ",
        "adopted_on": date(2001, 5, 31),
        "title": "О государственной судебно-экспертной деятельности в Российской Федерации",
        "authority": "Российская Федерация",
        "mode": LegalActMode.full_text,
        "source_url": IPS,
        "sort_order": 10,
        "tracked_articles": [],
        "notes": "Профильный закон, целиком",
        "ips_nd": "102071320",
        "watch_enabled": True,
        "watch_name": "государственной судебно-экспертной деятельности",
    },
    {
        "slug": "upk-ekspertiza",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "УПК РФ",
        "adopted_on": date(2001, 12, 18),
        "title": "Уголовно-процессуальный кодекс РФ — нормы об экспертизе",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 20,
        "tracked_articles": [
            "ст. 57",
            "ст. 58",
            "ст. 70",
            "ст. 71",
            "ст. 80",
            "гл. 27 (ст. 195–207)",
            "ст. 282",
            "ст. 283",
        ],
        "notes": "Эксперт и экспертиза в уголовном процессе",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "Уголовно-процессуальный кодекс",
    },
    {
        "slug": "gpk-ekspertiza",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "ГПК РФ",
        "adopted_on": date(2002, 11, 14),
        "title": "Гражданский процессуальный кодекс РФ — нормы об экспертизе",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 30,
        "tracked_articles": [
            "ст. 79",
            "ст. 80",
            "ст. 81",
            "ст. 82",
            "ст. 83",
            "ст. 84",
            "ст. 85",
            "ст. 86",
            "ст. 87",
            "ст. 113",
            "ст. 168",
            "ст. 187",
        ],
        "notes": "Экспертиза в гражданском процессе",
        "ips_nd": "102078828",
        "watch_enabled": True,
        "watch_name": "Гражданский процессуальный кодекс",
    },
    {
        "slug": "apk-ekspertiza",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "АПК РФ",
        "adopted_on": date(2002, 7, 24),
        "title": "Арбитражный процессуальный кодекс РФ — нормы об экспертизе",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 40,
        "tracked_articles": ["ст. 55", "ст. 82", "ст. 83", "ст. 84", "ст. 85", "ст. 86", "ст. 87", "ст. 87.1"],
        "notes": "Арбитражный процесс",
        "ips_nd": "102079219",
        "watch_enabled": True,
        "watch_name": "Арбитражный процессуальный кодекс",
    },
    {
        "slug": "kas-ekspertiza",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "КАС РФ",
        "adopted_on": date(2015, 3, 8),
        "title": "Кодекс административного судопроизводства РФ — нормы об экспертизе",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 50,
        "tracked_articles": [
            "ст. 49",
            "ст. 77",
            "ст. 78",
            "ст. 79",
            "ст. 80",
            "ст. 81",
            "ст. 82",
            "ст. 83",
        ],
        "notes": "Административное судопроизводство",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "Кодекс административного судопроизводства",
    },
    {
        "slug": "koap-ekspertiza",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "КоАП РФ",
        "adopted_on": date(2001, 12, 30),
        "title": "Кодекс РФ об административных правонарушениях — нормы об экспертизе",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 60,
        "tracked_articles": ["ст. 25.9", "ст. 26.4", "ст. 26.5"],
        "notes": "Дела об административных правонарушениях",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "об административных правонарушениях",
    },
    {
        "slug": "uk-otvetstvennost-eksperta",
        "category": LegalActCategory.code,
        "act_kind": "Кодекс",
        "number": "УК РФ",
        "adopted_on": date(1996, 6, 13),
        "title": "Уголовный кодекс РФ — ответственность эксперта",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 70,
        "tracked_articles": ["ст. 307", "ст. 310"],
        "notes": "Ответственность эксперта",
        "ips_nd": "102041891",
        "watch_enabled": True,
        "watch_name": "Уголовный кодекс Российской Федерации",
    },
    {
        "slug": "3185-1-psihiatricheskaya-pomoshch",
        "category": LegalActCategory.law,
        "act_kind": "Закон РФ",
        "number": "3185-1",
        "adopted_on": date(1992, 7, 2),
        "title": "О психиатрической помощи и гарантиях прав граждан при её оказании",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 80,
        "tracked_articles": [],
        "notes": "Для судебно-психиатрического профиля; статьи уточнит владелец при наполнении",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "психиатрической помощи",
    },
    {
        "slug": "323-fz-meditsinskaya-ekspertiza",
        "category": LegalActCategory.law,
        "act_kind": "Федеральный закон",
        "number": "323-ФЗ",
        "adopted_on": date(2011, 11, 21),
        "title": "Об основах охраны здоровья граждан в Российской Федерации — ст. 58, 62",
        "authority": "Российская Федерация",
        "mode": LegalActMode.fragments,
        "source_url": IPS,
        "sort_order": 90,
        "tracked_articles": ["ст. 58", "ст. 62"],
        "notes": "Медицинская и судебно-медицинская экспертиза",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "основах охраны здоровья граждан",
    },
    {
        "slug": "plenum-vs-28-2010",
        "category": LegalActCategory.plenum,
        "act_kind": "Постановление Пленума",
        "number": "28",
        "adopted_on": date(2010, 12, 21),
        "title": "О судебной экспертизе по уголовным делам",
        "authority": "Верховный Суд РФ",
        "mode": LegalActMode.full_text,
        "source_url": VSRF,
        "sort_order": 100,
        "tracked_articles": [],
        "notes": "Источник: vsrf.ru",
        "ips_nd": None,
        "watch_enabled": False,
        "watch_name": None,
    },
    {
        "slug": "plenum-vas-23-2014",
        "category": LegalActCategory.plenum,
        "act_kind": "Постановление Пленума",
        "number": "23",
        "adopted_on": date(2014, 4, 4),
        "title": "О некоторых вопросах практики применения законодательства об экспертизе (ВАС РФ)",
        "authority": "Высший Арбитражный Суд РФ",
        "mode": LegalActMode.full_text,
        "source_url": VSRF,
        "sort_order": 110,
        "tracked_articles": [],
        "notes": "Действует в части",
        "ips_nd": None,
        "watch_enabled": False,
        "watch_name": None,
    },
    {
        "slug": "minzdrav-3n-2017",
        "category": LegalActCategory.order,
        "act_kind": "Приказ",
        "number": "3н",
        "adopted_on": date(2017, 1, 12),
        "title": "Порядок проведения судебно-психиатрической экспертизы",
        "authority": "Минздрав России",
        "mode": LegalActMode.full_text,
        "source_url": PUBLICATION,
        "sort_order": 120,
        "tracked_articles": [],
        "notes": "Официальное опубликование",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "судебно-психиатрической экспертизы",
    },
    {
        "slug": "minzdravsots-346n-2010",
        "category": LegalActCategory.order,
        "act_kind": "Приказ",
        "number": "346н",
        "adopted_on": date(2010, 5, 12),
        "title": "Порядок организации и производства судебно-медицинских экспертиз",
        "authority": "Минздравсоцразвития России",
        "mode": LegalActMode.full_text,
        "source_url": PUBLICATION,
        "sort_order": 130,
        "tracked_articles": [],
        "notes": "Проверить действующий статус при наполнении",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "судебно-медицинских экспертиз",
    },
    {
        "slug": "minjust-seu-prikazy",
        "category": LegalActCategory.order,
        "act_kind": "Приказы",
        "number": "СЭУ Минюста",
        "adopted_on": None,
        "title": "Приказы Минюста России о судебно-экспертной деятельности СЭУ Минюста",
        "authority": "Минюст России",
        "mode": LegalActMode.full_text,
        "source_url": MINJUST,
        "sort_order": 140,
        "tracked_articles": [],
        "notes": "Перечень видов экспертиз, аттестация — действующие номера уточняются при наполнении",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "судебно-экспертн",
    },
    {
        "slug": "mvd-511-2005",
        "category": LegalActCategory.order,
        "act_kind": "Приказ",
        "number": "511",
        "adopted_on": date(2005, 6, 29),
        "title": "Вопросы организации производства судебных экспертиз в экспертно-криминалистических подразделениях ОВД",
        "authority": "МВД России",
        "mode": LegalActMode.full_text,
        "source_url": PUBLICATION,
        "sort_order": 150,
        "tracked_articles": [],
        "notes": "В действующей редакции",
        "ips_nd": None,
        "watch_enabled": True,
        "watch_name": "экспертно-криминалистических подразделениях",
    },
    {
        "slug": "gost-r-57344-2016",
        "category": LegalActCategory.standard,
        "act_kind": "ГОСТ Р",
        "number": "57344-2016",
        "adopted_on": date(2016, 12, 13),
        "title": "Судебно-психологическая экспертиза. Термины и определения",
        "authority": "Росстандарт",
        "mode": LegalActMode.card,
        "source_url": GOST,
        "sort_order": 160,
        "tracked_articles": [],
        "notes": "Карточка + ссылка на фонд стандартов; полный текст не републикуем",
        "ips_nd": None,
        "watch_enabled": False,
        "watch_name": None,
    },
    {
        "slug": "gost-r-sudebnaya-ekspertiza-prochee",
        "category": LegalActCategory.standard,
        "act_kind": "ГОСТ Р",
        "number": "57343, 57428 и др.",
        "adopted_on": None,
        "title": "Иные профильные ГОСТ Р по судебной экспертизе",
        "authority": "Росстандарт",
        "mode": LegalActMode.card,
        "source_url": GOST,
        "sort_order": 170,
        "tracked_articles": [],
        "notes": "Карточки; полный текст не републикуем",
        "ips_nd": None,
        "watch_enabled": False,
        "watch_name": None,
    },
]


def ensure_legal_registry(db: Session) -> list[LegalAct]:
    """Идемпотентно загрузить/обновить начальный реестр актов."""
    out: list[LegalAct] = []
    for row in INITIAL_REGISTRY:
        act = db.scalar(select(LegalAct).where(LegalAct.slug == row["slug"]))
        payload = {
            "category": row["category"],
            "act_kind": row["act_kind"],
            "number": row["number"],
            "adopted_on": row["adopted_on"],
            "title": row["title"],
            "authority": row["authority"],
            "status": LegalActStatus.active,
            "mode": row["mode"],
            "source_url": row["source_url"],
            "sort_order": row["sort_order"],
            "tracked_articles": list(row["tracked_articles"]),
            "notes": row.get("notes"),
            "ips_nd": row.get("ips_nd"),
            "watch_enabled": bool(row.get("watch_enabled", False)),
            "watch_name": row.get("watch_name"),
        }
        if act is None:
            act = LegalAct(slug=row["slug"], **payload)
            db.add(act)
            db.flush()
        else:
            for key, value in payload.items():
                setattr(act, key, value)
        _ensure_fragment_stubs(db, act)
        out.append(act)
    db.flush()
    return out


def _ensure_fragment_stubs(db: Session, act: LegalAct) -> None:
    if act.mode != LegalActMode.fragments:
        return
    existing = {
        f.article_ref: f
        for f in db.scalars(select(ActFragment).where(ActFragment.act_id == act.id)).all()
    }
    for idx, ref in enumerate(act.tracked_articles or []):
        frag = existing.get(ref)
        if frag is None:
            db.add(
                ActFragment(
                    act_id=act.id,
                    article_ref=ref,
                    title=ref,
                    body_html="",
                    sort_order=idx * 10,
                )
            )
        else:
            frag.sort_order = idx * 10
            if not frag.title:
                frag.title = ref


def published_version(db: Session, act_id: int) -> ActVersion | None:
    return db.scalar(
        select(ActVersion).where(
            ActVersion.act_id == act_id,
            ActVersion.status == ActVersionStatus.published,
        )
    )


def create_draft_version(
    db: Session,
    *,
    act_id: int,
    body_html: str,
    revision_date: date | None = None,
    change_basis: str | None = None,
    loaded_by_user_id: int | None = None,
    pdf_path: str | None = None,
    diff_text: str | None = None,
    text_origin: str | None = None,
) -> ActVersion:
    version = ActVersion(
        act_id=act_id,
        body_html=body_html or "",
        revision_date=revision_date,
        change_basis=change_basis,
        status=ActVersionStatus.draft,
        loaded_at=utcnow(),
        loaded_by_user_id=loaded_by_user_id,
        pdf_path=pdf_path,
        diff_text=diff_text,
        text_origin=text_origin,
    )
    db.add(version)
    db.flush()
    return version


def publish_version(
    db: Session,
    version: ActVersion,
    *,
    reviewed_by_user_id: int | None = None,
) -> ActVersion:
    """Опубликовать редакцию: прежняя published → archived (одна published на акт)."""
    if version.status == ActVersionStatus.published:
        return version
    if version.status != ActVersionStatus.draft:
        raise ValueError("Можно публиковать только черновик")
    current = published_version(db, version.act_id)
    if current is not None and current.id != version.id:
        current.status = ActVersionStatus.archived
    version.status = ActVersionStatus.published
    version.reviewed_at = utcnow()
    version.reviewed_by_user_id = reviewed_by_user_id
    act = db.get(LegalAct, version.act_id)
    if act is not None:
        act.last_verified_at = utcnow()
    db.flush()
    # W-22: пересборка поискового индекса (черновики/архив не индексируются)
    from app.services.legal_search import rebuild_act_index

    rebuild_act_index(db, version.act_id)
    return version


def reject_version(
    db: Session,
    version: ActVersion,
    *,
    reviewed_by_user_id: int | None = None,
) -> ActVersion:
    """Отклонить черновик → archived (на публичном сайте не виден)."""
    if version.status != ActVersionStatus.draft:
        raise ValueError("Можно отклонять только черновик")
    version.status = ActVersionStatus.archived
    version.reviewed_at = utcnow()
    version.reviewed_by_user_id = reviewed_by_user_id
    db.flush()
    return version


def bootstrap_legal(db: Session) -> None:
    ensure_legal_registry(db)
    db.commit()
