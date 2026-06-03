from abc import ABC, abstractmethod

from app.llm.types import AnalysisResult, PageData


class LLMClient(ABC):
    """Base interface for all LLM providers."""

    @abstractmethod
    async def analyze(self, page: PageData) -> AnalysisResult:
        """Analyze a page and return structured analysis."""
        ...

    @abstractmethod
    async def generate_script(self, analysis: AnalysisResult, url: str) -> str:
        """Generate a Playwright scraping script from analysis."""
        ...
