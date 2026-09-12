"""W-50 часть A: deploy.log, TLS days-left, пороги, backup excludes, mail From."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ops.certs import cert_days_left, host_from_url
from app.ops.deploy_log import parse_ok_tags, previous_ok_tag
from app.ops.mail_auth import smtp_from_ok
from app.ops.metrics import disk_trend_gb_per_week
from app.ops.thresholds import (
    tone_disk_pct,
    tone_mail_auth,
    tone_restore_days,
    tone_tls_days,
)


FIXTURE_DEPLOY_LOG = """\
2026-09-01T10:00:00Z tag=v1.2.0 sha=aaa status=ok df=40%
2026-09-05T10:00:00Z tag=v1.2.1 sha=bbb status=fail df=45%
2026-09-08T10:00:00Z tag=v1.2.1 sha=bbb status=ok df=48%
2026-09-09T10:00:00Z tag=v1.2.2 sha=ccc status=ok df=53%
"""


def test_parse_ok_tags_skips_fail():
    assert parse_ok_tags(FIXTURE_DEPLOY_LOG) == ["v1.2.0", "v1.2.1", "v1.2.2"]


def test_previous_ok_tag_is_second_to_last():
    assert previous_ok_tag(FIXTURE_DEPLOY_LOG) == "v1.2.1"


def test_previous_ok_tag_legacy_deploy_done():
    legacy = """\
2026-08-01T00:00:00Z deploy done tag=v1.1.0 image=x
2026-09-01T00:00:00Z deploy done tag=v1.2.0 image=y
"""
    assert previous_ok_tag(legacy) == "v1.1.0"


def test_host_from_url():
    assert host_from_url("https://app.dok.moscow/login") == "app.dok.moscow"
    assert host_from_url("dok.moscow") == "dok.moscow"


def test_cert_days_left_direct():
    fake_cert = {"notAfter": "Oct 30 08:01:33 2026 GMT"}

    class FakeSslSock:
        def getpeercert(self):
            return fake_cert

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_ctx = MagicMock()
    fake_ctx.wrap_socket.return_value = FakeSslSock()
    fixed_now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return datetime.fromtimestamp(ts, tz=tz)

    with (
        patch("app.ops.certs.ssl.create_default_context", return_value=fake_ctx),
        patch("app.ops.certs.socket.create_connection", return_value=FakeSock()),
        patch("app.ops.certs.dt.datetime", FixedDateTime),
    ):
        days = cert_days_left("dok.moscow")
    assert days == 47


def test_cert_days_left_hairpin_fallback():
    """При OSError на публичный host — повтор на caddy с тем же SNI."""
    fake_cert = {"notAfter": "Oct 30 08:01:33 2026 GMT"}
    calls: list[tuple[str, str]] = []

    class FakeSslSock:
        def getpeercert(self):
            return fake_cert

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_ctx = MagicMock()
    fake_ctx.wrap_socket.side_effect = lambda sock, server_hostname=None: (
        calls.append(("sni", server_hostname or "")),
        FakeSslSock(),
    )[1]

    def fake_connect(addr, timeout=None):
        host, _port = addr
        calls.append(("tcp", host))
        if host == "dok.moscow":
            raise OSError("hairpin")
        return FakeSock()

    fixed_now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return datetime.fromtimestamp(ts, tz=tz)

    with (
        patch("app.ops.certs.ssl.create_default_context", return_value=fake_ctx),
        patch("app.ops.certs.socket.create_connection", side_effect=fake_connect),
        patch("app.ops.certs.dt.datetime", FixedDateTime),
        patch.dict("os.environ", {"CADDY_PROBE_HOST": "caddy"}, clear=False),
    ):
        days = cert_days_left("dok.moscow")
    assert days == 47
    assert ("tcp", "dok.moscow") in calls
    assert ("tcp", "caddy") in calls
    assert ("sni", "dok.moscow") in calls


def test_tone_thresholds():
    assert tone_disk_pct(30) == "ok"
    assert tone_disk_pct(50) == "warn"
    assert tone_disk_pct(70) == "danger"
    assert tone_tls_days(40) == "ok"
    assert tone_tls_days(25) == "warn"
    assert tone_tls_days(10) == "danger"
    assert tone_restore_days(10) == "ok"
    assert tone_restore_days(100) == "warn"
    assert tone_restore_days(200) == "danger"


def test_tone_mail_auth_requires_dok_domain_in_marker():
    assert (
        tone_mail_auth(
            from_ok=True,
            marker_age_days=10,
            marker_domain="gmail.com",
            current_domain="gmail.com",
        )
        == "danger"
    )
    assert (
        tone_mail_auth(
            from_ok=True,
            marker_age_days=10,
            marker_domain="dok.moscow",
            current_domain="dok.moscow",
        )
        == "ok"
    )
    assert (
        tone_mail_auth(
            from_ok=True,
            marker_age_days=10,
            marker_domain="dok.moscow",
            current_domain="gmail.com",
        )
        == "danger"
    )
    # legacy маркер без from_domain
    assert (
        tone_mail_auth(
            from_ok=True,
            marker_age_days=10,
            marker_domain=None,
            current_domain="dok.moscow",
        )
        == "danger"
    )


def test_mail_auth_checklist_and_marker_domain(tmp_path, monkeypatch):
    from app.config import Settings
    from app.ops import mail_auth as ma

    monkeypatch.setattr(ma, "data_ops_dir", lambda settings=None: tmp_path)
    bad = Settings(smtp_from="Dok <user@gmail.com>")
    ok_settings = Settings(smtp_from="Dok <noreply@dok.moscow>")

    # 1) маркера нет
    ok, fact, *_ = ma.mail_auth_checklist(ok_settings)
    assert ok is False
    assert "маркера нет" in fact

    # 2) подтверждено для gmail.com
    ma.write_mail_auth_ok(checked_by="test", settings=bad)
    marker = ma.read_mail_auth_ok(bad)
    assert marker and marker.get("from_domain") == "gmail.com"
    assert marker.get("confirmed_at")
    ok, fact, *_ = ma.mail_auth_checklist(bad)
    assert ok is False
    assert "подтверждено для gmail.com" in fact

    # маркер gmail + текущий dok — всё ещё «подтверждено для gmail»
    ok, fact, *_ = ma.mail_auth_checklist(ok_settings)
    assert ok is False
    assert "подтверждено для gmail.com" in fact

    # 3) ok для dok.moscow
    ma.write_mail_auth_ok(checked_by="test", settings=ok_settings)
    assert ma.read_mail_auth_ok(ok_settings)["from_domain"] == "dok.moscow"
    assert ma.mail_auth_checklist(ok_settings)[0] is True

    # 4) домен изменился (маркер dok, From gmail)
    ok, fact, *_ = ma.mail_auth_checklist(bad)
    assert ok is False
    assert "домен изменился" in fact


def test_healthz_has_sha(app, monkeypatch):
    client, _dbmod = app
    monkeypatch.setenv("GIT_REVISION", "abcdef1234567890")
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data.get("sha") == "abcdef1234567890"
    assert data.get("revision") == "abcdef1234567890"


def test_smtp_from_ok(monkeypatch):
    from app.config import Settings

    ok_s = Settings(smtp_from="Dok.Moscow <noreply@dok.moscow>")
    bad_s = Settings(smtp_from="Dok <sudexpertpsy@gmail.com>")
    empty = Settings(smtp_from="")
    assert smtp_from_ok(ok_s)[0] is True
    assert smtp_from_ok(bad_s)[0] is False
    assert smtp_from_ok(empty)[0] is False


def test_disk_trend_gb_per_week(tmp_path, monkeypatch):
    ops = tmp_path / ".ops"
    ops.mkdir()
    lines = [
        '{"day":"2026-09-01","ts":"2026-09-01T00:00:00+00:00","key":"disk_used_gb","value_num":10.0,"value_text":null}\n',
        '{"day":"2026-09-08","ts":"2026-09-08T00:00:00+00:00","key":"disk_used_gb","value_num":11.0,"value_text":null}\n',
    ]
    (ops / "metrics.jsonl").write_text("".join(lines), encoding="utf-8")
    monkeypatch.setattr("app.ops.metrics.ops_dir", lambda: ops)
    trend = disk_trend_gb_per_week(days=30)
    assert trend is not None
    assert 0.9 <= trend <= 1.1


def test_backup_sh_excludes_imports_exports():
    text = Path(__file__).resolve().parents[2] / "deploy" / "backup.sh"
    src = text.read_text(encoding="utf-8")
    assert "*/imports" in src
    assert "*/exports" in src


def test_tar_excludes_drop_exports(tmp_path):
    """A.4: tar с теми же exclude не кладёт exports/ в архив."""
    import subprocess
    import tarfile

    root = tmp_path / "files"
    (root / "2" / "exports").mkdir(parents=True)
    (root / "2" / "imports").mkdir(parents=True)
    (root / "2" / "keep.txt").write_text("ok", encoding="utf-8")
    (root / "2" / "exports" / "x.zip").write_text("nope", encoding="utf-8")
    (root / "2" / "imports" / "y.xlsx").write_text("nope", encoding="utf-8")
    archive = tmp_path / "files.tar.gz"
    subprocess.run(
        [
            "tar",
            "-C",
            str(root),
            "--exclude=*/imports",
            "--exclude=*/imports/*",
            "--exclude=*/exports",
            "--exclude=*/exports/*",
            "-czf",
            str(archive),
            ".",
        ],
        check=True,
    )
    names = tarfile.open(archive, "r:gz").getnames()
    joined = "\n".join(names)
    assert "keep.txt" in joined
    assert "exports" not in joined
    assert "imports" not in joined


def test_deploy_sh_has_prune_keep_logic():
    src = (Path(__file__).resolve().parents[2] / "deploy" / "deploy.sh").read_text(
        encoding="utf-8"
    )
    assert "prune_old_dok_app_tags" in src
    assert "status=ok" in src
    assert "previous_ok_tag_from_log" in src
    assert "PRE_DEPLOY_TAG" in src
    assert "running_app_image_tag" in src
    assert "deploy_image_sha" in src
    assert "org.opencontainers.image.revision" in src
    assert "--rehearse" in src
    assert "migrate_rehearsal" in src
    assert 'd.get("sha")' in src or "d.get('sha')" in src
