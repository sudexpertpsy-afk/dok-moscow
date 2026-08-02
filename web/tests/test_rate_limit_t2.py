"""T2: rate limit в БД — общее состояние для «реплик» (разные экземпляры)."""

from __future__ import annotations

from app.rate_limit import LoginRateLimiter


def test_db_limiter_shared_across_instances(app):
    _client, _dbmod = app
    a = LoginRateLimiter(limit=3, window_sec=60, name="t2_test")
    b = LoginRateLimiter(limit=3, window_sec=60, name="t2_test")
    a.clear()

    key = "10.0.0.1"
    assert not a.is_blocked(key)
    a.register_failure(key)
    a.register_failure(key)
    assert not b.is_blocked(key)
    b.register_failure(key)
    assert a.is_blocked(key)
    assert b.is_blocked(key)

    a.reset(key)
    assert not b.is_blocked(key)
    a.clear()


def test_buckets_are_isolated(app):
    _client, _dbmod = app
    login = LoginRateLimiter(limit=2, window_sec=60, name="t2_bucket_a")
    other = LoginRateLimiter(limit=2, window_sec=60, name="t2_bucket_b")
    login.clear()
    other.clear()

    key = "same-key"
    login.register_failure(key)
    login.register_failure(key)
    assert login.is_blocked(key)
    assert not other.is_blocked(key)
    login.clear()
    other.clear()
