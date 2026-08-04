"""W-43: хранение и обработка печати/подписей организации (branding)."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.safe_paths import org_files_root, resolve_under

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg"})
DPI = 300

# Печать: диаметр 40 мм → 472 px при 300 dpi; порог 450, рекомендация 600
STAMP_DIAMETER_MM = 40.0
STAMP_MIN_PX = 450
STAMP_RECOMMENDED_PX = 600

# Подпись: до 65×22 мм → ~770×260 рекомендуемо; минимум 500×170
SIGN_MAX_W_MM = 65.0
SIGN_MAX_H_MM = 22.0
SIGN_MIN_W_PX = 500
SIGN_MIN_H_PX = 170
SIGN_RECOMMENDED_W_PX = 770
SIGN_RECOMMENDED_H_PX = 260

# Авто-масштаб в документ (спека W-43)
DOC_STAMP_MM = 40.0  # в PDF/InlineImage
DOC_SIGN_W_MM = 65.0
DOC_SIGN_H_MM = 22.0

SLOT_PECHAT = "печать"
SLOT_DIRECTOR = "директор"
SLOT_ACCOUNTANT = "бухгалтер"
SLOT_CASHIER = "кассир"

SLOTS: dict[str, dict[str, Any]] = {
    SLOT_PECHAT: {
        "label": "Печать организации",
        "kind": "stamp",
        "filename": "stamp.png",
        "placeholder": "факсимиле_печать",
    },
    SLOT_DIRECTOR: {
        "label": "Подпись руководителя",
        "kind": "signature",
        "filename": "sign_director.png",
        "placeholder": "факсимиле_директор",
    },
    SLOT_ACCOUNTANT: {
        "label": "Подпись главного бухгалтера",
        "kind": "signature",
        "filename": "sign_accountant.png",
        "placeholder": "факсимиле_бухгалтер",
    },
    SLOT_CASHIER: {
        "label": "Подпись кассира",
        "kind": "signature",
        "filename": "sign_cashier.png",
        "placeholder": "факсимиле_кассир",
    },
}

PLACEHOLDER_TO_SLOT = {meta["placeholder"]: key for key, meta in SLOTS.items()}
FACSIMILE_PLACEHOLDERS = frozenset(PLACEHOLDER_TO_SLOT)
RESERVED_FACSIMILE_NAMES = FACSIMILE_PLACEHOLDERS | frozenset(SLOTS.keys())


class VerdictLevel(str, Enum):
    excellent = "excellent"
    usable = "usable"
    rejected = "rejected"


@dataclass
class ImageVerdict:
    level: VerdictLevel
    message: str
    width: int = 0
    height: int = 0
    print_w_mm: float = 0.0
    print_h_mm: float = 0.0
    has_alpha: bool = False


class BrandingError(ValueError):
    """Ошибка загрузки/обработки branding-изображения."""


def branding_dir(org_id: int) -> Path:
    root = resolve_under(org_files_root(org_id), "branding")
    root.mkdir(parents=True, exist_ok=True)
    return root


def slot_path(org_id: int, slot: str) -> Path:
    meta = SLOTS.get(slot)
    if meta is None:
        raise BrandingError(f"Неизвестный слот: {slot}")
    return resolve_under(branding_dir(org_id), meta["filename"])


def meta_path(org_id: int) -> Path:
    return resolve_under(branding_dir(org_id), "meta.json")


def load_meta(org_id: int) -> dict[str, Any]:
    path = meta_path(org_id)
    if not path.is_file():
        return {"slots": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"slots": {}}
    if not isinstance(data, dict):
        return {"slots": {}}
    data.setdefault("slots", {})
    return data


def save_meta(org_id: int, data: dict[str, Any]) -> None:
    path = meta_path(org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def px_to_mm(px: int, dpi: int = DPI) -> float:
    return round(px * 25.4 / dpi, 1)


def mm_to_px(mm: float, dpi: int = DPI) -> int:
    return int(round(mm * dpi / 25.4))


def _open_verified(data: bytes) -> Image.Image:
    """Pillow verify + reopen (polyglot-защита)."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise BrandingError(f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    try:
        bio = io.BytesIO(data)
        with Image.open(bio) as img:
            img.verify()
        bio = io.BytesIO(data)
        img = Image.open(bio)
        img.load()
        img = ImageOps.exif_transpose(img)
        return img
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BrandingError("Файл не является изображением PNG/JPEG") from exc


def remove_near_white_bg(img: Image.Image, threshold: int = 240) -> Image.Image:
    """Сделать почти белый фон прозрачным (порог яркости 0–255)."""
    threshold = max(0, min(255, int(threshold)))
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    assert pixels is not None
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if r >= threshold and g >= threshold and b >= threshold:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def trim_transparent(img: Image.Image, pad: int = 2) -> Image.Image:
    rgba = img.convert("RGBA")
    bbox = rgba.getbbox()
    if not bbox:
        return rgba
    l, t, r, b = bbox
    l = max(0, l - pad)
    t = max(0, t - pad)
    r = min(rgba.width, r + pad)
    b = min(rgba.height, b + pad)
    return rgba.crop((l, t, r, b))


def _contrast_after_clean(img: Image.Image) -> float:
    """Грубая оценка контраста непрозрачных пикселей (0–1)."""
    rgba = img.convert("RGBA")
    hist = rgba.convert("L").histogram()
    # только оценка по всему изображению; низкий контраст → узкий гистограммный разброс
    total = sum(hist) or 1
    mean = sum(i * c for i, c in enumerate(hist)) / total
    var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / total
    return min(1.0, (var ** 0.5) / 64.0)


def evaluate_image(data: bytes, *, kind: str) -> ImageVerdict:
    """Вердикт до сохранения: excellent / usable / rejected."""
    try:
        img = _open_verified(data)
    except BrandingError as exc:
        return ImageVerdict(VerdictLevel.rejected, str(exc))
    w, h = img.size
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    print_w = px_to_mm(w)
    print_h = px_to_mm(h)

    if kind == "stamp":
        side = min(w, h)
        if side < STAMP_MIN_PX:
            return ImageVerdict(
                VerdictLevel.rejected,
                f"Слишком малое разрешение печати ({w}×{h} px). "
                f"Нужно не менее {STAMP_MIN_PX}×{STAMP_MIN_PX} px "
                f"(≈{STAMP_DIAMETER_MM:.0f} мм при 300 dpi).",
                w,
                h,
                print_w,
                print_h,
                has_alpha,
            )
        if side < STAMP_RECOMMENDED_PX:
            return ImageVerdict(
                VerdictLevel.usable,
                f"Пригодно, но печать может быть «мыльной»: {w}×{h} px ≈ "
                f"{print_w}×{print_h} мм при 300 dpi. Рекомендуем ≥ "
                f"{STAMP_RECOMMENDED_PX}×{STAMP_RECOMMENDED_PX} px.",
                w,
                h,
                print_w,
                print_h,
                has_alpha,
            )
        return ImageVerdict(
            VerdictLevel.excellent,
            "Отлично — разрешение достаточное для печати 40 мм.",
            w,
            h,
            print_w,
            print_h,
            has_alpha,
        )

    # signature
    if w < SIGN_MIN_W_PX or h < SIGN_MIN_H_PX:
        return ImageVerdict(
            VerdictLevel.rejected,
            f"Слишком малое разрешение подписи ({w}×{h} px). "
            f"Нужно не менее {SIGN_MIN_W_PX}×{SIGN_MIN_H_PX} px "
            f"(до {SIGN_MAX_W_MM:.0f}×{SIGN_MAX_H_MM:.0f} мм при 300 dpi).",
            w,
            h,
            print_w,
            print_h,
            has_alpha,
        )
    if w < SIGN_RECOMMENDED_W_PX or h < SIGN_RECOMMENDED_H_PX:
        return ImageVerdict(
            VerdictLevel.usable,
            f"Пригодно, но подпись может быть нечёткой: {w}×{h} px ≈ "
            f"{print_w}×{print_h} мм при 300 dpi. Рекомендуем ≥ "
            f"{SIGN_RECOMMENDED_W_PX}×{SIGN_RECOMMENDED_H_PX} px.",
            w,
            h,
            print_w,
            print_h,
            has_alpha,
        )
    return ImageVerdict(
        VerdictLevel.excellent,
        "Отлично — разрешение достаточное для подписи в документе.",
        w,
        h,
        print_w,
        print_h,
        has_alpha,
    )


def process_and_save(
    org_id: int,
    slot: str,
    data: bytes,
    *,
    remove_bg: bool = True,
    bg_threshold: int = 240,
) -> tuple[Path, ImageVerdict]:
    """Валидация, очистка фона, PNG-перекодирование, сохранение в branding/."""
    if slot not in SLOTS:
        raise BrandingError(f"Неизвестный слот: {slot}")
    kind = SLOTS[slot]["kind"]
    verdict = evaluate_image(data, kind=kind)
    if verdict.level == VerdictLevel.rejected:
        raise BrandingError(verdict.message)

    img = _open_verified(data)
    if remove_bg:
        img = remove_near_white_bg(img, threshold=bg_threshold)
        # низкий контраст после очистки → понизить вердикт
        if verdict.level == VerdictLevel.excellent and _contrast_after_clean(img) < 0.25:
            verdict = ImageVerdict(
                VerdictLevel.usable,
                "Пригодно, но после очистки фона контраст низкий "
                "(возможны тени или бледные штрихи). Проверьте предпросмотр.",
                verdict.width,
                verdict.height,
                verdict.print_w_mm,
                verdict.print_h_mm,
                True,
            )
    else:
        img = img.convert("RGBA")
    img = trim_transparent(img)

    out = slot_path(org_id, slot)
    img.save(out, format="PNG", optimize=True)

    meta = load_meta(org_id)
    meta.setdefault("slots", {})[slot] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "width": img.width,
        "height": img.height,
        "remove_bg": bool(remove_bg),
        "bg_threshold": int(bg_threshold),
        "verdict": verdict.level.value,
        "message": verdict.message,
    }
    save_meta(org_id, meta)
    return out, verdict


def delete_slot(org_id: int, slot: str) -> bool:
    if slot not in SLOTS:
        raise BrandingError(f"Неизвестный слот: {slot}")
    path = slot_path(org_id, slot)
    existed = path.is_file()
    if existed:
        path.unlink()
    meta = load_meta(org_id)
    meta.get("slots", {}).pop(slot, None)
    save_meta(org_id, meta)
    return existed


def slot_exists(org_id: int, slot: str) -> bool:
    try:
        return slot_path(org_id, slot).is_file()
    except BrandingError:
        return False


def list_slots_status(org_id: int) -> list[dict[str, Any]]:
    meta = load_meta(org_id)
    rows = []
    for key, conf in SLOTS.items():
        path = slot_path(org_id, key)
        info = (meta.get("slots") or {}).get(key) or {}
        rows.append(
            {
                "slot": key,
                "label": conf["label"],
                "kind": conf["kind"],
                "placeholder": conf["placeholder"],
                "exists": path.is_file(),
                "path": path if path.is_file() else None,
                "info": info,
            }
        )
    return rows


def preview_png_bytes(org_id: int, slot: str, *, max_side: int = 320) -> bytes | None:
    path = slot_path(org_id, slot)
    if not path.is_file():
        return None
    with Image.open(path) as img:
        img = img.convert("RGBA")
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def doc_image_width_mm(slot: str) -> float:
    kind = SLOTS[slot]["kind"]
    if kind == "stamp":
        return DOC_STAMP_MM
    return DOC_SIGN_W_MM
