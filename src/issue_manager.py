from dataclasses import dataclass
import json
import re
import subprocess

from src.differ import SourceDiffResult
from src.fetcher import Paper

_ensured_labels: set[str] = set()


@dataclass
class ExistingIssue:
    number: int
    arxiv_id: str
    version: int
    state: str
    state_reason: str | None
    title: str


def normalize_arxiv_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def parse_arxiv_version(arxiv_id: str) -> int:
    match = re.search(r"v(\d+)$", arxiv_id)
    return int(match.group(1)) if match else 0


def extract_raw_arxiv_id_from_title(title: str) -> str | None:
    if title.startswith("[") and "]" in title:
        raw_id = title[1 : title.index("]")].strip()
        if raw_id:
            return raw_id
    return None


def extract_arxiv_id_from_title(title: str) -> str | None:
    raw_id = extract_raw_arxiv_id_from_title(title)
    return normalize_arxiv_id(raw_id) if raw_id else None


def fetch_existing_issues() -> dict[str, ExistingIssue]:
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "repos/{owner}/{repo}/issues?state=all&per_page=100",
            "--jq",
            ".[] | select(.pull_request == null) | {number, title, state, state_reason}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch existing issues for deduplication: {result.stderr.strip()}"
        )

    existing: dict[str, ExistingIssue] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        title = item.get("title", "")
        raw_id = extract_raw_arxiv_id_from_title(title)
        if not raw_id:
            continue
        base_id = normalize_arxiv_id(raw_id)
        issue = ExistingIssue(
            number=int(item["number"]),
            arxiv_id=base_id,
            version=parse_arxiv_version(raw_id),
            state=item.get("state", "open"),
            state_reason=item.get("state_reason"),
            title=title,
        )
        prev = existing.get(base_id)
        if prev is None or (issue.version, -issue.number) > (
            prev.version,
            -prev.number,
        ):
            existing[base_id] = issue
    return existing


def fetch_posted_arxiv_ids() -> set[str]:
    return set(fetch_existing_issues().keys())


def is_already_posted(
    arxiv_id: str, posted_ids: set[str] | None = None
) -> bool:
    if posted_ids is None:
        posted_ids = fetch_posted_arxiv_ids()
    return normalize_arxiv_id(arxiv_id) in posted_ids


def ensure_label_exists(label: str) -> None:
    if label in _ensured_labels:
        return
    result = subprocess.run(
        [
            "gh",
            "label",
            "create",
            label,
            "--description",
            f"arXiv category: {label}",
            "--force",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"ERROR creating label: {label}")
        print(f"STDERR: {result.stderr}")
    else:
        _ensured_labels.add(label)


def format_issue_body(paper: Paper) -> str:
    authors_str = ", ".join(paper.authors)
    categories_str = ", ".join(f"`{c}`" for c in paper.categories)
    links = f"[abs]({paper.url})"
    if paper.pdf_url:
        links += f" | [pdf]({paper.pdf_url})"
    meta_lines = [
        f"**Authors:** {authors_str}",
        f"**Published:** {paper.published}",
    ]
    if paper.updated and paper.updated != paper.published:
        meta_lines.append(f"**Updated:** {paper.updated}")
    meta_lines.append(f"**Categories:** {categories_str}")
    if paper.comment:
        meta_lines.append(f"**Comment:** {paper.comment}")
    meta_block = "\n".join(meta_lines)
    return (
        f"## {paper.title}\n\n"
        f"{meta_block}\n\n"
        f"**Links:** {links}\n\n"
        f"---\n\n"
        f"### Abstract\n\n"
        f"{paper.abstract}\n"
    )


def format_update_comment(
    paper: Paper,
    old_version: int,
    metadata_diff: str = "",
    source_diff: SourceDiffResult | None = None,
    run_url: str | None = None,
) -> str:
    new_version = parse_arxiv_version(paper.source_id)
    links = f"[abs]({paper.url})"
    if paper.pdf_url:
        links += f" | [pdf]({paper.pdf_url})"
    lines = [
        f"### New version detected: `{paper.source_id}` (`v{old_version}` -> `v{new_version}`)",
        "",
    ]
    if paper.updated:
        lines.append(f"**Updated:** {paper.updated}")
    if paper.comment:
        lines.append(f"**Comment:** {paper.comment}")
    lines.append(f"**Links:** {links}")
    lines.append("")

    if metadata_diff:
        lines.append("#### Metadata & Abstract Diff")
        lines.append("")
        lines.append(metadata_diff)
        lines.append("")

    if source_diff is not None:
        lines.append("#### TeX Source Diff")
        lines.append("")
        if source_diff.available:
            if source_diff.stat:
                lines.append("```text")
                lines.append(source_diff.stat)
                lines.append("```")
            else:
                lines.append("_No TeX source changes detected._")
            if source_diff.diff_file is not None:
                artifact_note = f"Full diff file: `{source_diff.diff_file.name}`"
                if run_url:
                    artifact_note += f" (download from [Actions Artifacts]({run_url}))"
                lines.append("")
                lines.append(artifact_note)
        else:
            lines.append(f"_TeX source diff unavailable: {source_diff.reason}_")
        lines.append("")

    lines.append("Issue title and body have been updated to the latest version.")
    return "\n".join(lines) + "\n"


def create_issue(paper: Paper, label_prefix: str) -> bool:
    title = f"[{paper.source_id}] {paper.title}"
    labels = [f"{label_prefix}:{cat}" for cat in paper.categories]

    for label in labels:
        ensure_label_exists(label)

    body = format_issue_body(paper)

    result = subprocess.run(
        [
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--label",
            ",".join(labels),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"ERROR creating issue: {title}")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        return False

    print(f"ISSUE CREATED: {result.stdout.strip()}")
    return True


def update_issue_for_new_version(
    existing: ExistingIssue,
    paper: Paper,
    label_prefix: str,
    metadata_diff: str = "",
    source_diff: SourceDiffResult | None = None,
    run_url: str | None = None,
) -> bool:
    title = f"[{paper.source_id}] {paper.title}"
    labels = [f"{label_prefix}:{cat}" for cat in paper.categories]

    for label in labels:
        ensure_label_exists(label)

    body = format_issue_body(paper)
    edit_cmd = [
        "gh",
        "issue",
        "edit",
        str(existing.number),
        "--title",
        title,
        "--body",
        body,
    ]
    if labels:
        edit_cmd.extend(["--add-label", ",".join(labels)])

    edit_result = subprocess.run(
        edit_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if edit_result.returncode != 0:
        print(f"ERROR editing issue #{existing.number}: {title}")
        print(f"STDERR: {edit_result.stderr}")
        return False

    comment_body = format_update_comment(
        paper,
        existing.version,
        metadata_diff=metadata_diff,
        source_diff=source_diff,
        run_url=run_url,
    )
    comment_result = subprocess.run(
        [
            "gh",
            "issue",
            "comment",
            str(existing.number),
            "--body",
            comment_body,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if comment_result.returncode != 0:
        print(f"ERROR commenting on issue #{existing.number}: {title}")
        print(f"STDERR: {comment_result.stderr}")
        return False

    # State transition rules:
    # - open (待定): stay open
    # - closed + completed (完成): reopen -> 待定
    # - closed + not_planned/other (关闭): stay closed
    if existing.state == "closed" and existing.state_reason == "completed":
        reopen_result = subprocess.run(
            ["gh", "issue", "reopen", str(existing.number)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if reopen_result.returncode != 0:
            print(f"ERROR reopening issue #{existing.number}: {title}")
            print(f"STDERR: {reopen_result.stderr}")
            return False

    print(
        f"ISSUE UPDATED: #{existing.number} "
        f"(v{existing.version} -> v{parse_arxiv_version(paper.source_id)})"
    )
    return True
