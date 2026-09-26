"""Unit tests for progress calculation and session idempotency logic."""

from grimmlink_adapter.services.metadata_service import normalize_rating
from grimmlink_adapter.services.progress_service import calculate_display_percentage
from grimmlink_adapter.services.session_service import generate_session_idempotency_key


def test_progress_display_percentage_ratio() -> None:
    # Explicit invariant check from WORKFLOW_GEMINI_SOL.md:
    # 55 / 16653 ≈ 0.33%, not 33%
    pct = calculate_display_percentage(55, 16653)
    assert 0.33 <= pct <= 0.34
    assert pct != 33.0


def test_progress_display_percentage_boundaries() -> None:
    assert calculate_display_percentage(0, 100) == 0.0
    assert calculate_display_percentage(50, 100) == 50.0
    assert calculate_display_percentage(100, 100) == 100.0
    # Guard against divide by zero or negative
    assert calculate_display_percentage(10, 0) == 0.0
    assert calculate_display_percentage(-5, 100) == 0.0


def test_rating_normalization() -> None:
    # 10 scale to 5 scale
    assert normalize_rating(10.0, source_scale=10) == 5.0
    assert normalize_rating(8.0, source_scale=10) == 4.0
    assert normalize_rating(5.0, source_scale=10) == 2.5
    # 5 scale passthrough
    assert normalize_rating(4.5, source_scale=5) == 4.5


def test_session_idempotency_key_deterministic() -> None:
    key1 = generate_session_idempotency_key("alice", 42, "2026-09-26T10:00:00Z", "2026-09-26T10:30:00Z", "kobo1")
    key2 = generate_session_idempotency_key("alice", 42, "2026-09-26T10:00:00Z", "2026-09-26T10:30:00Z", "kobo1")
    key3 = generate_session_idempotency_key("bob", 42, "2026-09-26T10:00:00Z", "2026-09-26T10:30:00Z", "kobo1")

    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 64  # SHA-256 hex digest
