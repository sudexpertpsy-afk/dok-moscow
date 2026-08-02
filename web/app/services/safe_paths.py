"""Безопасное разрешение путей под FILES_ROOT / org (аудит F-01/F-02).

Использует Path.resolve() + is_relative_to — без уязвимого startswith
(prefix-bypass вида FILES_ROOT_evil) и со следованием symlink наружу.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def files_root() -> Path:
    return Path(get_settings().files_root).resolve()


def org_files_root(org_id: int) -> Path:
    """Каталог FILES_ROOT/{org_id}; сам должен лежать под FILES_ROOT."""
    root = files_root()
    org = (root / str(int(org_id))).resolve()
    if not org.is_relative_to(root):
        raise FileNotFoundError("Недопустимый путь")
    return org


def resolve_under(base: Path, *parts: str | Path) -> Path:
    """Резолв пути, который обязан остаться внутри base после resolve()."""
    base_r = Path(base).resolve()
    if not parts:
        return base_r
    if len(parts) == 1:
        p = Path(parts[0])
        candidate = p.resolve() if p.is_absolute() else (base_r / p).resolve()
    else:
        candidate = base_r.joinpath(*[str(p) for p in parts]).resolve()
    if not candidate.is_relative_to(base_r):
        raise FileNotFoundError("Недопустимый путь")
    return candidate


def resolve_under_org(org_id: int, path: str | Path) -> Path:
    """path — абсолютный или относительный к FILES_ROOT; обязан быть под org.

    Подходит для Document.file_path («{org_id}/…») и абсолютных file_path
    из результата job (комплекты PDF/ZIP).
    """
    org = org_files_root(org_id)
    p = Path(path)
    if p.is_absolute():
        candidate = p.resolve()
    else:
        candidate = (files_root() / p).resolve()
    if not candidate.is_relative_to(org):
        raise FileNotFoundError("Недопустимый путь")
    return candidate
