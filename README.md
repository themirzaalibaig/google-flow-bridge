# Google Flow Bridge (Extension + Python Server + CLI + MCP)

Google Flow Bridge connects your authenticated Google Flow browser sessions with an OpenAI-compatible REST API, CLI, and MCP (Model Context Protocol) server.

## Architecture

- **Chrome Extension** (`extension/`):
  - Manifest V3 extension with host permissions for `https://flow.google.com/*` and `https://labs.google/*`.
  - Connects via WebSocket to `ws://127.0.0.1:8769/ws`.
  - Content script controls:
    - Mode switching (`image` vs `video`)
    - Aspect ratio selection (`16:9`, `4:3`, `1:1`, `3:4`, `9:16`)
    - Image count selection (`x1`, `x2`, `x3`, `x4`)
    - Model family selection (`Nano Banana 2.1`, `Nano Banana Pro`, `Nano Banana 2 Lite`, `Veo 3.1 - Lite`, `Omni 1.2 Flash`)
    - Agent Mode toggle (`true`/`false`)
  - Uses native Chrome Debugger (`Input.insertText`) to input prompts directly into ProseMirror while respecting Trusted Types CSP policies.
- **Python Server** (`server/server.py`):
  - **OpenAI-Compatible REST API** (default port `8769`, configurable via `PORT`):
    - `POST /v1/images/generations`
    - `POST /v1/videos/generations`
    - `GET /v1/media/download` & `POST /v1/media/download`
    - `GET /v1/models`
    - `GET /health`
  - **MCP Server**:
    - `POST /mcp` (JSON-RPC)
    - Tools: `flow_generate_image`, `flow_generate_video`, `flow_download_media`, `flow_get_balance`, `flow_list_projects`, `flow_create_project`, `flow_select_project`, `flow_attach_media`, `flow_inspect_dom`, `flow_click_ref`, `flow_type_ref`, `flow_evaluate`
  - **CLI Client**:
    - `python server/server.py cli status`
    - `python server/server.py cli generate-image --prompt "..." --aspect-ratio "16:9" --count 1`
    - `python server/server.py cli generate-video --prompt "..." --aspect-ratio "16:9"`

## Quick Start

### 1. Install Dependencies & Start Python Server
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
python server/server.py
```

### 2. Load the Chrome Extension
1. Open Google Chrome and go to `chrome://extensions/`.
2. Enable **Developer mode** in the top right.
3. Click **Load unpacked** and select the `<repo-root>/extension` directory.
4. Keep an active tab open on [https://flow.google.com/](https://flow.google.com/).

### 3. Usage Examples

#### OpenAI Python SDK
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8769/v1", api_key="not-needed")

response = client.images.generate(
    prompt="A futuristic electric hypercar speeding through neon cyberpunk city",
    size="1792x1024", # translates to 16:9
    n=1
)
print("Image URL:", response.data[0].url)
```

#### MCP Integration
Start `python server/server.py` and point your MCP client to the HTTP endpoint:
```json
{
  "mcpServers": {
    "google-flow-bridge": {
      "type": "http",
      "url": "http://127.0.0.1:8769/mcp"
    }
  }
}
```

See [SKILL.md](SKILL.md) for the full agent skill specification, prompt guardrails, and DOM driving tools.
