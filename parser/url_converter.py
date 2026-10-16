import json
import time
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests
from loguru import logger


SPFA_AVITO_URL_ENDPOINT = "https://spfa.pro/api/avito-url/"


class AvitoUrlConverter:
    """Convert supported public URLs locally; keep SPFA as explicit fallback."""

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

    def convert(self, url: str) -> str:
        """Convert only supported public URLs without calling SPFA."""
        cached_url = self._cache.get(url)
        if cached_url:
            logger.debug(f"API URL загружен из кэша: {url}")
            return cached_url

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host not in {"avito.ru", "www.avito.ru"}:
            raise ValueError("Ссылка должна быть на avito.ru")
        if "/web/1/js/items" in parts.path:
            return url

        location_ids = {"rossiya": 621540, "moskva": 637640}
        path_parts = [part for part in parts.path.split("/") if part]
        if len(path_parts) != 1 or path_parts[0] not in location_ids:
            raise ValueError(
                "Эту ссылку нельзя преобразовать локально; "
                "используйте structured filters или convert_via_spfa"
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
            ("locationId", location_ids[path_parts[0]]),
            ("presentationType", "serp"),
            ("sort", source_params.get("sort") or "default"),
        ]
        if source_params.get("q"):
            api_params.append(("query", source_params["q"]))
        for key in ("pmin", "pmax", "cd"):
            if source_params.get(key):
                api_params.append((key, source_params[key]))

        api_url = "https://www.avito.ru/web/1/js/items?" + urlencode(api_params)
        self._cache[url] = api_url
        self._save_cache()
        logger.info(f"Ссылка Avito локально преобразована: {url}")
        return api_url

    def convert_via_spfa(self, url: str) -> str:
        cached_url = self._cache.get(url)
        if cached_url:
            logger.debug(f"API URL загружен из кэша: {url}")
            return cached_url

        with self._request_lock:
            # Another converter in this process may have populated the file
            # while this instance was waiting for the rate-limit lock.
            self._cache.update(self._load_cache())
            cached_url = self._cache.get(url)
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
                raise RuntimeError(
                    "SPFA не вернул api_url для ссылки Avito"
                )

            self._cache[url] = api_url
            self._save_cache()
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
        message = messages.get(
            response.status_code,
            f"SPFA вернул неожиданный HTTP-код {response.status_code}",
        )
        return message

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