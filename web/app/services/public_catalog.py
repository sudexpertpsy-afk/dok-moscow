"""W-44: публичный каталог образцов (/obraztsy) из реестра встроенных шаблонов."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from app.services.legal_public import get_act_by_slug, normative_for_template
from app.services.templates import list_templates, template_path

# SEO-slug ← stem шаблона (латиница, стабильные URL).
_SLUG_BY_STEM: dict[str, str] = {
    "Акт_ГПД_эксперт": "akt-gpd-ekspert",
    "Акт_оказанных_услуг": "akt-okazannykh-uslug",
    "Акт_оказанных_услуг_юрлицо": "akt-okazannykh-uslug-yurlitso",
    "Договор_ГПД_эксперт": "dogovor-gpd-ekspert",
    "Договор_обучение_полиграф": "dogovor-obuchenie-poligrafa",
    "Договор_обучение_СПЭ": "dogovor-obuchenie-spe",
    "Договор_обучение_СПЭ_юрлицо": "dogovor-obuchenie-spe-yurlitso",
    "Договор_освидетельствование": "dogovor-osvidetelstvovanie",
    "Договор_рецензия": "dogovor-retsenziya",
    "Договор_рецензия_юрлицо": "dogovor-retsenziya-yurlitso",
    "Договор_услуги_v2": "dogovor-na-ekspertizu",
    "Договор_услуги_с_печатью": "dogovor-uslugi-s-pechatyu",
    "Договор_услуги_юрлицо": "dogovor-uslugi-yurlitso",
    "Допсоглашение_продление": "dop-soglashenie-prodlenie",
    "Допсоглашение_продление_юрлицо": "dop-soglashenie-prodlenie-yurlitso",
    "Заключение_эксперта_гражданский_процесс": "zaklyuchenie-eksperta-gpk",
    "Заключение_эксперта_уголовный_процесс": "zaklyuchenie-eksperta-upk",
    "ПКО_КО-1": "pko-ko-1",
    "Психология_ДРО_с_итогом": "psikhologiya-dro",
    "Согласие_ПДн": "soglasie-pdn",
    "Соглашение_расторжение": "soglashenie-rastorzhenie",
    "Соглашение_расторжение_юрлицо": "soglashenie-rastorzhenie-yurlitso",
    "Сопроводительное_письмо": "soprovoditelnoe-pismo",
    "СППЭ_информация_суду": "sppe-informatsiya-sudu",
    "Счёт_на_оплату": "schet-na-oplatu",
    "Счёт_на_оплату_юрлицо": "schet-na-oplatu-yurlitso",
    "Уведомление_расторжение": "uvedomlenie-rastorzhenie",
    "Уведомление_расторжение_юрлицо": "uvedomlenie-rastorzhenie-yurlitso",
    "Ходатайство_о_назначении_экспертизы": "khodataystvo-o-naznachenii-ekspertizy",
}

# Статические PDF витрины (водяной знак ОБРАЗЕЦ) → имя шаблона.
_SAMPLE_PDF_BY_NAME: dict[str, str] = {
    "Договор_услуги_v2.docx": "dogovor-fl.pdf",
    "Счёт_на_оплату.docx": "schet.pdf",
    "Акт_оказанных_услуг.docx": "akt.pdf",
    "Заключение_эксперта_гражданский_процесс.docx": "zaklyuchenie-fragment.pdf",
}

_SAMPLE_THUMB_BY_PDF: dict[str, str] = {
    "dogovor-fl.pdf": "sample-dogovor.webp",
    "schet.pdf": "sample-schet.webp",
    "akt.pdf": "sample-akt.webp",
    "zaklyuchenie-fragment.pdf": "sample-zaklyuchenie.webp",
    "schet-faksimile.pdf": "sample-schet-faksimile.webp",
}

# Кратко: для чего и кто стороны (SEO-копирайт; иначе — из группы).
_META_BY_STEM: dict[str, tuple[str, str]] = {
    "Договор_услуги_v2": (
        "Договор на проведение судебной экспертизы с физическим лицом",
        "Заказчик (ФЛ) и исполнитель — экспертная организация",
    ),
    "Договор_услуги_юрлицо": (
        "Договор на проведение судебной экспертизы с юридическим лицом",
        "Заказчик (юрлицо) и исполнитель — экспертная организация",
    ),
    "Заключение_эксперта_гражданский_процесс": (
        "Заключение эксперта по гражданскому делу (каркас по ГПК и ФЗ-73)",
        "Эксперт / экспертная организация; представление в суд",
    ),
    "Заключение_эксперта_уголовный_процесс": (
        "Заключение эксперта по уголовному делу (каркас по УПК и ФЗ-73)",
        "Эксперт / экспертная организация; представление в суд",
    ),
    "Счёт_на_оплату": (
        "Счёт на оплату услуг экспертизы",
        "Исполнитель выставляет заказчику",
    ),
    "Акт_оказанных_услуг": (
        "Акт оказанных услуг по договору на экспертизу",
        "Заказчик и исполнитель",
    ),
    "Ходатайство_о_назначении_экспертизы": (
        "Ходатайство о назначении судебной экспертизы",
        "Сторона / представитель — в суд",
    ),
    "Сопроводительное_письмо": (
        "Сопроводительное письмо к материалам экспертизы",
        "Экспертная организация — в суд",
    ),
}

_GROUP_FALLBACK_PARTIES = {
    "Договоры": "Заказчик и исполнитель",
    "Счета и акты": "Заказчик и исполнитель по договору",
    "Кассовые": "Кассир / организация",
    "Письма суду": "Экспертная организация — в суд",
    "Заключения эксперта": "Эксперт / экспертная организация",
}


@dataclass(frozen=True)
class CatalogItem:
    name: str
    stem: str
    slug: str
    title: str
    description: str
    group: str
    kind: str
    purpose: str
    parties: str
    sample_pdf: str | None
    sample_thumb: str | None
    fields: tuple[str, ...]
    normative_slugs: tuple[str, ...]


def slug_for_stem(stem: str) -> str:
    if stem in _SLUG_BY_STEM:
        return _SLUG_BY_STEM[stem]
    # запасной путь для новых шаблонов без записи в карте
    import re
    import unicodedata

    raw = stem.replace("_", "-").casefold()
    # простая транслитерация остатка
    table = str.maketrans(
        {
            "а": "a",
            "б": "b",
            "в": "v",
            "г": "g",
            "д": "d",
            "е": "e",
            "ё": "e",
            "ж": "zh",
            "з": "z",
            "и": "i",
            "й": "y",
            "к": "k",
            "л": "l",
            "м": "m",
            "н": "n",
            "о": "o",
            "п": "p",
            "р": "r",
            "с": "s",
            "т": "t",
            "у": "u",
            "ф": "f",
            "х": "kh",
            "ц": "ts",
            "ч": "ch",
            "ш": "sh",
            "щ": "shch",
            "ъ": "",
            "ы": "y",
            "ь": "",
            "э": "e",
            "ю": "yu",
            "я": "ya",
        }
    )
    raw = unicodedata.normalize("NFKC", raw).translate(table)
    raw = re.sub(r"[^a-z0-9-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw or "template"


def human_title(stem: str) -> str:
    return stem.replace("_", " ").replace(" v2", "").strip()


def _fields_for(name: str) -> tuple[str, ...]:
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.filler import list_template_variables

    try:
        path = template_path(name)
    except FileNotFoundError:
        return ()
    try:
        vars_ = list_template_variables(path)
    except Exception:
        return ()
    # стабильный порядок, без технических факсимиле-служебных если не нужны — оставляем все
    return tuple(sorted(str(v) for v in vars_ if v))


@lru_cache(maxsize=1)
def _catalog_cache_key() -> float:
    from app.services.templates import templates_dir

    try:
        return templates_dir().stat().st_mtime
    except OSError:
        return 0.0


_items_cache: tuple[float, tuple[CatalogItem, ...]] | None = None


def invalidate_public_catalog_cache() -> None:
    global _items_cache
    _items_cache = None


def list_catalog_items(*, with_fields: bool = False) -> list[CatalogItem]:
    """Каталог встроенных шаблонов для /obraztsy."""
    global _items_cache
    stamp = _catalog_cache_key()
    if not with_fields and _items_cache is not None and _items_cache[0] == stamp:
        return list(_items_cache[1])

    items: list[CatalogItem] = []
    for raw in list_templates():
        stem = raw["stem"]
        name = raw["name"]
        group = raw.get("group") or "Прочее"
        meta = _META_BY_STEM.get(stem)
        if meta:
            purpose, parties = meta
            title = purpose
        else:
            title = human_title(stem)
            purpose = (raw.get("description") or title).strip()
            parties = _GROUP_FALLBACK_PARTIES.get(group, "Стороны по документу")
        pdf = _SAMPLE_PDF_BY_NAME.get(name)
        thumb = _SAMPLE_THUMB_BY_PDF.get(pdf) if pdf else None
        fields: tuple[str, ...] = _fields_for(name) if with_fields else ()
        items.append(
            CatalogItem(
                name=name,
                stem=stem,
                slug=slug_for_stem(stem),
                title=title,
                description=(raw.get("description") or title).strip(),
                group=group,
                kind=raw.get("kind") or "документ",
                purpose=purpose,
                parties=parties,
                sample_pdf=f"/static/samples/{pdf}" if pdf else None,
                sample_thumb=f"/static/img/{thumb}" if thumb else None,
                fields=fields,
                normative_slugs=tuple(normative_for_template(name)),
            )
        )
    if not with_fields:
        _items_cache = (stamp, tuple(items))
    return items


def catalog_grouped() -> list[tuple[str, list[CatalogItem]]]:
    from app.services.templates import ensure_core_on_path

    ensure_core_on_path()
    from docfiller_core.template_manifest import group_sort_key

    items = list_catalog_items()
    buckets: dict[str, list[CatalogItem]] = {}
    for it in items:
        buckets.setdefault(it.group, []).append(it)
    return sorted(buckets.items(), key=lambda kv: group_sort_key(kv[0]))


def get_catalog_item(slug: str) -> CatalogItem | None:
    slug_n = (slug or "").strip()
    if not slug_n:
        return None
    for it in list_catalog_items():
        if it.slug == slug_n:
            # детальная страница — с полями
            fields = _fields_for(it.name)
            return CatalogItem(
                name=it.name,
                stem=it.stem,
                slug=it.slug,
                title=it.title,
                description=it.description,
                group=it.group,
                kind=it.kind,
                purpose=it.purpose,
                parties=it.parties,
                sample_pdf=it.sample_pdf,
                sample_thumb=it.sample_thumb,
                fields=fields,
                normative_slugs=it.normative_slugs,
            )
    return None


def normative_acts_for_item(db: Session, item: CatalogItem) -> list:
    """Активные акты /zakon для блока «Нормативная база»."""
    acts = []
    seen: set[str] = set()
    for slug in item.normative_slugs:
        if slug in seen:
            continue
        seen.add(slug)
        act = get_act_by_slug(db, slug)
        if act is not None:
            acts.append(act)
    return acts


def templates_for_act_slug(slug: str) -> list[CatalogItem]:
    """Обратная перелинковка: шаблоны, ссылающиеся на данный акт."""
    slug_n = (slug or "").strip()
    if not slug_n:
        return []
    return [it for it in list_catalog_items() if slug_n in it.normative_slugs]


def all_obraztsy_paths() -> list[str]:
    paths = ["/obraztsy"]
    for it in list_catalog_items():
        paths.append(f"/obraztsy/{it.slug}")
    return paths
