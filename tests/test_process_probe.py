from autocoder.core import process_probe as probe


def test_matches_execute_worker_line():
    line = "/usr/bin/python -m autocoder.cli --config config.yaml execute recABC"
    assert probe._matches(line, "recABC", "execute") is True


def test_matches_plan_worker_line():
    line = "/usr/bin/python -m autocoder.cli plan recXYZ"
    assert probe._matches(line, "recXYZ", "plan") is True


def test_no_match_wrong_record_id():
    line = "/usr/bin/python -m autocoder.cli execute recABC"
    assert probe._matches(line, "recOTHER", "execute") is False


def test_no_match_wrong_kind():
    line = "/usr/bin/python -m autocoder.cli plan recABC"
    assert probe._matches(line, "recABC", "execute") is False


def test_no_match_unrelated_line():
    line = "/usr/bin/python -m autocoder.cli dispatch-feishu"
    assert probe._matches(line, "recABC", "execute") is False
