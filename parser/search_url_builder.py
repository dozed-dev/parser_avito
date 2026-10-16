"""Build Avito API URLs from structured search requests."""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests
from loguru import logger


SPFA_AVITO_URL_ENDPOINT = "https://spfa.pro/api/avito-url/"

# The key is the exact location name expected from the MCP model. The slug and
# ID are kept together so public URLs and structured requests share one source.
LOCAL_LOCATIONS = {
    "Россия": {"slug": "rossiya", "location_id": 621540},
    "Москва": {"slug": "moskva", "location_id": 637640},
    "Санкт-Петербург": {"slug": "sankt-peterburg", "location_id": 653240},
}
SORT_VALUES = {
    "relevance": "default",
    "newest": "date",
    "price_asc": "price",
    "price_desc": "price_desc",
}


@dataclass(frozen=True)
class StructuredSearchRequest:
    query: str | None = None
    location: str = "Россия"
    location_id: int | None = None
    price_min: int | None = None
    price_max: int | None = None
    sort: str = "relevance"
    page: int = 1
    with_delivery_only: bool = False

    def filters(self, resolved_location_id: int) -> dict[str, object]:
        return {
            "query": self.query.strip() if self.query else None,
            "location": self.location,
            "location_id": resolved_location_id,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "sort": self.sort,
            "with_delivery_only": self.with_delivery_only,
            "page": self.page,
        }


class SpfaUrlResolver:
    """Resolve public Avito URLs through SPFA and persist the result cache."""

    _request_lock = Lock()
    _last_request_at = 0.0

    def __init__(
        self,
        cache_path: str | Path = "storage/avito_api_urls.json",
        min_request_interval: float = 31.0,
        timeout: int = 20,
    ):
        self.cache_path = Path(cache_path)
        self.min_request_interval = min_request_interval
        self.timeout = timeout
        self._cache = self._load_cache()

    def cached(self, url: str) -> str | None:
        return self._cache.get(url)

    def store(self, source_url: str, api_url: str) -> None:
        self._cache[source_url] = api_url
        self._save_cache()

    def convert(self, url: str) -> str:
        cached_url = self.cached(url)
        if cached_url:
            logger.debug(f"API URL загружен из кэша: {url}")
            return cached_url

        with self._request_lock:
            self._cache.update(self._load_cache())
            cached_url = self.cached(url)
            if cached_url:
                return cached_url

            self._wait_for_rate_limit()
            response = requests.post(
                SPFA_AVITO_URL_ENDPOINT,
                json={"url": url},
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            type(self)._last_request_at = time.monotonic()

            if response.status_code != 200:
                raise RuntimeError(self._format_error(response))

            try:
                payload = response.json()
            except ValueError as error:
                raise RuntimeError(
                    "SPFA вернул некорректный JSON при преобразовании URL"
                ) from error

            api_url = payload.get("api_url")
            if not payload.get("success") or not api_url:
                raise RuntimeError("SPFA не вернул api_url для ссылки Avito")

            self.store(url, api_url)
            logger.info(f"Ссылка Avito преобразована в API URL: {url}")
            return api_url

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - type(self)._last_request_at
        delay = self.min_request_interval - elapsed
        if type(self)._last_request_at and delay > 0:
            logger.info(
                f"Ожидание {delay:.1f} сек. перед следующим запросом к SPFA"
            )
            time.sleep(delay)

    @staticmethod
    def _format_error(response) -> str:
        messages = {
            400: "SPFA отклонил ссылку: некорректный JSON или URL не относится к Avito",
            429: "Превышен лимит SPFA: не более 2 преобразований URL в минуту",
            502: "SPFA не смог получить URI от Avito",
            503: "У SPFA серверная ошибка",
        }
        return messages.get(
            response.status_code,
            f"SPFA вернул неожиданный HTTP-код {response.status_code}",
        )

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}

        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("cache root must be an object")
            return {
                str(source_url): str(api_url)
                for source_url, api_url in data.items()
                if source_url and api_url
            }
        except (OSError, ValueError, TypeError) as error:
            logger.warning(f"Не удалось загрузить кэш API URL: {error}")
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.cache_path)


class AvitoSearchUrlBuilder:
    """Build API URLs and resolve unknown locations through SPFA."""

    def __init__(
        self,
        cache_path: str | Path = "storage/avito_api_urls.json",
        min_request_interval: float = 31.0,
        timeout: int = 20,
    ):
        self._spfa = SpfaUrlResolver(cache_path, min_request_interval, timeout)
        self._resolved_location_ids: dict[str, int] = {}

    def build(
        self,
        request: StructuredSearchRequest,
    ) -> tuple[str, dict[str, object]]:
        self._validate(request)
        resolved_location_id = self.resolve_location_id(
            request.location,
            request.location_id,
        )
        params: list[tuple[str, str | int]] = [
            ("localPriority", 0),
            ("locationId", resolved_location_id),
            ("presentationType", "serp"),
            ("sort", SORT_VALUES[request.sort]),
            ("page", request.page),
        ]
        if request.query and request.query.strip():
            params.append(("query", request.query.strip()))
        if request.price_min is not None:
            params.append(("pmin", request.price_min))
        if request.price_max is not None:
            params.append(("pmax", request.price_max))
        if request.with_delivery_only:
            params.append(("cd", 1))

        api_url = "https://www.avito.ru/web/1/js/items?" + urlencode(params)
        return api_url, request.filters(resolved_location_id)

    def convert_public_url(self, url: str) -> str:
        """Build simple public search URLs without calling SPFA."""
        cached_url = self._spfa.cached(url)
        if cached_url:
            logger.debug(f"API URL загружен из кэша: {url}")
            return cached_url

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host not in {"avito.ru", "www.avito.ru"}:
            raise ValueError("Ссылка должна быть на avito.ru")
        if "/web/1/js/items" in parts.path:
            return url

        path_parts = [part for part in parts.path.split("/") if part]
        local_location = next(
            (
                location
                for location in LOCAL_LOCATIONS.values()
                if location["slug"] == (path_parts[0] if len(path_parts) == 1 else "")
            ),
            None,
        )
        if local_location is None:
            raise ValueError(
                "Эту ссылку нельзя преобразовать локально; "
                "используйте structured search или convert_via_spfa"
            )

        source_params = dict(parse_qsl(parts.query, keep_blank_values=True))
        supported_params = {"q", "pmin", "pmax", "cd", "sort"}
        unsupported = set(source_params) - supported_params
        if unsupported:
            raise ValueError(
                "Локальный converter не поддерживает параметры: "
                + ", ".join(sorted(unsupported))
            )

        api_params: list[tuple[str, str | int]] = [
            ("localPriority", 0),
            ("locationId", local_location["location_id"]),
            ("presentationType", "serp"),
            ("sort", source_params.get("sort") or "default"),
        ]
        if source_params.get("q"):
            api_params.append(("query", source_params["q"]))
        for key in ("pmin", "pmax", "cd"):
            if source_params.get(key):
                api_params.append((key, source_params[key]))

        api_url = "https://www.avito.ru/web/1/js/items?" + urlencode(api_params)
        self._spfa.store(url, api_url)
        logger.info(f"Ссылка Avito локально преобразована: {url}")
        return api_url

    def convert_via_spfa(self, url: str) -> str:
        return self._spfa.convert(url)

    def resolve_location_id(self, location: str, location_id: int | None = None) -> int:
        if location_id is not None:
            if location_id <= 0:
                raise ValueError("location_id должен быть положительным числом")
            return location_id

        location_name = location.strip()
        local_location = LOCAL_LOCATIONS.get(location_name)
        if local_location is not None:
            return local_location["location_id"]
        if location_name in self._resolved_location_ids:
            return self._resolved_location_ids[location_name]
        slug = avito_location_slug(location_name)
        if not slug:
            raise self._unknown_location_error(location)

        probe_url = f"https://www.avito.ru/{slug}?q=mcp_location_probe"
        try:
            api_url = self.convert_via_spfa(probe_url)
            params = dict(parse_qsl(urlsplit(api_url).query, keep_blank_values=True))
            resolved_location_id = int(params["locationId"])
        except (KeyError, ValueError, RuntimeError) as error:
            raise self._unknown_location_error(location) from error

        self._resolved_location_ids[location_name] = resolved_location_id
        return resolved_location_id

    @staticmethod
    def _unknown_location_error(location: str) -> ValueError:
        return ValueError(
            f"Город или регион не найден: «{location}». "
            "Возможно, в названии опечатка? Укажите полное название."
        )

    @staticmethod
    def _validate(request: StructuredSearchRequest) -> None:
        if request.page < 1 or request.page > 100:
            raise ValueError("page должен быть от 1 до 100")
        if request.sort not in SORT_VALUES:
            supported = ", ".join(SORT_VALUES)
            raise ValueError(f"sort должен быть одним из: {supported}")
        if request.price_min is not None and request.price_min < 0:
            raise ValueError("price_min не может быть отрицательным")
        if request.price_max is not None and request.price_max < 0:
            raise ValueError("price_max не может быть отрицательным")
        if (
            request.price_min is not None
            and request.price_max is not None
            and request.price_min > request.price_max
        ):
            raise ValueError("price_min не может быть больше price_max")


def avito_location_slug(location: str) -> str:
    """Transliterate a full Russian city or region name to an Avito slug."""
    transliteration = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    })
    slug = location.strip().lower().translate(transliteration)
    slug = re.sub(r"[^a-z0-9-]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_-")
    return slug
