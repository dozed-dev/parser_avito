from mcp_server import _extract_search_metadata


def test_preserves_distinct_category_params_and_merges_duplicate_navigation_nodes():
    mobile = {
        "id": 29365,
        "categoryId": 84,
        "name": "Мобильные телефоны",
        "params": {"110680": 458500},
        "url": "/all/telefony/mobilnye_telefony",
    }
    payload = {
        "searchCore": {
            "query": "",
            "categoryId": 84,
            "params": {"110680": 458500, "110618": 469735, "110617": 22121571},
        },
        "rubricators": {
            "side": {"nodes": [
                {"id": 1000001, "categoryId": 0, "name": "Все категории"},
                {
                    "id": 1000049,
                    "categoryId": 84,
                    "name": "Телефоны",
                    "params": {},
                    "children": [mobile],
                },
            ]},
            "rubricatorCategories": [
                {"name": "Root", "subs": [{**mobile, "current": True}]},
            ],
        },
    }

    result = _extract_search_metadata(payload)

    assert result["resolved_search"] == {
        "query": "",
        "category_id": 84,
        "category_params": {"110680": 458500, "110618": 469735, "110617": 22121571},
    }
    assert result["category_options"] == [
        {
            "category_id": 84,
            "name": "Телефоны",
            "category_params": {},
            "selected": False,
            "url": None,
        },
        {
            "category_id": 84,
            "name": "Мобильные телефоны",
            "category_params": {"110680": 458500},
            "selected": True,
            "url": "https://www.avito.ru/all/telefony/mobilnye_telefony",
        },
    ]


def test_does_not_invent_categories_when_avito_omits_metadata():
    assert _extract_search_metadata({"catalog": {"items": []}}) == {
        "resolved_search": None,
        "category_options": [],
    }
