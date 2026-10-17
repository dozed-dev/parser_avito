from urllib.parse import parse_qsl, urlsplit

import pytest

from parser.search_url_builder import (
    AvitoSearchUrlBuilder,
    LOCAL_LOCATIONS,
    StructuredSearchRequest,
    avito_location_slug,
)


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


@pytest.mark.parametrize("location, expected_id", [
    (name, str(data["location_id"]))
    for name, data in LOCAL_LOCATIONS.items()
])
def test_builds_every_local_location(tmp_path, location, expected_id):
    url, _ = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").build(
        StructuredSearchRequest(location=location)
    )

    assert _query(url)["locationId"] == expected_id

def test_builds_structured_search_url_for_local_location(tmp_path):
    url, filters = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").build(
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
    assert "categoryId" not in params
    assert not any(key.startswith("params[") for key in params)


def test_resolves_existing_unknown_location_through_spfa(tmp_path, monkeypatch):
    builder = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json")
    probes: list[str] = []

    def resolve(probe_url: str) -> str:
        probes.append(probe_url)
        return "https://www.avito.ru/web/1/js/items?locationId=5510"

    monkeypatch.setattr(builder._spfa, "convert", resolve)
    url, _ = builder.build(StructuredSearchRequest(location="Нижний Новгород"))

    assert _query(url)["locationId"] == "5510"
    assert probes == [
        "https://www.avito.ru/nizhniy_novgorod?q=mcp_location_probe"
    ]


def test_transliterates_full_region_name_to_avito_slug():
    assert avito_location_slug("Свердловская область") == "sverdlovskaya_oblast"
    assert avito_location_slug("Нижний Новгород") == "nizhniy_novgorod"
    assert avito_location_slug("Санкт-Петербург") == "sankt-peterburg"


def test_rejects_nonexistent_location_with_typo_hint(tmp_path, monkeypatch):
    builder = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json")
    monkeypatch.setattr(
        builder._spfa,
        "convert",
        lambda _: (_ for _ in ()).throw(RuntimeError("SPFA не нашел ссылку")),
    )

    with pytest.raises(ValueError, match="Город или регион не найден.*опечатка"):
        builder.build(StructuredSearchRequest(location="Город Которого Нет"))


def test_does_not_treat_short_alias_as_local_location(tmp_path, monkeypatch):
    builder = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json")
    probes: list[str] = []

    def reject(probe_url: str) -> str:
        probes.append(probe_url)
        raise RuntimeError("SPFA не нашел ссылку")

    monkeypatch.setattr(builder._spfa, "convert", reject)

    with pytest.raises(ValueError, match="Город или регион не найден"):
        builder.build(StructuredSearchRequest(location="СПб"))
    assert probes == ["https://www.avito.ru/spb?q=mcp_location_probe"]


def test_rejects_invalid_price_range(tmp_path):
    with pytest.raises(ValueError, match="price_min"):
        AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").build(
            StructuredSearchRequest(price_min=100, price_max=10)
        )


def test_builds_supported_public_url_without_duplicate_logic(tmp_path):
    url = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").convert_public_url(
        "https://www.avito.ru/sankt-peterburg?q=iphone"
    )

    assert _query(url)["locationId"] == "653240"
    assert _query(url)["query"] == "iphone"


def test_builds_exact_category_and_subcategory_params(tmp_path):
    url, _ = AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").build(
        StructuredSearchRequest(
            query="iphone 16",
            category_id=84,
            category_params={"110680": 458500, "110618": "469735"},
        )
    )

    params = _query(url)
    assert params["categoryId"] == "84"
    assert params["params[110680]"] == "458500"
    assert params["params[110618]"] == "469735"
    assert params["query"] == "iphone 16"


@pytest.mark.parametrize("search_request, message", [
    (StructuredSearchRequest(category_id=0), "category_id"),
    (StructuredSearchRequest(category_params={"110680": 458500}), "category_params"),
])
def test_rejects_invalid_category_selection(tmp_path, search_request, message):
    with pytest.raises(ValueError, match=message):
        AvitoSearchUrlBuilder(cache_path=tmp_path / "cache.json").build(search_request)
