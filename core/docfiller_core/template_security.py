"""
Безопасный разбор пользовательских DOCX-шаблонов (W-41).

Разрешены только {{ поля }} и фильтры из белого списка.
Конструкции {% ... %}, неизвестные фильтры и опасный доступ к атрибутам
отклоняются при загрузке с понятным сообщением.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docxtpl import DocxTemplate
from jinja2 import nodes
from jinja2.exceptions import TemplateSyntaxError
from jinja2.meta import find_undeclared_variables
from jinja2.sandbox import SandboxedEnvironment

from . import filters as doc_filters
from .filler import SYSTEM_VARIABLES

# Белый список фильтров: доменные + безопасные встроенные Jinja.
ALLOWED_FILTERS: frozenset[str] = frozenset(
    {
        # доменные (filters.install)
        "им",
        "род",
        "дат",
        "вин",
        "тв",
        "пр",
        "фраза",
        "прописью",
        "дней_прописью",
        "согласовать",
        "раб_дней",
        "кал_дней",
        "руб",
        "руб_копейки",
        "дата_рус",
        "дата",
        "число",
        "ио",
        "фио_кратко",
        "фамилия",
        "имя",
        "отчество",
        "пол",
        # штатные Jinja (безопасные)
        "default",
        "d",
        "e",
        "escape",
        "forceescape",
        "string",
        "trim",
        "upper",
        "lower",
        "capitalize",
        "title",
        "replace",
        "length",
        "list",
        "join",
        "first",
        "last",
        "int",
        "float",
        "round",
        "abs",
        "min",
        "max",
        "wordcount",
        "truncate",
        "striptags",
        "urlencode",
        "center",
        "indent",
        "wordwrap",
        "batch",
        "slice",
        "sort",
        "unique",
        "reject",
        "select",
        "map",
        "selectattr",
        "rejectattr",
        "groupby",
        "dictsort",
        "xmlattr",
        "urlize",
        "filesizeformat",
        "pprint",
        "tojson",
    }
)

# Имена, которые нельзя определять как пользовательские поля / использовать как корни.
RESERVED_ROOT_NAMES: frozenset[str] = frozenset(
    {
        "настройки",
        "организация",
        "cycler",
        "joiner",
        "namespace",
        "range",
        "dict",
        "lipsum",
        "self",
        "request",
        "config",
        "g",
    }
)

_STATEMENT_NODES = (
    nodes.For,
    nodes.If,
    nodes.Macro,
    nodes.CallBlock,
    nodes.FilterBlock,
    nodes.Assign,
    nodes.AssignBlock,
    nodes.Import,
    nodes.FromImport,
    nodes.Include,
    nodes.Extends,
    nodes.Block,
    nodes.With,
    nodes.Break,
    nodes.Continue,
    nodes.Scope,
    nodes.OverlayScope,
)

MSG_STATEMENTS = (
    "В шаблоне поддерживаются только {{ поля }} и фильтры "
    "(например {{ сумма | руб }}). Конструкции {% ... %} запрещены."
)


class TemplateSecurityError(Exception):
    """Шаблон не прошёл проверку безопасности / синтаксиса."""


@dataclass
class TemplateAnalysis:
    variables: list[str] = field(default_factory=list)
    filters_used: list[str] = field(default_factory=list)


def _line_hint(node: nodes.Node) -> str:
    lineno = getattr(node, "lineno", None)
    if lineno:
        return f" (около строки XML {lineno})"
    return ""


def _walk_security(node: nodes.Node, *, filters_seen: set[str]) -> None:
    if isinstance(node, _STATEMENT_NODES):
        raise TemplateSecurityError(MSG_STATEMENTS + _line_hint(node))

    if isinstance(node, nodes.Filter):
        name = node.name
        filters_seen.add(name)
        if name not in ALLOWED_FILTERS:
            raise TemplateSecurityError(
                f"Неизвестный или запрещённый фильтр «{name}»{_line_hint(node)}. "
                "Разрешены: руб, прописью, дата_рус, ио, число и другие из справки."
            )

    if isinstance(node, nodes.Getattr):
        attr = node.attr or ""
        if attr.startswith("_"):
            raise TemplateSecurityError(
                f"Запрещён доступ к атрибуту «{attr}»{_line_hint(node)}."
            )

    if isinstance(node, nodes.Getitem):
        # cycler['__init__'] и т.п.
        arg = node.arg
        if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
            if arg.value.startswith("_"):
                raise TemplateSecurityError(
                    f"Запрещён доступ к ключу «{arg.value}»{_line_hint(node)}."
                )

    if isinstance(node, nodes.Call):
        # Вызовы методов/функций в пользовательских шаблонах не нужны.
        raise TemplateSecurityError(
            f"Вызовы функций в шаблоне запрещены{_line_hint(node)}. "
            "Используйте {{ поле }} и фильтры (| руб, | прописью…)."
        )

    if isinstance(node, nodes.Name):
        if node.name in RESERVED_ROOT_NAMES and node.name not in SYSTEM_VARIABLES:
            # «настройки» допускается как системный корень; cycler и пр. — нет
            if node.name != "настройки":
                raise TemplateSecurityError(
                    f"Имя «{node.name}» зарезервировано и недоступно в шаблоне"
                    f"{_line_hint(node)}."
                )
        if node.name.startswith("_"):
            raise TemplateSecurityError(
                f"Запрещённое имя «{node.name}»{_line_hint(node)}."
            )

    for child in node.iter_child_nodes():
        _walk_security(child, filters_seen=filters_seen)


def _raw_has_statements(xml: str) -> bool:
    # Пойманные docxtpl-ом конструкции; сырой маркер — страховка.
    return "{%" in xml or "%}" in xml


def _load_patched_xml(source: str | Path | bytes) -> str:
    """Собрать XML тела/колонтитулов так же, как docxtpl.get_undeclared_template_variables."""
    import os
    import tempfile

    from docx import Document
    from docx.oxml import parse_xml

    tmp_path: str | None = None
    try:
        if isinstance(source, bytes):
            fd, tmp_path = tempfile.mkstemp(suffix=".docx")
            os.close(fd)
            Path(tmp_path).write_bytes(source)
            path = tmp_path
        else:
            path = str(source)

        helper = DocxTemplate(path)
        temp_doc = Document(path)
        xml = helper.xml_to_string(temp_doc._element.body)
        xml = helper.patch_xml(xml)

        for uri in (helper.HEADER_URI, helper.FOOTER_URI):
            for _rel_key, val in temp_doc.part.rels.items():
                if val.reltype == uri and val.target_part.blob:
                    part_xml = helper.xml_to_string(parse_xml(val.target_part.blob))
                    xml += helper.patch_xml(part_xml)
        return xml
    except TemplateSecurityError:
        raise
    except Exception as exc:
        raise TemplateSecurityError(f"Не удалось разобрать шаблон: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def analyze_docx_template(source: str | Path | bytes) -> TemplateAnalysis:
    """Проверить DOCX и вернуть список переменных формы."""
    xml = _load_patched_xml(source)

    env = SandboxedEnvironment()
    doc_filters.install(env)

    if _raw_has_statements(xml):
        raise TemplateSecurityError(MSG_STATEMENTS)

    try:
        ast = env.parse(xml)
    except TemplateSyntaxError as exc:
        msg = str(exc).lower()
        if "{%" in xml or "unexpected" in msg or "for" in msg or "if" in msg:
            raise TemplateSecurityError(MSG_STATEMENTS) from exc
        raise TemplateSecurityError(f"Синтаксическая ошибка в шаблоне: {exc}") from exc

    filters_seen: set[str] = set()
    _walk_security(ast, filters_seen=filters_seen)

    undeclared = find_undeclared_variables(ast)
    variables = sorted(v for v in undeclared if v not in SYSTEM_VARIABLES)
    for name in variables:
        if name in RESERVED_ROOT_NAMES and name != "настройки":
            raise TemplateSecurityError(
                f"Имя «{name}» зарезервировано системой и не может быть полем формы."
            )
    return TemplateAnalysis(
        variables=variables,
        filters_used=sorted(filters_seen),
    )


def assert_safe_docx_template(source: str | Path | bytes) -> list[str]:
    """Проверить шаблон; вернуть список переменных или бросить TemplateSecurityError."""
    return analyze_docx_template(source).variables


def zip_has_vba_or_encryption(data: bytes) -> str | None:
    """Вернуть текст ошибки, если в OOXML есть VBA или шифрование."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return None

    lower_map = {n.lower(): n for n in names}
    for key in lower_map:
        base = key.rsplit("/", 1)[-1]
        if base in {"vbaproject.bin", "vbadata.xml", "vbaproject.xml"}:
            return "Макросы VBA в шаблоне запрещены — сохраните DOCX без макросов."
        if "vbaproject" in base:
            return "Макросы VBA в шаблоне запрещены — сохраните DOCX без макросов."

    if "encryptedpackage" in lower_map or "encryptioninfo" in {
        k.rsplit("/", 1)[-1] for k in lower_map
    }:
        return "Парольная защита / шифрование DOCX не поддерживаются — снимите пароль."

    # Классический encrypted OPC: нет word/document.xml, есть EncryptedPackage
    if "word/document.xml" not in {n.lower() for n in names}:
        if any("encrypted" in n.lower() for n in names):
            return "Парольная защита / шифрование DOCX не поддерживаются — снимите пароль."
    return None
