from dataclasses import dataclass
import difflib
import gzip
import io
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

import arxiv

from src.fetcher import Paper

TEX_EXTENSIONS = {".tex", ".bib", ".sty", ".cls", ".bst", ".txt", ".md"}
ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{version_id}"
USER_AGENT = "arxiv-issue-bot/1.0 (https://github.com/yzy1851169/arxiv-issue-bot)"


@dataclass
class SourceDiffResult:
    available: bool
    stat: str = ""
    diff_file: Path | None = None
    reason: str = ""


def fetch_paper_version(base_id: str, version: int) -> Paper | None:
    if version <= 0:
        return None
    version_id = f"{base_id}v{version}"
    try:
        client = arxiv.Client()
        search = arxiv.Search(id_list=[version_id])
        for result in client.results(search):
            updated_dt = getattr(result, "updated", None)
            updated_str = (
                updated_dt.strftime("%Y-%m-%d")
                if hasattr(updated_dt, "strftime")
                else ""
            )
            raw_comment = getattr(result, "comment", None)
            comment_str = raw_comment if isinstance(raw_comment, str) else None
            return Paper(
                source_id=result.get_short_id(),
                title=result.title,
                authors=[a.name for a in result.authors],
                abstract=result.summary,
                url=result.entry_id,
                pdf_url=result.pdf_url,
                categories=result.categories,
                primary_category=result.primary_category,
                published=result.published.strftime("%Y-%m-%d"),
                updated=updated_str,
                comment=comment_str,
            )
    except Exception as exc:
        print(f"WARNING: failed to fetch old version {version_id}: {exc}")
    return None


def split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]


def build_metadata_abstract_diff(old_paper: Paper, new_paper: Paper) -> str:
    sections: list[str] = []

    if old_paper.title.strip() != new_paper.title.strip():
        sections.append(
            "**Title changed:**\n```diff\n"
            f"- {old_paper.title.strip()}\n"
            f"+ {new_paper.title.strip()}\n"
            "```"
        )

    if old_paper.authors != new_paper.authors:
        sections.append(
            "**Authors changed:**\n```diff\n"
            f"- {', '.join(old_paper.authors)}\n"
            f"+ {', '.join(new_paper.authors)}\n"
            "```"
        )

    if old_paper.categories != new_paper.categories:
        sections.append(
            "**Categories changed:**\n```diff\n"
            f"- {', '.join(old_paper.categories)}\n"
            f"+ {', '.join(new_paper.categories)}\n"
            "```"
        )

    old_comment = (old_paper.comment or "").strip()
    new_comment = (new_paper.comment or "").strip()
    if old_comment != new_comment:
        sections.append(
            "**Comment changed:**\n```diff\n"
            f"- {old_comment or '(none)'}\n"
            f"+ {new_comment or '(none)'}\n"
            "```"
        )

    old_sents = split_sentences(old_paper.abstract)
    new_sents = split_sentences(new_paper.abstract)
    if old_sents != new_sents:
        diff_lines = list(
            difflib.unified_diff(
                old_sents,
                new_sents,
                fromfile=old_paper.source_id,
                tofile=new_paper.source_id,
                lineterm="",
                n=1,
            )
        )
        if diff_lines:
            diff_text = "\n".join(diff_lines)
            sections.append(f"**Abstract diff:**\n```diff\n{diff_text}\n```")

    if not sections:
        return "_No metadata or abstract changes detected._"
    return "\n\n".join(sections)


def _download_eprint_bytes(version_id: str, timeout: int = 30) -> bytes:
    url = ARXIV_EPRINT_URL.format(version_id=version_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_tex_files(raw_bytes: bytes, dest_dir: Path) -> bool:
    if raw_bytes.startswith(b"%PDF-"):
        return False

    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0

    # Try tar / tar.gz archive first
    try:
        with tarfile.open(fileobj=io.BytesIO(raw_bytes), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                rel = Path(member.name)
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                if rel.suffix.lower() not in TEX_EXTENSIONS:
                    continue
                target = dest_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                src_f = tf.extractfile(member)
                if src_f is None:
                    continue
                target.write_bytes(src_f.read())
                extracted += 1
        if extracted > 0:
            return True
    except tarfile.TarError:
        pass

    # Try single gzipped .tex file
    try:
        decompressed = gzip.decompress(raw_bytes)
        if not decompressed.startswith(b"%PDF-"):
            text = decompressed.decode("utf-8", errors="replace")
            if "\\documentclass" in text or "\\begin{document}" in text:
                (dest_dir / "main.tex").write_text(text, encoding="utf-8")
                return True
    except OSError:
        pass

    # Try plain .tex file
    text = raw_bytes.decode("utf-8", errors="replace")
    if "\\documentclass" in text or "\\begin{document}" in text:
        (dest_dir / "main.tex").write_text(text, encoding="utf-8")
        return True

    return False


def generate_source_diff(
    base_id: str,
    old_version: int,
    new_version: int,
    output_dir: Path,
    delay_seconds: float = 3.0,
) -> SourceDiffResult:
    if old_version <= 0 or new_version <= old_version:
        return SourceDiffResult(available=False, reason="Invalid version range")

    old_id = f"{base_id}v{old_version}"
    new_id = f"{base_id}v{new_version}"

    try:
        old_bytes = _download_eprint_bytes(old_id)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        new_bytes = _download_eprint_bytes(new_id)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return SourceDiffResult(
            available=False,
            reason=f"Failed to download arXiv source: {exc}",
        )

    with tempfile.TemporaryDirectory(prefix="arxiv_diff_") as tmp:
        tmp_path = Path(tmp)
        old_dir = tmp_path / f"v{old_version}"
        new_dir = tmp_path / f"v{new_version}"

        if not _extract_tex_files(old_bytes, old_dir):
            return SourceDiffResult(
                available=False,
                reason=f"No TeX source available for {old_id} (PDF-only)",
            )
        if not _extract_tex_files(new_bytes, new_dir):
            return SourceDiffResult(
                available=False,
                reason=f"No TeX source available for {new_id} (PDF-only)",
            )

        stat_proc = subprocess.run(
            ["git", "diff", "--no-index", "--stat", "--", f"v{old_version}", f"v{new_version}"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if stat_proc.returncode not in (0, 1):
            return SourceDiffResult(
                available=False,
                reason=f"git diff --stat failed: {stat_proc.stderr.strip()}",
            )

        diff_proc = subprocess.run(
            ["git", "diff", "--no-index", "--", f"v{old_version}", f"v{new_version}"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if diff_proc.returncode not in (0, 1):
            return SourceDiffResult(
                available=False,
                reason=f"git diff failed: {diff_proc.stderr.strip()}",
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        diff_file = output_dir / f"{base_id}_v{old_version}_to_v{new_version}.diff"
        diff_content = diff_proc.stdout
        if not diff_content.strip():
            diff_content = f"# No TeX source differences between {old_id} and {new_id}\n"
        diff_file.write_text(diff_content, encoding="utf-8")

        return SourceDiffResult(
            available=True,
            stat=stat_proc.stdout.strip(),
            diff_file=diff_file,
        )
