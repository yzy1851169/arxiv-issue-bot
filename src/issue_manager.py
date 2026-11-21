import re
import subprocess

from src.fetcher import Paper

_ensured_labels: set[str] = set()


def normalize_arxiv_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def extract_arxiv_id_from_title(title: str) -> str | None:
    if title.startswith("[") and "]" in title:
        raw_id = title[1 : title.index("]")].strip()
        if raw_id:
            return normalize_arxiv_id(raw_id)
    return None


def fetch_posted_arxiv_ids() -> set[str]:
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            "repos/{owner}/{repo}/issues?state=all&per_page=100",
            "--jq",
            ".[] | select(.pull_request == null) | .title",
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

    posted_ids: set[str] = set()
    for line in result.stdout.splitlines():
        arxiv_id = extract_arxiv_id_from_title(line.strip())
        if arxiv_id:
            posted_ids.add(arxiv_id)
    return posted_ids


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
    return (
        f"## {paper.title}\n\n"
        f"**Authors:** {authors_str}\n"
        f"**Published:** {paper.published}\n"
        f"**Categories:** {categories_str}\n\n"
        f"**Links:** {links}\n\n"
        f"---\n\n"
        f"### Abstract\n\n"
        f"{paper.abstract}\n"
    )


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
