from urllib.parse import parse_qsl, urlsplit

import pytest

from parser.search_url_builder import AvitoSearchUrlBuilder, StructuredSearchRequest


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


def test_builds_structured_search_url():
    url, filters = AvitoSearchUrlBuilder().build(
        StructuredSearchRequest(
            query="iphone 16",
            location="Санкт-Петербург",
            price_min=10_000,
            price_max=100_000,
            sort="newest",
            page=2,
            with_delivery_only=True,
        )
    )

    params = _query(url)
    assert params["locationId"] == "653240"
    assert params["query"] == "iphone 16"
    assert params["pmin"] == "10000"
    assert params["pmax"] == "100000"
    assert params["sort"] == "date"
    assert params["page"] == "2"
    assert params["cd"] == "1"
    assert filters["location_id"] == 653240


def test_resolves_unknown_location_through_injected_resolver():
    calls: list[str] = []

    def resolve(location: str) -> int:
        calls.append(location)
        return 5510

    url, _ = AvitoSearchUrlBuilder().build(
        StructuredSearchRequest(location="Казань"),
        location_resolver=resolve,
    )

    assert _query(url)["locationId"] == "5510"
    assert calls == ["Казань"]


def test_rejects_invalid_price_range():
    with pytest.raises(ValueError, match="price_min"):
        AvitoSearchUrlBuilder().build(
            StructuredSearchRequest(price_min=100, price_max=10)
        )


def test_builds_supported_public_url_without_duplicate_logic():
    url = AvitoSearchUrlBuilder().build_public_url(
        "https://www.avito.ru/sankt-peterburg?q=iphone"
    )

    assert _query(url)["locationId"] == "653240"
    assert _query(url)["query"] == "iphone"
