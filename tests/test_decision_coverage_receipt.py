from run_searches import _decision_coverage_receipt


def _notices():
    return [
        {"source_id": "notice-a"},
        {"source_id": "notice-b"},
        {"source_id": "notice-c"},
    ]


def test_decision_coverage_requires_one_valid_disposition_per_notice():
    receipt = _decision_coverage_receipt(
        _notices(),
        {
            "notice-a": {"verdict": "pursue"},
            "notice-b": {"verdict": "monitor"},
            "notice-c": {"verdict": "discard"},
        },
        prefilter_receipt={"complete": True},
    )

    assert receipt["verdict"] == "complete"
    assert receipt["complete"] is True
    assert receipt["candidate_census"] == 3
    assert receipt["dispositioned"] == 3
    assert receipt["counts"] == {
        "pursue": 1,
        "monitor": 1,
        "discard": 1,
    }


def test_decision_coverage_fails_closed_for_unscreened_or_missing_rows():
    receipt = _decision_coverage_receipt(
        _notices(),
        {
            "notice-a": {"verdict": "pursue"},
            "notice-b": {"verdict": "unscreened"},
        },
        prefilter_receipt={"complete": True},
    )

    assert receipt["verdict"] == "incomplete"
    assert receipt["complete"] is False
    assert receipt["missing_count"] == 1
    assert receipt["invalid_or_unscreened_count"] == 1
    assert receipt["missing_examples"] == ["notice-c"]
    assert receipt["invalid_or_unscreened_examples"] == ["notice-b"]


def test_decision_coverage_rejects_unknown_and_duplicate_notice_ids():
    notices = [
        {"source_id": "notice-a"},
        {"source_id": "notice-a"},
    ]
    receipt = _decision_coverage_receipt(
        notices,
        {
            "notice-a": {"verdict": "discard"},
            "not-in-census": {"verdict": "monitor"},
        },
        prefilter_receipt={"complete": False},
    )

    assert receipt["complete"] is False
    assert receipt["duplicate_notice_id_count"] == 1
    assert receipt["unexpected_examples"] == ["not-in-census"]
    assert receipt["prefilter_complete"] is False
