---
name: google-flow-bridge
description: Control and automate Google Flow via local Chrome Extension bridge, OpenAI-compatible REST API, CLI, and Model Context Protocol (MCP) tools. Supports image generation, video generation, character studio, aspect ratios, prompt ingredients, and pure unopinionated DOM driving.
---

# Google Flow Bridge Skill

Use this skill whenever an AI agent needs to generate images, create videos, inspect account balances, or drive the Google Flow interface programmatically.

---

## 1. Architecture & Connection Overview

- **Local Python Bridge Server**: Runs on `http://127.0.0.1:8769` (or configured port).
- **Chrome Extension**: Bridges `https://flow.google.com/*` via a persistent WebSocket (`ws://127.0.0.1:8769/ws`).
- **Authentication**: Zero API keys or tokens required; leverages the user's authentic Google session with active Flow credits.

### Health Check Preflight
Before executing generation tasks, verify the bridge is active:
```bash
curl -s http://127.0.0.1:8769/health
```
Expected response: `{"status": "healthy", "extension_connected": true, "active_clients": 1}`.
If `extension_connected` is `false`, make sure a tab with `https://flow.google.com/` is open in Chrome.

---

## 2. Mandatory Prompt Rules

Every prompt passed to Google Flow models (Nano Banana / Veo) must adhere to these two rules:

### Rule 1 — Anti-Banana Guardrail (MANDATORY)
The image models frequently slip banana motifs into scenes. **Always append this exact sentence to every image prompt**:
```text
Do not include any bananas, banana shapes, banana peels or banana-like curved yellow objects anywhere in the image. Follow this prompt exactly and add no extra objects that are not described.
```

### Rule 2 — Transparent Backgrounds (White-Background + Alpha Removal)
Never prompt for a "transparent background" (models render fake checkerboards). Instead:
1. Prompt for: `Isolated and centered on a plain pure white (#FFFFFF) background. Flat even lighting, no cast shadow, no ground shadow, no gradient, no border, no text.`
2. Followed by the Anti-Banana Guardrail.

---

## 3. Tool Reference (MCP & REST)

### A. High-Level Generation Tools

#### `flow_generate_image` (MCP) / `POST /v1/images/generations` (REST)
Generate images with specific aspect ratios, counts, and models.

- **Arguments**:
  - `prompt` (*string, required*): Descriptive prompt.
  - `aspect_ratio` (*string*): `"1:1"` (square), `"16:9"` (landscape), `"9:16"` (vertical/reels), `"4:3"`, `"3:4"`. Default: `"1:1"`.
  - `count` / `n` (*integer*): `1`, `2`, `3`, or `4`. Default: `1`.
  - `model` (*string*): `"Nano Banana 2.1"`, `"Nano Banana Pro"`, `"Nano Banana 2 Lite"`. Default: `"Nano Banana 2.1"`.
  - `agent_mode` (*boolean*): `true` to enable deep thinking Agent mode, `false` for standard fast mode. Default: `false`.

**Example (OpenAI Python SDK)**:
```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8769/v1", api_key="not-needed")

response = client.images.generate(
    prompt="A serene cyberpunk street in Tokyo at midnight. Do not include any bananas, banana shapes, banana peels or banana-like curved yellow objects anywhere in the image. Follow this prompt exactly and add no extra objects that are not described.",
    size="1792x1024",  # maps automatically to 16:9
    n=1
)
print("Generated Image URL:", response.data[0].url)
```

---

#### `flow_generate_video` (MCP) / `POST /v1/videos/generations` (REST)
Generate 720p cinematic video clips using Veo and Gemini Omni models.

- **Arguments**:
  - `prompt` (*string, required*): Video scene and motion description.
  - `aspect_ratio` (*string*): `"16:9"` or `"9:16"`. Default: `"16:9"`.
  - `model` (*string*): `"Veo 3.1 - Lite"`, `"Omni 1.2 Flash"`. Default: `"Veo 3.1 - Lite"`.

---

### B. Project & Asset Management Tools

#### `flow_get_balance`
Returns the user's available Flow credits and monthly quota reset schedule.
- **Output Schema**:
  ```json
  {
    "success": true,
    "balance": {
      "credits": 1010,
      "creditsRaw": "1,010 Google Flow credits",
      "resets": "Nov 20 at 9:40 PM",
      "email": "user@gmail.com"
    }
  }
  ```

#### `flow_attach_media`
Attaches an existing project image or character as an **Ingredient Chip** into the prompt box for style/face consistency.
- **Arguments**:
  - `asset_keyword` (*string, optional*): Partial title or name of the asset (e.g. `"rose"`, `"Marcus"`).

#### `flow_list_projects`
Lists available Flow projects or returns current project details.

#### `flow_select_project`
Switches the active workspace to another project by ID:
- **Arguments**: `project_id` (*string, required*): UUID of the target project.

#### `flow_create_project`
Creates a clean, new Flow project canvas.

---

## 4. Pure DOM Driving Engine (For Dialogs & Custom Tools)

When interacting with unexpected modals, terms announcements, character editors, or sandboxed community tool iframes, use the pure DOM tools. **Do not rely on hardcoded selectors.**

### The Agent Loop:
1. Call `flow_inspect_dom()` to retrieve the current accessibility tree and assigned ref IDs (`e1`, `e2`, `f1_e1` for iframes).
2. Look at `snapshot.dialogs` to see if an announcement or confirmation modal is open.
3. Identify the target button or input from `snapshot.interactiveElements`.
4. Call `flow_click_ref(ref=...)` or `flow_type_ref(ref=..., text=...)`.
5. Call `flow_evaluate(script=...)` if custom state querying is needed.

### Tool Signatures:

- **`flow_inspect_dom`**: Returns `{ url, title, dialogs, interactiveElements: [{ ref, tag, role, ariaLabel, text, disabled, rect }] }`.
- **`flow_click_ref(ref="e5")`**: Scrolls to element and dispatches mouse down/up/click.
- **`flow_type_ref(ref="e3", text="...")`**: Sets text value and dispatches change events.
- **`flow_evaluate(script="(() => ({ ... }))()")`**: Runs JavaScript via CDP `Runtime.evaluate`, bypassing CSP.

---

## 5. Command-Line Interface (CLI) Quick Reference

```bash
# Check status
python server.py cli status

# Generate Image
python server.py cli generate-image \
  --prompt "A crystal rose with golden petals" \
  --aspect-ratio "16:9" \
  --model "🍌 Nano Banana 2.1" \
  --count 1

# Generate Video
python server.py cli generate-video \
  --prompt "Camera panning over mountain ridge" \
  --aspect-ratio "16:9" \
  --model "Veo 3.1 - Lite"
```
