import os
from pathlib import Path
import sys
import time

from src.config import load_config
from src.differ import (
    build_metadata_abstract_diff,
    fetch_paper_version,
    generate_source_diff,
)
from src.fetcher import ArxivFetcher, Paper
from src.filter import filter_papers
from src.issue_manager import (
    create_issue,
    fetch_existing_issues,
    normalize_arxiv_id,
    parse_arxiv_version,
    update_issue_for_new_version,
)


def _get_actions_run_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return None


def main() -> None:
    config = load_config()
    fetcher = ArxivFetcher()

    all_papers: dict[str, Paper] = {}
    for i, category in enumerate(config.categories):
        if i > 0:
            time.sleep(3)
        papers = fetcher.fetch(category, config.max_results_per_category)
        for p in papers:
            if not config.include_cross_listed and p.primary_category != category:
                continue
            base_id = normalize_arxiv_id(p.source_id)
            prev = all_papers.get(base_id)
            if prev is None or parse_arxiv_version(p.source_id) > parse_arxiv_version(
                prev.source_id
            ):
                all_papers[base_id] = p

    print(
        f"Fetched {len(all_papers)} unique papers "
        f"across {len(config.categories)} categories"
    )

    filtered = filter_papers(list(all_papers.values()), config.keywords)
    print(f"After keyword filtering: {len(filtered)} papers")

    existing_issues = fetch_existing_issues() if filtered else {}
    print(f"Existing posted papers in repository: {len(existing_issues)}")

    new_papers: list[Paper] = []
    updated_papers: list[tuple] = []
    for p in filtered:
        base_id = normalize_arxiv_id(p.source_id)
        existing = existing_issues.get(base_id)
        if existing is None:
            new_papers.append(p)
        elif parse_arxiv_version(p.source_id) > existing.version:
            updated_papers.append((existing, p))

    print(
        f"New papers: {len(new_papers)}, "
        f"Updated papers (newer version): {len(updated_papers)}"
    )

    created = 0
    for i, paper in enumerate(new_papers):
        if i > 0:
            time.sleep(1)
        if create_issue(paper, config.label_prefix):
            created += 1
            print(f"  Created: [{paper.source_id}] {paper.title}")
        else:
            print(
                f"  FAILED:  [{paper.source_id}] {paper.title}",
                file=sys.stderr,
            )

    run_url = _get_actions_run_url()
    diffs_dir = Path("diffs")
    updated = 0
    for i, (existing, paper) in enumerate(updated_papers):
        if i > 0 or created > 0:
            time.sleep(1)
        old_paper = fetch_paper_version(existing.arxiv_id, existing.version)
        metadata_diff = (
            build_metadata_abstract_diff(old_paper, paper) if old_paper else ""
        )
        source_diff = generate_source_diff(
            existing.arxiv_id,
            existing.version,
            parse_arxiv_version(paper.source_id),
            diffs_dir,
        )
        if update_issue_for_new_version(
            existing,
            paper,
            config.label_prefix,
            metadata_diff=metadata_diff,
            source_diff=source_diff,
            run_url=run_url,
        ):
            updated += 1
            print(
                f"  Updated: #{existing.number} [{paper.source_id}] {paper.title}"
            )
        else:
            print(
                f"  FAILED UPDATE: #{existing.number} [{paper.source_id}] {paper.title}",
                file=sys.stderr,
            )

    print(f"\nDone. Created {created} new issues, updated {updated} issues.")


if __name__ == "__main__":
    main()
