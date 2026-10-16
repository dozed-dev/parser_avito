"""Read-only MCP facade for Avito searches and listings."""


import asyncio
import atexit
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP

from get_cookies import get_profile_cookies
from load_config import load_avito_config
from parser.http.client import HttpClient
from parser.proxies.proxy_factory import build_proxy
from parser.url_converter import AvitoUrlConverter


MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

mcp = FastMCP("Avito read-only", host=MCP_HOST, port=MCP_PORT)


def _api_url_for_page(api_url: str, page: int) -> str:
    parts = urlsplit(api_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key not in {"p", "page"}]
    query.append(("page", str(page)))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _extract_catalog(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    candidates = [
        payload.get("catalog"),
        result.get("catalog") if isinstance(result, dict) else None,
        result,
        payload,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            return [item for item in candidate["items"] if isinstance(item, dict)]
    return []


def _image_urls(value: Any, image_context: bool = False) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            context = image_context or any(
                token in key.lower() for token in ("image", "photo", "gallery")
            )
            urls.extend(_image_urls(child, context))
    elif isinstance(value, list):
        for child in value:
            urls.extend(_image_urls(child, image_context))
    elif image_context and isinstance(value, str) and value.startswith(("http://", "https://")):
        urls.append(value)
    return list(dict.fromkeys(urls))


def _absolute_avito_url(path: Any) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"https://www.avito.ru{path}" if path.startswith("/") else None


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    price = item.get("priceDetailed")
    price = price if isinstance(price, dict) else {}
    location = item.get("location")
    location = location if isinstance(location, dict) else {}
    address = item.get("addressDetailed")
    address = address if isinstance(address, dict) else {}
    images = _image_urls(item)
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "description": item.get("description"),
        "price": price.get("value"),
        "price_text": price.get("string") or item.get("normalizedPrice"),
        "location": location.get("name"),
        "address": address.get("locationName"),
        "seller_id": item.get("sellerId"),
        "is_promotion": item.get("isPromotion", False),
        "url": _absolute_avito_url(item.get("urlPath")),
        "image_urls": images,
        "thumbnail_url": images[0] if images else None,
    }


def _valid_avito_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (
        host == "avito.ru" or host.endswith(".avito.ru")
    )


class AvitoMcpService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="avito-mcp")
        self._http: HttpClient | None = None
        self._converter = AvitoUrlConverter()
        self._config = load_avito_config()

    def _get_http(self) -> HttpClient:
        with self._lock:
            if self._http is not None:
                return self._http

            proxy = build_proxy(self._config)
            cookies = get_profile_cookies(
                timeout=self._config.timeout,
                proxy=proxy,
            )
            use_playwright = False
            self._http = HttpClient(
                proxy=proxy,
                cookies=cookies,
                timeout=self._config.timeout,
                max_retries=self._config.max_count_of_retry,
                retry_delay=self._config.retry_delay,
                block_threshold=self._config.block_threshold,
                use_playwright=use_playwright,
            )
            return self._http

    def search(self, url: str, page: int) -> dict[str, Any]:
        if not _valid_avito_url(url):
            raise ValueError("url должен быть ссылкой на avito.ru")
        if page < 1 or page > 100:
            raise ValueError("page должен быть от 1 до 100")

        api_url = (
            url
            if "/web/1/js/items" in url
            else self._converter.convert(url)
        )
        response = self._get_http().request(
            "GET",
            _api_url_for_page(api_url, page),
        )
        items = [_item_summary(item) for item in _extract_catalog(response.json())]
        return {"source_url": url, "page": page, "count": len(items), "items": items}

    def get_ad(self, item_id: int, include_raw: bool) -> dict[str, Any]:
        if item_id <= 0:
            raise ValueError("item_id должен быть положительным числом")
        payload = self._get_http().fetch_item_data(item_id)
        success = payload.get("success")
        success = success if isinstance(success, dict) else {}
        mobile = success.get("mobile")
        mobile = mobile if isinstance(mobile, dict) else {}
        detail_url = _absolute_avito_url(
            mobile.get("urlPath") or mobile.get("url")
        )
        result: dict[str, Any] = {
            "id": item_id,
            "title": mobile.get("title"),
            "description": mobile.get("description"),
            "image_urls": _image_urls(payload),
        }
        if detail_url:
            result["url"] = detail_url
        if include_raw:
            result["raw"] = payload
        return result

    async def run_search(self, url: str, page: int) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.search, url, page)

    async def run_get_ad(self, item_id: int, include_raw: bool) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            self.get_ad,
            item_id,
            include_raw,
        )

    def _close_sync(self) -> None:
        with self._lock:
            if self._http is not None:
                self._http.close()
                self._http = None

    def close(self) -> None:
        executor = self._executor
        self._executor = None
        if executor is None:
            return
        try:
            executor.submit(self._close_sync).result(timeout=30)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


service = AvitoMcpService()
atexit.register(service.close)


@mcp.tool()
async def search_ads(url: str, page: int = 1) -> dict[str, Any]:
    """Search an Avito public search URL and return listings with image URLs."""
    return await service.run_search(url, page)


@mcp.tool()
async def get_ad(item_id: int, include_raw: bool = False) -> dict[str, Any]:
    """Get one Avito listing by numeric ID, including image URLs."""
    return await service.run_get_ad(item_id, include_raw)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
