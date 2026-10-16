"""Build Avito API URLs from structured search requests."""

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit


LOCATION_IDS = {
    "россия": 621540,
    "russia": 621540,
    "ru": 621540,
    "москва": 637640,
    "moscow": 637640,
    "санкт-петербург": 653240,
    "санкт петербург": 653240,
    "петербург": 653240,
    "спб": 653240,
    "saint petersburg": 653240,
    "saint-petersburg": 653240,
    "spb": 653240,
}

PUBLIC_PATH_LOCATION_IDS = {
    "rossiya": 621540,
    "moskva": 637640,
    "sankt-peterburg": 653240,
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


class AvitoSearchUrlBuilder:
    """Build API URLs and resolve unknown locations through an injected resolver."""

    def build(
        self,
        request: StructuredSearchRequest,
        location_resolver: Callable[[str], int] | None = None,
    ) -> tuple[str, dict[str, object]]:
        self._validate(request)
        resolved_location_id = self._resolve_location(
            request.location,
            request.location_id,
            location_resolver,
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

    def build_public_url(self, url: str) -> str:
        """Build simple public search URLs without calling an external resolver."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host not in {"avito.ru", "www.avito.ru"}:
            raise ValueError("Ссылка должна быть на avito.ru")
        if "/web/1/js/items" in parts.path:
            return url

        path_parts = [part for part in parts.path.split("/") if part]
        if len(path_parts) != 1 or path_parts[0] not in PUBLIC_PATH_LOCATION_IDS:
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
            ("locationId", PUBLIC_PATH_LOCATION_IDS[path_parts[0]]),
            ("presentationType", "serp"),
            ("sort", source_params.get("sort") or "default"),
        ]
        if source_params.get("q"):
            api_params.append(("query", source_params["q"]))
        for key in ("pmin", "pmax", "cd"):
            if source_params.get(key):
                api_params.append((key, source_params[key]))
        return "https://www.avito.ru/web/1/js/items?" + urlencode(api_params)

    @staticmethod
    def _resolve_location(
        location: str,
        location_id: int | None,
        location_resolver: Callable[[str], int] | None,
    ) -> int:
        if location_id is not None:
            if location_id <= 0:
                raise ValueError("location_id должен быть положительным числом")
            return location_id

        normalized = location.strip().lower()
        if normalized in LOCATION_IDS:
            return LOCATION_IDS[normalized]
        if location_resolver is not None:
            resolved = location_resolver(location)
            if resolved > 0:
                return resolved
        supported = ", ".join(sorted({"Россия", "Москва", "Санкт-Петербург"}))
        raise ValueError(
            f"Неизвестная location: {location}. Поддерживаются: {supported}; "
            "для другого места укажите location_id"
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


def location_slug_candidates(location: str) -> list[str]:
    """Return common Avito path spellings for a human-readable location."""
    transliteration = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
        "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    })
    slug = location.strip().lower().translate(transliteration)
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    candidates = [slug]
    if "-" in slug:
        candidates.append(slug.replace("-", "_"))
    return list(dict.fromkeys(candidate for candidate in candidates if candidate))
