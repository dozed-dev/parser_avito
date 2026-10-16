"""
Клиент для запросов парсера.
"""
import json
import os
import time
from dataclasses import dataclass

from curl_cffi import requests
from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.proxies.proxy import Proxy


@dataclass
class _PlaywrightResponse:
    status_code: int
    text: str
    headers: dict

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class HttpClient:
    ITEM_API_URL = "https://m.avito.ru/api/1/card/items/{item_id}"
    ITEM_API_HEADERS = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ru-RU,ru;q=0.9",
        "referer": "https://m.avito.ru/",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
    }

    def __init__(
        self,
        proxy: Proxy,
        cookies: CookiesProvider | None = None,
        timeout: int = 30,
        max_retries: int = 5,
        retry_delay: int = 5,
        block_threshold: int = 3,
        use_playwright: bool = False,
    ):
        self.proxy = proxy
        self.cookies = cookies
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.block_threshold = block_threshold
        self._block_attempts = 0
        self._playwright = None
        self._context = None
        self._page = None
        self._client = None if use_playwright else self._build_client()
        if use_playwright:
            self._start_playwright()

    def _start_playwright(self) -> None:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        profile = os.getenv("AVITO_PROFILE_PATH", "storage/container-chromium-profile")
        executable = os.getenv(
            "AVITO_CHROMIUM_PATH",
            "/ms-playwright/chromium-1169/chrome-linux/chrome",
        )
        options = {
            "headless": False,
            "viewport": None,
            "args": [
                "--no-sandbox",
                "--disable-breakpad",
                "--disable-crash-reporter",
                "--crash-dumps-dir=/tmp",
                "--ozone-platform=wayland",
            ],
        }
        if os.path.exists(executable):
            options["executable_path"] = executable
        self._context = self._playwright.chromium.launch_persistent_context(profile, **options)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto("https://www.avito.ru", wait_until="domcontentloaded", timeout=self.timeout * 1000)

    def _build_client(self) -> requests.Session:
        fingerprint = self.cookies.get_fingerprint() if self.cookies else None
        fingerprint = fingerprint if isinstance(fingerprint, dict) else {}
        fingerprint_headers = fingerprint.get("headers", {})
        fingerprint_headers = (
            fingerprint_headers if isinstance(fingerprint_headers, dict) else {}
        )
        user_agent = fingerprint_headers.get("user-agent")
        if not user_agent and self.cookies:
            user_agent = self.cookies.get_user_agent()
        user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        )
        is_mobile = " Mobile " in user_agent
        impersonate = fingerprint.get("impersonate") or "chrome"
        session = requests.Session(impersonate=impersonate)
        default_headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            "referer": "https://www.avito.ru/",
            'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="149", "Chromium";v="149"',
            'sec-ch-ua-mobile': '?1' if is_mobile else '?0',
            'sec-ch-ua-platform': '"Android"' if is_mobile else '"Windows"',
            'user-agent': user_agent,
        }
        headers = {**default_headers, **fingerprint_headers}
        headers["user-agent"] = user_agent
        session.headers.update(headers)
        if self.cookies:
            session.cookies.update(self.cookies.get())

        proxy = self.proxy.get_httpx_proxy()
        if proxy:
            session.proxies = {
                "http": proxy,
                "https": proxy,
            }

        return session

    def _reset_client(self) -> None:
        self._client.close()
        self._client = self._build_client()

    def _recover_session(self) -> None:
        """Сменить IP и обновить cookies в правильном порядке."""
        if self._playwright:
            self._page.reload(wait_until="domcontentloaded", timeout=self.timeout * 1000)
            self._block_attempts = 0
            return
        self.proxy.handle_block()
        if self.cookies:
            self.cookies.handle_block()
        self._reset_client()
        self._block_attempts = 0

    def _request_playwright(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers") or {}
        result = self._page.evaluate(
            """async ({method, url, headers}) => {
                const response = await fetch(url, {method, headers, credentials: 'include'});
                return {status: response.status, headers: Object.fromEntries(response.headers.entries()), text: await response.text()};
            }""",
            {"method": method, "url": url, "headers": headers},
        )
        return _PlaywrightResponse(result["status"], result["text"], result["headers"])

    def request(self, method: str, url: str, **kwargs):
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = (
                    self._request_playwright(method, url, **kwargs)
                    if self._playwright
                    else self._client.request(method, url, timeout=self.timeout, **kwargs)
                )

                if self.cookies:
                    self.cookies.update(response)

                if response.status_code in (403, 429, 439):
                    self._block_attempts += 1
                    logger.warning(
                        f"Запрос заблокирован ({response.status_code}) к {url}, "
                        f"попытка {self._block_attempts}"
                    )

                    if self._block_attempts >= self.block_threshold:
                        logger.warning("Достигнут лимит блокировок, запускается обработка")
                        self._recover_session()

                    time.sleep(self.retry_delay)
                    continue

                self._block_attempts = 0
                response.raise_for_status()
                return response

            except requests.RequestsError as e:
                last_exc = e
                self._block_attempts += 1
                logger.warning(f"Request error (attempt {attempt}): {e}")
                if self._block_attempts >= self.block_threshold:
                    logger.warning(
                        "Достигнут лимит сетевых ошибок, "
                        "обновляем proxy и cookies"
                    )
                    self._recover_session()
                time.sleep(self.retry_delay)

        raise RuntimeError("HTTP запросы были неуспешными") from last_exc

    def fetch_item_data(self, item_id: int) -> dict:
        """Загрузить JSON карточки объявления через mobile API."""
        response = self.request(
            "GET",
            self.ITEM_API_URL.format(item_id=item_id),
            headers=self.ITEM_API_HEADERS.copy(),
        )
        logger.info(f"Карточка {item_id}: HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Ответ карточки не является JSON-объектом")
        return payload
