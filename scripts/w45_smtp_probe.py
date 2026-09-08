#!/usr/bin/env python3
"""W-45/Б-2: отправить по одному письму каждого типа на audit-inbox.

Запуск внутри app-контейнера:
  python scripts/w45_smtp_probe.py --to you@example.com
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="audit-inbox")
    args = parser.parse_args()
    to_addr = args.to.strip()

    from app.config import get_settings
    from app.services.billing_mail import (
        notify_admin_subscription_change,
        notify_manual_extend,
        notify_payment_success,
        notify_subscription_expiring,
    )
    from app.services.legal_mail import notify_legal_change
    from app.services.mail import send_email
    from app.services.ops import send_ops_alert

    settings = get_settings()
    if not settings.smtp_host:
        print("SMTP не настроен", file=sys.stderr)
        return 2

    base = settings.app_base_url.rstrip("/")
    results: list[tuple[str, bool]] = []

    # 1) инвайт (шаблон текста как в leads/staff)
    results.append(
        (
            "invite",
            send_email(
                settings,
                to_addr=to_addr,
                subject=f"[{settings.app_name}] W-45 probe: приглашение",
                body=(
                    f"Вас пригласили в {settings.app_name}.\n"
                    f"Принять: {base}/invite/w45-probe-token\n"
                ),
            ),
        )
    )
    # 2) сброс пароля
    results.append(
        (
            "password_reset",
            send_email(
                settings,
                to_addr=to_addr,
                subject=f"[{settings.app_name}] W-45 probe: сброс пароля",
                body=f"Сброс пароля: {base}/reset/w45-probe-token\n",
            ),
        )
    )

    org = SimpleNamespace(name="W-45 Probe Org")
    ends = datetime.now(timezone.utc) + timedelta(days=30)
    pay = SimpleNamespace(
        purpose="W-45 probe payment",
        amount_kop=1000,
        receipt_url=f"{base}/cabinet/billing/",
        receipt_status="DONE",
        manual_basis="W-45 SMTP probe",
    )
    sub = SimpleNamespace(
        tariff=SimpleNamespace(name="Организация"),
        ends_at=ends,
    )

    results.append(
        (
            "payment_receipt",
            notify_payment_success(
                to_addr=to_addr, org=org, payment=pay, ends_at=ends, settings=settings
            ),
        )
    )
    results.append(
        (
            "subscription_ending",
            notify_subscription_expiring(
                to_addr=to_addr, org=org, sub=sub, days_left=7, settings=settings
            ),
        )
    )
    results.append(
        (
            "npa_change",
            notify_legal_change(
                act_title="W-45 probe НПА",
                act_slug="w45-probe",
                change_summary="Тестовое уведомление об изменении НПА.",
                draft_version_id=None,
                eo_numbers=["W45"],
            ),
        )
    )
    # ops alert пишет на admin_notify; дополнительно дублируем явно
    send_ops_alert(
        f"w45_smtp_probe_{uuid4().hex[:8]}",
        "[Док.Москва] W-45 probe: алерт",
        f"Тестовый ops-алерт.\nКабинет: {base}/admin/status\n",
    )
    results.append(
        (
            "alert_direct",
            send_email(
                settings,
                to_addr=to_addr,
                subject=f"[{settings.app_name}] W-45 probe: алерт",
                body=f"Тестовый алерт.\nСтатус: {base}/admin/status\n",
            ),
        )
    )
    results.append(
        (
            "staff_deactivate",
            send_email(
                settings,
                to_addr=to_addr,
                subject=f"[{settings.app_name}] W-45 probe: деактивация сотрудника",
                body=(
                    f"Доступ сотрудника в организацию «{org.name}» отключён.\n"
                    f"Кабинет: {base}/cabinet/\n"
                ),
            ),
        )
    )
    results.append(
        (
            "admin_extend",
            notify_manual_extend(
                to_addr=to_addr, org=org, payment=pay, ends_at=ends, settings=settings
            ),
        )
    )
    results.append(
        (
            "admin_sub_change",
            notify_admin_subscription_change(
                to_addr=to_addr,
                org=org,
                tariff_name="Организация",
                ends_at=ends,
                settings=settings,
            ),
        )
    )

    bad_links = []
    for name, ok in results:
        status = "OK" if ok else "FAIL"
        print(f"{status}\t{name}")
        if not ok:
            bad_links.append(name)
    # проверка https-хоста в телах уже зашита через app_base_url
    print(f"app_base_url={base}")
    if not base.startswith("https://"):
        print("WARN: app_base_url не https", file=sys.stderr)
        return 3
    return 1 if bad_links else 0


if __name__ == "__main__":
    raise SystemExit(main())
