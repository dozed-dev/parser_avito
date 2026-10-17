"""Read-only MCP facade for Avito searches and listings."""


import asyncio
import json
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from load_config import load_avito_config
from parser.http.client import HttpClient
from parser.proxies.proxy_factory import build_proxy
from parser.search_url_builder import StructuredSearchRequest
from parser.url_converter import AvitoUrlConverter


MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

@asynccontextmanager
async def _lifespan(_server: FastMCP):
    try:
        yield {}
    finally:
        service.close()


mcp = FastMCP("Avito read-only", host=MCP_HOST, port=MCP_PORT, lifespan=_lifespan)


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


def _extract_search_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    core = payload.get("searchCore")
    resolved = None
    if isinstance(core, dict):
        resolved = {
            "query": core.get("query"),
            "category_id": core.get("categoryId"),
            "category_params": core.get("params") or {},
        }

    options: dict[tuple[int, str], dict[str, Any]] = {}

    def visit(nodes):
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            category_id = node.get("categoryId")
            if isinstance(category_id, int) and category_id > 0 and node.get("name"):
                params = node.get("params")
                params = params if isinstance(params, dict) else {}
                key = (category_id, json.dumps(params, sort_keys=True))
                selected = bool(node.get("isCurrent") or node.get("current"))
                if key in options:
                    options[key]["selected"] |= selected
                else:
                    options[key] = {
                        "category_id": category_id,
                        "name": node["name"],
                        "category_params": params,
                        "selected": selected,
                        "url": _absolute_avito_url(node.get("url")),
                    }
            visit(node.get("children"))
            visit(node.get("subs"))

    rubricators = payload.get("rubricators")
    if isinstance(rubricators, dict):
        side = rubricators.get("side")
        if isinstance(side, dict):
            visit(side.get("nodes"))
        visit(rubricators.get("rubricatorCategories"))
    return {"resolved_search": resolved, "category_options": list(options.values())}


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
            self._http = HttpClient(
                proxy=proxy,
                timeout=self._config.timeout,
                max_retries=self._config.max_count_of_retry,
                retry_delay=self._config.retry_delay,
                block_threshold=self._config.block_threshold,
                use_playwright=True,
            )
            return self._http


    def search(
        self,
        url: str | None,
        query: str | None,
        location: str,
        location_id: int | None,
        price_min: int | None,
        price_max: int | None,
        sort: str,
        page: int,
        with_delivery_only: bool,
        category_id: int | None,
        category_params: dict[str, int | str] | None,
    ) -> dict[str, Any]:
        if page < 1 or page > 100:
            raise ValueError("page должен быть от 1 до 100")

        if url:
            has_structured_filters = any(
                (
                    query,
                    location_id is not None,
                    location != "Россия",
                    price_min is not None,
                    price_max is not None,
                    sort != "relevance",
                    with_delivery_only,
                    category_id is not None,
                    bool(category_params),
                )
            )
            if has_structured_filters:
                raise ValueError("Нельзя одновременно передавать url и structured filters")
            if not _valid_avito_url(url):
                raise ValueError("url должен быть ссылкой на avito.ru")
            api_url = url if "/web/1/js/items" in url else self._converter.convert(url)
            filters = {"legacy_url": url}
        else:
            request = StructuredSearchRequest(
                query=query,
                location=location,
                location_id=location_id,
                price_min=price_min,
                price_max=price_max,
                sort=sort,
                page=page,
                with_delivery_only=with_delivery_only,
                category_id=category_id,
                category_params=category_params,
            )
            api_url, filters = self._converter.build_search_url(request)

        page_api_url = _api_url_for_page(api_url, page)
        response = self._get_http().request("GET", page_api_url)
        payload = response.json()
        items = [_item_summary(item) for item in _extract_catalog(payload)]
        return {
            "source_url": url,
            "api_url": page_api_url,
            "filters": filters,
            "page": page,
            "count": len(items),
            "items": items,
            **_extract_search_metadata(payload),
        }

    def get_ad(self, item_id: int, include_raw: bool) -> dict[str, Any]:
        if item_id <= 0:
            raise ValueError("item_id должен быть положительным числом")
        payload = self._get_http().fetch_item_data(item_id)
        success = payload.get("success")
        mobile = success.get("mobile") if isinstance(success, dict) else None
        if not isinstance(mobile, dict) or not mobile.get("title"):
            raise ValueError(f"Avito не вернул данные объявления {item_id}")
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

    async def run_sync(self, function: Callable[..., Any], *args: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, function, *args)

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


@mcp.tool()
async def search_ads(
    query: Annotated[str | None, "Текст поиска на Avito; можно не указывать для всех объявлений."] = None,
    category_id: Annotated[
        int | None,
        Field(
            gt=0,
            description="ТОЧНЫЙ category_id из category_options предыдущего поиска. Первый поиск всегда делайте БЕЗ категории; не угадывайте ID по названию или памяти.",
        ),
    ] = None,
    category_params: Annotated[
        dict[str, int | str] | None,
        Field(description="Точные category_params выбранного варианта category_options; копируйте без изменений вместе с category_id. До первого поиска не задавать."),
    ] = None,
    location: Annotated[str, "Полное название города или региона; локально: Россия, Москва, Санкт-Петербург; aliases не использовать."] = "Россия",
    location_id: Annotated[int | None, "Внутренний Avito locationId для другого региона."] = None,
    price_min: Annotated[int | None, "Минимальная цена в рублях."] = None,
    price_max: Annotated[int | None, "Максимальная цена в рублях."] = None,
    sort: Annotated[
        Literal["relevance", "newest", "price_asc", "price_desc"],
        "Сортировка результатов.",
    ] = "relevance",
    page: Annotated[int, "Номер страницы от 1 до 100."] = 1,
    with_delivery_only: Annotated[bool, "Только объявления с доставкой."] = False,
    url: Annotated[str | None, "Legacy: готовая ссылка Avito; не смешивать с фильтрами."] = None,
) -> dict[str, Any]:
    """Search Avito and return listings, resolved_search and category_options.

    Первый поиск выполняйте без category_id/category_params. Затем при необходимости
    повторите запрос с точной категорией из category_options, не придумывая категории.
    """
    return await service.run_sync(
        service.search,
        url,
        query,
        location,
        location_id,
        price_min,
        price_max,
        sort,
        page,
        with_delivery_only,
        category_id,
        category_params,
    )


@mcp.tool()
async def get_ad(item_id: int, include_raw: bool = False) -> dict[str, Any]:
    """Get one Avito listing by numeric ID, including image URLs."""
    return await service.run_sync(service.get_ad, item_id, include_raw)


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio or streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
