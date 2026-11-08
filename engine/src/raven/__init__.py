"""Engine observability and analytics subsystem.

Provides structured event tracking, aggregation queries, and retention
management. Persists to ``obs_*`` tables in ``brain.sqlite``.

Public API::

    from src.raven import raven

    raven.track(TokenUsageEvent(...))
    raven.track_untyped("custom.event", {"key": "value"})
    rollup = raven.query(metric="tokens_used", window="5h")
    events = raven.events(since=1712840000.0, kind="token_usage")
"""
