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

To start only MCP without the tunnel:

```bash
podman compose up -d --build --no-deps parser_avito
```

The real tunnel variables must still be available for Compose configuration validation. Do not substitute dummy credentials on an existing stack: podman-compose can recreate dependent services despite `--no-deps`, especially with `--force-recreate`. To run the monitoring parser instead, stop MCP first and use `podman compose run --rm --no-deps -e APP_MODE=parser parser_avito`. Do not run both against the same Chromium profile concurrently.

- `search_ads(query, category_id, category_params, location, location_id, price_min, price_max, sort, page, with_delivery_only)` - build a search locally and return listings, `resolved_search`, and `category_options`;
- `get_ad(item_id, include_raw=false)` - load one listing by numeric ID and return details with image URLs;
- `search_ads(url=..., page=...)` remains available for direct API URLs, cached URLs, and simple `/rossiya`, `/moskva`, or `/sankt-peterburg` links.

Structured search defaults to `Россия`. `location` must be the exact full Russian city or region name: the local list contains `Россия`, `Москва`, and `Санкт-Петербург`; aliases such as `СПб` are not supported. Other full names are transliterated using Avito-style slugs (`Нижний Новгород` -> `nizhniy_novgorod`, `Свердловская область` -> `sverdlovskaya_oblast`) and resolved through SPFA, then cached. If the city or region cannot be resolved, the tool returns `Город или регион не найден ... Возможно, в названии опечатка?`. Use `location_id` to skip resolution when the Avito ID is known. `sort` accepts `relevance`, `newest`, `price_asc`, and `price_desc`. `page` is limited to 1..100. The server does not add, edit, or delete parser configuration and has no monitoring tools.

### Category workflow

Make the first search without `category_id` or `category_params`, even when a category seems obvious. Avito automatically interprets the query; MCP does not classify or rank categories itself.

- `category_options` contains Avito's navigation options: exact positive `category_id`, `name`, `category_params`, `selected`, and a reference `url`. Duplicate options with identical IDs and parameters are merged; UI node IDs are not exposed as category IDs.
- To refine the search, copy the chosen option's `category_id` and `category_params` into the next call. Category names and guessed IDs are not accepted. Parameters are necessary because multiple subcategories can share one category ID.
- `resolved_search` reports Avito's effective `query`, `category_id`, and `category_params`; `filters` still describes the requested filters. Avito can replace query text with product parameters, so these can differ.

Categories are extracted from the same API response as the listings, without an extra classification request. Options are navigation choices, not relevance probabilities. If Avito omits category metadata, `resolved_search` is null and `category_options` is empty; no categories are invented.


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

MCP always uses one persistent Playwright browser, started lazily by the first tool call. It does not use curl or perform a cookie handoff, regardless of the monitoring parser's `use_webdriver` and `playwright_handoff_to_curl` settings. Browser operations run on one worker thread; MCP shutdown closes the browser on that same thread. Search uses browser fetch on the Avito origin; card requests to the mobile API use a temporary tab in the same browser context, avoiding cross-origin fetch/CORS and preserving the search tab. HTML PoW challenges are executed by Chromium, not reimplemented in Python. Close any other Chromium process using the same profile before starting MCP.

Card navigation retains the mobile API request headers, which select the `success.mobile` response format. A 200 response without valid card data is reported as a tool error rather than an empty successful listing.

The browser uses `ru-RU`, `Europe/Moscow`, and native window dimensions (`no_viewport=True`). It omits `--enable-automation` and disables Blink's `AutomationControlled` feature; this makes `navigator.webdriver` false but does not guarantee that Avito accepts the browser. `AVITO_HEADLESS`, `AVITO_PROFILE_PATH`, and `AVITO_CHROMIUM_PATH` configure the browser; `proxy_string` selects its proxy. After Python changes, rebuild the Compose image with `podman compose build parser_avito`; `./run_chromium.sh` mounts the working tree and uses the updated launcher directly. Do not test both simultaneously against the same profile.

The default Compose service uses streamable HTTP:

```bash
podman compose up -d parser_avito
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`. The default port is `8000`; change it with `MCP_PORT`.
