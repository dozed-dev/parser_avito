# MCP server

The project includes a read-only MCP server with stdio and streamable HTTP transports.

## Run locally

Run it from the project directory:

```bash
podman compose run --rm -T -e APP_MODE=mcp parser_avito
```

- `search_ads(query, location, location_id, price_min, price_max, sort, page, with_delivery_only)` - build a search locally and return listings with image URLs;
- `get_ad(item_id, include_raw=false)` - load one listing by numeric ID and return details with image URLs;
- `search_ads(url=..., page=...)` remains available for direct API URLs, cached URLs, and simple `/rossiya`, `/moskva`, or `/sankt-peterburg` links.

Structured search defaults to `Россия`. `location` must be the exact full Russian city or region name: the local list contains `Россия`, `Москва`, and `Санкт-Петербург`; aliases such as `СПб` are not supported. Other full names are transliterated using Avito-style slugs (`Нижний Новгород` -> `nizhniy_novgorod`, `Свердловская область` -> `sverdlovskaya_oblast`) and resolved through SPFA, then cached. If the city or region cannot be resolved, the tool returns `Город или регион не найден ... Возможно, в названии опечатка?`. Use `location_id` to skip resolution when the Avito ID is known. `sort` accepts `relevance`, `newest`, `price_asc`, and `price_desc`. `page` is limited to 1..100. Categories are not exposed yet. The server does not add, edit, or delete parser configuration and has no monitoring tools.

## MCP client command

Example client configuration:

```json
{
  "mcpServers": {
    "avito": {
      "command": "podman",
      "args": [
        "compose", "run", "--rm", "-T", "-e", "APP_MODE=mcp", "parser_avito"
      ]
    }
  }
}
```

MCP always performs the Playwright profile -> curl handoff before the first tool call. Close any other Chromium process using the same profile before starting MCP.

The same tools are available over streamable HTTP:

```bash
podman compose run --rm \
  -e APP_MODE=mcp \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  -e MCP_PORT=8000 \
  parser_avito
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. The default port is `8000`; change it with `MCP_PORT`.
