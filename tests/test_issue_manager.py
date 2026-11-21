from unittest.mock import patch, MagicMock
import pytest

from src.fetcher import Paper
from src.issue_manager import (
    normalize_arxiv_id,
    extract_arxiv_id_from_title,
    fetch_posted_arxiv_ids,
    is_already_posted,
    format_issue_body,
)


def test_normalize_arxiv_id_removes_version():
    assert normalize_arxiv_id("2401.12345v1") == "2401.12345"
    assert normalize_arxiv_id("2401.12345v2") == "2401.12345"
    assert normalize_arxiv_id("2401.12345v10") == "2401.12345"


def test_normalize_arxiv_id_no_version():
    assert normalize_arxiv_id("2401.12345") == "2401.12345"


def test_extract_arxiv_id_from_title():
    assert extract_arxiv_id_from_title("[2401.12345v1] Test Paper") == "2401.12345"
    assert extract_arxiv_id_from_title("[2401.12345v12] Another") == "2401.12345"
    assert extract_arxiv_id_from_title("No bracket title") is None
    assert extract_arxiv_id_from_title("[] Empty bracket") is None


def _make_paper() -> Paper:
    return Paper(
        source_id="2401.12345v1",
        title="Test Paper",
        authors=["Alice", "Bob"],
        abstract="This is a test abstract.",
        url="https://arxiv.org/abs/2401.12345v1",
        pdf_url="https://arxiv.org/pdf/2401.12345v1",
        categories=["cs.AI", "cs.SE"],
        primary_category="cs.AI",
        published="2024-01-15",
    )


@patch("src.issue_manager.subprocess.run")
def test_fetch_posted_arxiv_ids_success(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="[2401.12345v1] Test Paper\n[2610.00714v2] MANTA\nUnrelated issue\n",
        stderr="",
    )
    posted = fetch_posted_arxiv_ids()
    assert posted == {"2401.12345", "2610.00714"}


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
def test_is_already_posted_fetches_when_not_provided(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="[2401.12345v1] Test Paper\n",
        stderr="",
    )
    assert is_already_posted("2401.12345v2") is True
    assert is_already_posted("2401.99999v1") is False


def test_format_issue_body_contains_key_info():
    paper = _make_paper()
    body = format_issue_body(paper)
    assert "Test Paper" in body
    assert "Alice" in body
    assert "Bob" in body
    assert "2024-01-15" in body
    assert "`cs.AI`" in body
    assert "[abs]" in body
    assert "[pdf]" in body
    assert "This is a test abstract." in body
