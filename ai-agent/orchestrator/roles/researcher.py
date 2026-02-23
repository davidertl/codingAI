from core.research import research
from orchestrator.contracts import ResearchBrief, ResearchSource


def build_research_brief(query: str, *, max_results: int = 6) -> ResearchBrief:
    data = research(query, max_results=max_results, use_cache=True)
    sources = []
    for item in data.get("results", [])[:max_results]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        sources.append(
            ResearchSource(
                title=str(item.get("title") or "").strip(),
                url=url,
                snippet=str(item.get("snippet") or "").strip(),
            )
        )
    assumptions = []
    if data.get("search_error"):
        assumptions.append("Search index may be incomplete due to search error.")
    if data.get("summary_error"):
        assumptions.append("Summary model unavailable; using raw snippets only.")
    return ResearchBrief(
        sources=sources,
        technical_summary=str(data.get("summary") or "").strip(),
        assumptions=assumptions,
        uncertainty_notes=[],
    )
