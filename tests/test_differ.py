import io
import tarfile
from pathlib import Path
from unittest.mock import patch

from src.differ import (
    SourceDiffResult,
    build_metadata_abstract_diff,
    generate_source_diff,
    split_sentences,
)
from src.fetcher import Paper
from src.issue_manager import format_update_comment


def _make_paper(
    source_id: str = "2610.00714v1",
    title: str = "MANTA",
    abstract: str = "First sentence. Second sentence.",
    comment: str | None = None,
) -> Paper:
    return Paper(
        source_id=source_id,
        title=title,
        authors=["Alice", "Bob"],
        abstract=abstract,
        url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        categories=["cs.DC", "cs.OS"],
        primary_category="cs.DC",
        published="2026-09-30",
        updated="2026-11-20",
        comment=comment,
    )


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_split_sentences():
    assert split_sentences("Hello world.  Second line!\nThird?") == [
        "Hello world.",
        "Second line!",
        "Third?",
    ]


def test_build_metadata_abstract_diff_detects_changes():
    old_p = _make_paper("2610.00714v1", title="Old Title", abstract="A. B.", comment=None)
    new_p = _make_paper(
        "2610.00714v2",
        title="New Title",
        abstract="A. B revised. C.",
        comment="Accepted to EuroSys 2027",
    )
    diff = build_metadata_abstract_diff(old_p, new_p)
    assert "Title changed" in diff
    assert "- Old Title" in diff
    assert "+ New Title" in diff
    assert "Comment changed" in diff
    assert "Accepted to EuroSys 2027" in diff
    assert "Abstract diff" in diff
    assert "-B." in diff
    assert "+B revised." in diff


def test_build_metadata_abstract_diff_no_changes():
    p1 = _make_paper("2610.00714v1")
    p2 = _make_paper("2610.00714v2")
    assert "No metadata or abstract changes" in build_metadata_abstract_diff(p1, p2)


@patch("src.differ._download_eprint_bytes")
def test_generate_source_diff_tar_gz(mock_dl, tmp_path: Path):
    v1_tar = _make_tar_gz(
        {
            "main.tex": b"\\documentclass{article}\nOld line.\n",
            "fig.png": b"\x89PNG\r\n\x1a\nfakebinary",
        }
    )
    v2_tar = _make_tar_gz(
        {
            "main.tex": b"\\documentclass{article}\nNew line.\n",
            "fig.png": b"\x89PNG\r\n\x1a\nchangedbinary",
        }
    )
    mock_dl.side_effect = [v1_tar, v2_tar]

    out_dir = tmp_path / "diffs"
    res = generate_source_diff("2610.00714", 1, 2, out_dir, delay_seconds=0)
    assert res.available is True
    assert res.diff_file is not None
    assert res.diff_file.exists()
    assert "main.tex" in res.stat
    assert "fig.png" not in res.stat
    content = res.diff_file.read_text(encoding="utf-8")
    assert "-Old line." in content
    assert "+New line." in content


@patch("src.differ._download_eprint_bytes")
def test_generate_source_diff_pdf_only_fallback(mock_dl, tmp_path: Path):
    mock_dl.side_effect = [b"%PDF-1.5 fake pdf", b"%PDF-1.5 fake pdf v2"]
    res = generate_source_diff("2610.00714", 1, 2, tmp_path / "diffs", delay_seconds=0)
    assert res.available is False
    assert "PDF-only" in res.reason


def test_format_update_comment_with_diffs(tmp_path: Path):
    p = _make_paper("2610.00714v2", comment="Camera ready")
    src_diff = SourceDiffResult(
        available=True,
        stat=" main.tex | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)",
        diff_file=tmp_path / "2610.00714_v1_to_v2.diff",
    )
    comment = format_update_comment(
        p,
        old_version=1,
        metadata_diff="**Comment changed:**\n```diff\n- (none)\n+ Camera ready\n```",
        source_diff=src_diff,
        run_url="https://github.com/yzy1851169/arxiv-issue-bot/actions/runs/123",
    )
    assert "Metadata & Abstract Diff" in comment
    assert "TeX Source Diff" in comment
    assert "2610.00714_v1_to_v2.diff" in comment
    assert "Actions Artifacts" in comment
