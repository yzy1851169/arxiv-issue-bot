import sys
import time

from src.config import load_config
from src.fetcher import ArxivFetcher, Paper
from src.filter import filter_papers
from src.issue_manager import (
    create_issue,
    fetch_existing_issues,
    normalize_arxiv_id,
    parse_arxiv_version,
    update_issue_for_new_version,
)


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

    updated = 0
    for i, (existing, paper) in enumerate(updated_papers):
        if i > 0 or created > 0:
            time.sleep(1)
        if update_issue_for_new_version(existing, paper, config.label_prefix):
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
