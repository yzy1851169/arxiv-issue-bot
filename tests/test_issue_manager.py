import json
from unittest.mock import patch, MagicMock
import pytest

from src.fetcher import Paper
from src.issue_manager import (
    ExistingIssue,
    normalize_arxiv_id,
    parse_arxiv_version,
    extract_arxiv_id_from_title,
    fetch_existing_issues,
    fetch_posted_arxiv_ids,
    is_already_posted,
    format_issue_body,
    format_update_comment,
    update_issue_for_new_version,
    _ensured_labels,
)


def test_normalize_arxiv_id_removes_version():
    assert normalize_arxiv_id("2401.12345v1") == "2401.12345"
    assert normalize_arxiv_id("2401.12345v2") == "2401.12345"
    assert normalize_arxiv_id("2401.12345v10") == "2401.12345"


def test_normalize_arxiv_id_no_version():
    assert normalize_arxiv_id("2401.12345") == "2401.12345"


def test_parse_arxiv_version():
    assert parse_arxiv_version("2401.12345v1") == 1
    assert parse_arxiv_version("2401.12345v12") == 12
    assert parse_arxiv_version("2401.12345") == 0


def test_extract_arxiv_id_from_title():
    assert extract_arxiv_id_from_title("[2401.12345v1] Test Paper") == "2401.12345"
    assert extract_arxiv_id_from_title("[2401.12345v12] Another") == "2401.12345"
    assert extract_arxiv_id_from_title("No bracket title") is None
    assert extract_arxiv_id_from_title("[] Empty bracket") is None


def _make_paper(source_id: str = "2401.12345v1") -> Paper:
    return Paper(
        source_id=source_id,
        title="Test Paper",
        authors=["Alice", "Bob"],
        abstract="This is a test abstract.",
        url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        categories=["cs.AI", "cs.SE"],
        primary_category="cs.AI",
        published="2024-01-15",
        updated="2024-02-01",
        comment="Accepted to EuroSys",
    )


@patch("src.issue_manager.subprocess.run")
def test_fetch_existing_issues_success(mock_run):
    lines = [
        json.dumps(
            {
                "number": 10,
                "title": "[2401.12345v1] Test Paper",
                "state": "open",
                "state_reason": None,
            }
        ),
        json.dumps(
            {
                "number": 20,
                "title": "[2610.00714v2] MANTA",
                "state": "closed",
                "state_reason": "completed",
            }
        ),
        json.dumps(
            {
                "number": 30,
                "title": "Unrelated issue",
                "state": "open",
                "state_reason": None,
            }
        ),
    ]
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="\n".join(lines) + "\n",
        stderr="",
    )
    existing = fetch_existing_issues()
    assert set(existing.keys()) == {"2401.12345", "2610.00714"}
    assert existing["2401.12345"].version == 1
    assert existing["2401.12345"].state == "open"
    assert existing["2610.00714"].version == 2
    assert existing["2610.00714"].state == "closed"
    assert existing["2610.00714"].state_reason == "completed"


@patch("src.issue_manager.subprocess.run")
def test_fetch_posted_arxiv_ids_gh_failure_raises(mock_run):
    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="HTTP 403: API rate limit exceeded",
    )
    with pytest.raises(RuntimeError, match="Failed to fetch existing issues"):
        fetch_posted_arxiv_ids()


def test_is_already_posted_with_cached_set():
    posted = {"2401.12345", "2610.00714"}
    assert is_already_posted("2401.12345v2", posted) is True
    assert is_already_posted("2610.00714v1", posted) is True
    assert is_already_posted("2401.99999v1", posted) is False


@patch("src.issue_manager.subprocess.run")
def test_update_issue_open_stays_open(mock_run):
    _ensured_labels.clear()
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    existing = ExistingIssue(
        number=10,
        arxiv_id="2401.12345",
        version=1,
        state="open",
        state_reason=None,
        title="[2401.12345v1] Test Paper",
    )
    assert update_issue_for_new_version(existing, _make_paper("2401.12345v2"), "arxiv") is True
    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert any(cmd[:4] == ["gh", "issue", "edit", "10"] for cmd in cmds)
    assert any(cmd[:4] == ["gh", "issue", "comment", "10"] for cmd in cmds)
    assert not any(cmd[:3] == ["gh", "issue", "reopen"] for cmd in cmds)


@patch("src.issue_manager.subprocess.run")
def test_update_issue_completed_reopens(mock_run):
    _ensured_labels.clear()
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    existing = ExistingIssue(
        number=11,
        arxiv_id="2401.12345",
        version=1,
        state="closed",
        state_reason="completed",
        title="[2401.12345v1] Test Paper",
    )
    assert update_issue_for_new_version(existing, _make_paper("2401.12345v2"), "arxiv") is True
    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert any(cmd[:4] == ["gh", "issue", "edit", "11"] for cmd in cmds)
    assert any(cmd[:4] == ["gh", "issue", "comment", "11"] for cmd in cmds)
    assert any(cmd[:4] == ["gh", "issue", "reopen", "11"] for cmd in cmds)


@patch("src.issue_manager.subprocess.run")
def test_update_issue_not_planned_stays_closed(mock_run):
    _ensured_labels.clear()
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    existing = ExistingIssue(
        number=12,
        arxiv_id="2401.12345",
        version=1,
        state="closed",
        state_reason="not_planned",
        title="[2401.12345v1] Test Paper",
    )
    assert update_issue_for_new_version(existing, _make_paper("2401.12345v2"), "arxiv") is True
    cmds = [call.args[0] for call in mock_run.call_args_list]
    assert any(cmd[:4] == ["gh", "issue", "edit", "12"] for cmd in cmds)
    assert any(cmd[:4] == ["gh", "issue", "comment", "12"] for cmd in cmds)
    assert not any(cmd[:3] == ["gh", "issue", "reopen"] for cmd in cmds)


def test_format_issue_body_contains_key_info():
    paper = _make_paper()
    body = format_issue_body(paper)
    assert "Test Paper" in body
    assert "Alice" in body
    assert "Bob" in body
    assert "2024-01-15" in body
    assert "2024-02-01" in body
    assert "Accepted to EuroSys" in body
    assert "`cs.AI`" in body
    assert "[abs]" in body
    assert "[pdf]" in body
    assert "This is a test abstract." in body


def test_format_update_comment():
    paper = _make_paper("2401.12345v2")
    comment = format_update_comment(paper, old_version=1)
    assert "2401.12345v2" in comment
    assert "`v1` -> `v2`" in comment
    assert "2024-02-01" in comment
    assert "Accepted to EuroSys" in comment
