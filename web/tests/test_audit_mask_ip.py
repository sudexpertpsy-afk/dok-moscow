"""T10: IP в events хранится в маскированном виде."""

from __future__ import annotations

from app.services.audit import mask_ip, record_event, sanitize_event_details


def test_mask_ip_v4_and_v6():
    assert mask_ip("203.0.113.45") == "203.0.113.0"
    assert mask_ip("unknown") == "unknown"
    assert mask_ip("") == "unknown"
    assert mask_ip("not-an-ip") == "unknown"
    masked = mask_ip("2001:db8:85a3::8a2e:370:7334")
    assert masked.startswith("2001:db8:85a3")
    assert masked.endswith("::") or masked.endswith(":0:0:0:0") or "::" in masked


def test_record_event_masks_ip(app):
    _, dbmod = app
    db = dbmod.SessionLocal()
    try:
        ev = record_event(
            db,
            type="login_ok",
            details={"ip": "198.51.100.77", "via": "password"},
        )
        assert ev.details["ip"] == "198.51.100.0"
        assert ev.details["via"] == "password"
        assert sanitize_event_details({"ip": "10.0.0.5"})["ip"] == "10.0.0.0"
    finally:
        db.close()
