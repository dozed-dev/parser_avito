from pathlib import Path

from parser.search_url_builder import AvitoSearchUrlBuilder, StructuredSearchRequest


class AvitoUrlConverter:
    """Facade over the shared public URL and structured search builder."""

    def __init__(
        self,
        cache_path: str | Path = "storage/avito_api_urls.json",
        min_request_interval: float = 31.0,
        timeout: int = 20,
    ):
        self._builder = AvitoSearchUrlBuilder(
            cache_path=cache_path,
            min_request_interval=min_request_interval,
            timeout=timeout,
        )

    def convert(self, url: str) -> str:
        """Convert supported public URLs locally without calling SPFA."""
        return self._builder.convert_public_url(url)

    def build_search_url(
        self,
        request: StructuredSearchRequest,
    ) -> tuple[str, dict[str, object]]:
        return self._builder.build(request)

    def convert_via_spfa(self, url: str) -> str:
        """Convert a public URL through the shared SPFA resolver."""
        return self._builder.convert_via_spfa(url)
