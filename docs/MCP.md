# MCP server

The project includes a read-only MCP server with stdio and streamable HTTP transports.

## Run locally

Run it from the project directory:

```bash
podman compose run --rm -T --no-deps -e MCP_TRANSPORT=stdio parser_avito
```

## ChatGPT tunnel via Compose

Set the tunnel credentials in the shell or `.env` file:

```bash
export CONTROL_PLANE_API_KEY=sk-...
export CONTROL_PLANE_TUNNEL_ID=tunnel_...
podman compose up -d --build --remove-orphans
```

`parser_avito` (container `avito`) runs MCP on `127.0.0.1:8000/mcp`, preserving the Chromium profile and Wayland/X11 mounts. There is no separate `avito_mcp` container and no background monitoring parser. `tunnel-client` forwards the MCP endpoint to the configured OpenAI tunnel using `CONTROL_PLANE_API_KEY`, `CONTROL_PLANE_TUNNEL_ID`, and `MCP_SERVER_URL`.

To start only MCP without the tunnel, run `podman compose up -d parser_avito` (the required tunnel variables must still be set for Compose configuration validation). To run the monitoring parser instead, stop MCP first and use `podman compose run --rm --no-deps -e APP_MODE=parser parser_avito`. Do not run both against the same Chromium profile concurrently.

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
        "compose", "run", "--rm", "-T", "--no-deps", "-e", "MCP_TRANSPORT=stdio", "parser_avito"
      ]
    }
  }
}
```

MCP always performs the Playwright profile -> curl handoff before the first tool call. Before exporting cookies, Chromium checks the catalog API. If it receives an HTTP 439 HTML PoW challenge, it executes Avito's own challenge document on the Avito origin, waits for the page reload, and requires a successful JSON API response before handing the session to curl. The proof algorithm is not reimplemented in Python. HTTP 403/429 or an unsuccessful challenge abort the handoff rather than returning an unverified session. Close any other Chromium process using the same profile before starting MCP.

The manual Chromium launcher, parser Playwright client, and MCP handoff use `ru-RU`, `Europe/Moscow`, and native window dimensions (`no_viewport=True`). They omit `--enable-automation` and disable Blink's `AutomationControlled` feature; this makes `navigator.webdriver` false but does not guarantee that Avito accepts the browser. The curl handoff keeps the Russian language and selects `chrome<major>` impersonation from the browser User-Agent; the installed curl_cffi must support that Chrome version. After Python changes, rebuild the Compose image with `podman compose build parser_avito`; `./run_chromium.sh` mounts the working tree and uses the updated launcher directly. Do not test both simultaneously against the same profile.

The default Compose service uses streamable HTTP:

```bash
podman compose up -d parser_avito
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. The default port is `8000`; change it with `MCP_PORT`.
