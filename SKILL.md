---
name: google-flow-bridge
description: Control and automate Google Flow (flow.google.com) via a local Chrome Extension WebSocket bridge, OpenAI-compatible REST API, CLI, and MCP server. Use when generating images or videos with Google Flow, inspecting Flow credits/projects, attaching project media ingredients, or driving Flow UI dialogs via pure DOM tools.
---

# Google Flow Bridge

Automates [Google Flow](https://flow.google.com/) using your authenticated Chrome session — no API keys, Playwright, or headless browser flags required.

## 1. Repository Layout

- `server/server.py` — FastAPI REST + WebSocket (`ws://127.0.0.1:8769/ws`) + MCP (`POST /mcp`) + CLI entrypoint.
- `server/requirements.txt` — Python dependencies (`fastapi`, `uvicorn`, `websockets`, `pydantic`, `requests`).
- `extension/` — Manifest V3 Chrome extension (`manifest.json`, `background.js`, `content.js`, `popup.html`, `popup.js`) that connects to `ws://127.0.0.1:8769/ws`.

---

## 2. Setup & Installation

### Requirements
- **Python 3.10+**
- **Google Chrome** (or Chromium-based browser) signed in to an account with Google Flow access (`https://flow.google.com/`).

### Step 1 — Install Python Dependencies
From the repository root:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

### Step 2 — Start the Bridge Server
```bash
python server/server.py
```
- Default address: `http://127.0.0.1:8769` (override with `PORT=8769 python server/server.py`).

### Step 3 — Load the Chrome Extension
1. Open `chrome://extensions/` in Chrome.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and select the `<repo-root>/extension` directory.
4. Open [https://flow.google.com/](https://flow.google.com/) (or a specific project URL) and ensure you are signed in.

### Step 4 — Preflight Health Check
Before running generation tasks, verify both the server and extension are connected:
```bash
curl -s http://127.0.0.1:8769/health
```
Expected response:
```json
{
  "status": "healthy",
  "extension_connected": true,
  "active_clients": 1,
  "accounts": [
    {
      "client_id": "a1b2c3d4",
      "email": "user@gmail.com",
      "url": "https://flow.google.com/project/...",
      "connected_at": 1795123582.2
    }
  ]
}
```
If `extension_connected` is `false`, confirm a `https://flow.google.com/` tab is open and reload the extension in `chrome://extensions/`.

### Multi-Account Routing & Automatic Failover
- Multiple Chrome profiles/accounts can connect to the same bridge simultaneously.
- By default, requests are distributed **round-robin** across connected accounts.
- Pass `account` (email substring or `client_id`) to `POST /v1/images/generations`, `POST /v1/videos/generations`, or MCP `tools/call` arguments to pin a specific profile.
- If a target or background profile fails (e.g., minimized window or unmounted tab), the bridge **automatically fails over** to the next connected account and reports `fallback_used` / `executed_account`.

---

## 3. Mandatory Prompt Rules

Apply these rules to every Google Flow image prompt unless the user explicitly requests otherwise:

### Rule 1 — Anti-Banana Guardrail (Mandatory)
Nano Banana models occasionally insert banana motifs into unrelated scenes. Always append this exact sentence to the end of every image prompt:
```text
Do not include any bananas, banana shapes, banana peels or banana-like curved yellow objects anywhere in the image. Follow this prompt exactly and add no extra objects that are not described.
```

### Rule 2 — Transparent / Isolated Assets (White Background + Alpha Removal)
Do not ask the model for a `"transparent background"` (it renders a fake checkerboard). Instead:
1. Append before the Anti-Banana Guardrail:
   ```text
   Isolated and centered on a plain pure white (#FFFFFF) background. Flat even lighting, no cast shadow, no ground shadow, no gradient, no border, no text.
   ```
2. Post-process the downloaded image locally with alpha/flood-fill removal if a transparent PNG is needed.

---

## 4. REST API Reference (OpenAI-Compatible)

Base URL: `http://127.0.0.1:8769`

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Bridge health, extension WebSocket status, and connected `accounts` list |
| `GET` | `/v1/models` | List supported image and video models |
| `POST` | `/v1/images/generations` | Generate images (`prompt`, `model`, `size` or `aspect_ratio`, `n`, `agent_mode`, optional `account`) |
| `POST` | `/v1/videos/generations` | Generate videos (`prompt`, `model`, `aspect_ratio`, optional `account`) |
| `GET` | `/v1/media/download?url=...` | Download an authenticated Flow media URL as raw bytes (with canvas fallback) |
| `POST` | `/v1/media/download` | Download a Flow media URL (`url`, optional `output_path` to save on disk) |
| `POST` | `/mcp` | JSON-RPC endpoint for MCP tool calls |

### Example — Python OpenAI SDK
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8769/v1", api_key="not-needed")

response = client.images.generate(
    prompt=(
        "A serene cyberpunk street in Tokyo at midnight. "
        "Do not include any bananas, banana shapes, banana peels or banana-like "
        "curved yellow objects anywhere in the image. Follow this prompt exactly "
        "and add no extra objects that are not described."
    ),
    model="Nano Banana 2.1",
    size="1792x1024",  # maps to 16:9
    n=1,
)
print("Generated Image URL:", response.data[0].url)
```

### Required Download Workflow (Do Not Fetch Flow URLs Directly)
`POST /v1/images/generations`, `POST /v1/videos/generations`, `flow_generate_image`, and `flow_generate_video` return authenticated `https://flow.google.com/asb/...` URLs that **cannot** be downloaded directly with `curl` or `requests` (missing browser session cookies).

Whenever a generation returns a `url`, **immediately download it through the bridge**:
- **REST:** `POST /v1/media/download` with `{"url": "<returned_url>", "output_path": "./output.png"}` (or `GET /v1/media/download?url=<encoded_url>`).
- **MCP:** `flow_download_media(url="<returned_url>", output_path="./output.png")`.

### Example — cURL (Generate + Download)
```bash
curl -s -X POST http://127.0.0.1:8769/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A minimalist ceramic coffee cup on a sunlit wooden table. Do not include any bananas, banana shapes, banana peels or banana-like curved yellow objects anywhere in the image. Follow this prompt exactly and add no extra objects that are not described.",
    "model": "Nano Banana 2.1",
    "size": "1024x1024",
    "n": 1
  }'
```

---

## 5. MCP Integration & Tools

### Client Configuration (HTTP MCP)
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

### Generation Tools
- **`flow_generate_image`**:
  - `prompt` (*string, required*)
  - `aspect_ratio` (*string*): `"1:1"` (default), `"16:9"`, `"9:16"`, `"4:3"`, `"3:4"`
  - `count` (*integer*): `1`–`4` (default `1`)
  - `model` (*string*): `"Nano Banana 2.1"` (default), `"Nano Banana Pro"`, `"Nano Banana 2 Lite"`
  - `agent_mode` (*boolean*): `false` (default) or `true`
- **`flow_generate_video`**:
  - `prompt` (*string, required*)
  - `aspect_ratio` (*string*): `"16:9"` (default) or `"9:16"`
  - `model` (*string*): `"Veo 3.1 - Lite"` (default), `"Omni 1.2 Flash"`

### Project & Account Tools
- **`flow_list_accounts()`**: Lists all connected Google Flow browser profiles/accounts (`client_id`, `email`, `url`, `connected_at`).
- **`flow_get_balance()`**: Returns available Flow credits, reset schedule, and account email.
- **`flow_list_projects()`**: Lists available Flow projects or returns current project details.
- **`flow_select_project(project_id)`**: Navigates to a project by UUID.
- **`flow_create_project()`**: Creates a new Flow project canvas.
- **`flow_attach_media(asset_keyword?)`**: Attaches an existing project image or character as an ingredient chip.
- **`flow_download_media(url, output_path?)`**: Downloads an authenticated Flow image or video URL through the Chrome extension session (with canvas binary fallback) and saves it locally (returns `{ success, url, output_path, content_type, size }`).

*(All MCP `tools/call` arguments also accept an optional `account` string — email substring or `client_id` — to target a specific connected profile.)*

### Pure DOM Driving Tools (Dialogs, Announcements, Custom Flows)
When handling unexpected modals, terms prompts, or custom controls, inspect and drive the live DOM without hardcoded selectors:
1. `flow_inspect_dom()` — Returns `{ url, title, dialogs, interactiveElements: [{ ref, tag, role, ariaLabel, text, disabled, rect }] }`.
2. `flow_click_ref(ref="e5")` — Scrolls to element and dispatches mouse down/up/click.
3. `flow_type_ref(ref="e3", text="...")` — Sets text value and dispatches change events.
4. `flow_evaluate(script="(() => ({ ... }))()")` — Executes JavaScript via Chrome Debugger (`Runtime.evaluate`), bypassing CSP.

---

## 6. CLI Quick Reference

```bash
# Check bridge and extension status
python server/server.py cli status

# Generate an image
python server/server.py cli generate-image \
  --prompt "A crystal rose with golden petals. Do not include any bananas, banana shapes, banana peels or banana-like curved yellow objects anywhere in the image. Follow this prompt exactly and add no extra objects that are not described." \
  --aspect-ratio "16:9" \
  --model "Nano Banana 2.1" \
  --count 1

# Generate a video
python server/server.py cli generate-video \
  --prompt "Camera panning smoothly over a misty mountain ridge at sunrise" \
  --aspect-ratio "16:9" \
  --model "Veo 3.1 - Lite"

# Download a generated Flow media URL
python server/server.py cli download \
  --url "https://flow.google.com/asb/..." \
  --output "./output.jpg"
```
