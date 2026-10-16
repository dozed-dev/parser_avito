# MCP server

The project includes a read-only MCP server using stdio transport.

## Run locally

Run it from the project directory:

```bash
podman compose run --rm -T -e APP_MODE=mcp parser_avito
```

The server exposes:

- `search_ads(url, page=1)` - search a public Avito URL and return listing data with image URLs;
- `get_ad(item_id, include_raw=false)` - load one listing by numeric ID and return details with image URLs.

`url` must point to `avito.ru`. `page` is limited to 1..100. The server does not add, edit, or delete parser configuration and has no monitoring tools.

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
