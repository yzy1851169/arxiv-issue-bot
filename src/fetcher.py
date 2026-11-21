from abc import ABC, abstractmethod
from dataclasses import dataclass
import arxiv


@dataclass
class Paper:
    source_id: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    pdf_url: str | None
    categories: list[str]
    primary_category: str
    published: str
    updated: str = ""
    comment: str | None = None


class PaperFetcher(ABC):
    @abstractmethod
    def fetch(self, category: str, max_results: int) -> list[Paper]:
        ...


class ArxivFetcher(PaperFetcher):
    def __init__(self) -> None:
        self.client = arxiv.Client()

    def fetch(self, category: str, max_results: int) -> list[Paper]:
        search = arxiv.Search(
            query=f"cat:{category}",
            max_results=max_results,
            sort_by=arxiv.SortCriterion.LastUpdatedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        papers: list[Paper] = []
        for result in self.client.results(search):
            updated_dt = getattr(result, "updated", None)
            updated_str = (
                updated_dt.strftime("%Y-%m-%d")
                if hasattr(updated_dt, "strftime")
                else ""
            )
            raw_comment = getattr(result, "comment", None)
            comment_str = raw_comment if isinstance(raw_comment, str) else None
            papers.append(
                Paper(
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
            )
        return papers
