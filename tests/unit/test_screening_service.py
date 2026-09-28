"""Unit tests for src/services/screening_service.py."""

import pytest

from src.services.screening_service import (
    ResumeScreeningService,
    extract_candidate_id,
    extract_raw_text,
)


@pytest.fixture
def service():
    return ResumeScreeningService()


def test_extract_candidate_id_prefers_explicit_id():
    assert extract_candidate_id({"id": "cand-42"}, 0) == "cand-42"
    assert extract_candidate_id({"candidate_id": "cand-7"}, 0) == "cand-7"


def test_extract_candidate_id_falls_back_to_positional_placeholder():
    assert extract_candidate_id({}, 0) == "candidate-1"
    assert extract_candidate_id("not-a-dict", 3) == "candidate-4"


def test_extract_raw_text_supports_both_key_names():
    assert extract_raw_text({"text": "abc"}) == "abc"
    assert extract_raw_text({"resume_text": "xyz"}) == "xyz"
    assert extract_raw_text({}) is None
    assert extract_raw_text("nope") is None


def test_get_next_candidate_unavailable_for_unregistered_session(service):
    result = service.get_next_candidate("no-such-session")
    assert result["status"] == "unavailable"


def test_fetch_sequence_and_exhaustion(service):
    service.register_batch(
        "sess-1",
        "JD",
        [
            {"candidate_id": "c1", "redacted_text": "t1", "protected_signal_categories": []},
            {"candidate_id": "c2", "redacted_text": "t2", "protected_signal_categories": ["age_proxy"]},
        ],
    )
    first = service.get_next_candidate("sess-1")
    assert first["status"] == "ok"
    assert first["candidate_id"] == "c1"
    assert first["evidence_text"] == "t1"

    second = service.get_next_candidate("sess-1")
    assert second["candidate_id"] == "c2"
    assert second["protected_signal_categories"] == ["age_proxy"]

    exhausted = service.get_next_candidate("sess-1")
    assert exhausted["status"] == "no_more_resumes"


def test_ground_truth_signal_only_available_after_serving(service):
    service.register_batch(
        "sess-2", "JD",
        [{"candidate_id": "c1", "redacted_text": "t1", "protected_signal_categories": ["gender_proxy"]}],
    )
    assert service.ground_truth_signal("sess-2", "c1") == []  # not served yet
    service.get_next_candidate("sess-2")
    assert service.ground_truth_signal("sess-2", "c1") == ["gender_proxy"]


def test_is_known_candidate(service):
    service.register_batch(
        "sess-3", "JD",
        [{"candidate_id": "c1", "redacted_text": "t1", "protected_signal_categories": []}],
    )
    assert service.is_known_candidate("sess-3", "c1") is False
    service.get_next_candidate("sess-3")
    assert service.is_known_candidate("sess-3", "c1") is True
    assert service.is_known_candidate("sess-3", "unknown") is False


def test_all_candidate_ids_returns_full_queue_regardless_of_serve_state(service):
    service.register_batch(
        "sess-4", "JD",
        [
            {"candidate_id": "c1", "redacted_text": "t1", "protected_signal_categories": []},
            {"candidate_id": "c2", "redacted_text": "t2", "protected_signal_categories": []},
        ],
    )
    assert service.all_candidate_ids("sess-4") == ["c1", "c2"]
    service.get_next_candidate("sess-4")
    assert service.all_candidate_ids("sess-4") == ["c1", "c2"]


def test_clear_session_removes_all_cached_data(service):
    service.register_batch(
        "sess-5", "JD",
        [{"candidate_id": "c1", "redacted_text": "t1", "protected_signal_categories": []}],
    )
    service.clear_session("sess-5")
    assert service.get_next_candidate("sess-5")["status"] == "unavailable"
    assert service.all_candidate_ids("sess-5") == []
