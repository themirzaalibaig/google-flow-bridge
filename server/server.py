#!/usr/bin/env python3
"""
Google Flow Bridge Server
-------------------------
Unified server providing:
1. OpenAI-compatible REST API endpoints:
   - POST /v1/images/generations
   - POST /v1/chat/completions (with automated Flow tool calling or direct prompt generation)
   - GET  /v1/models
2. Model Context Protocol (MCP) server endpoints:
   - POST /mcp (JSON-RPC)
   - GET  /sse (Server-Sent Events)
   - Tools: flow_generate_image, flow_generate_video, flow_get_status, flow_set_config
3. Interactive Command Line Interface (CLI):
   - python server.py cli generate --prompt "..." --aspect-ratio "16:9" --model "Nano Banana 2.1"
   - python server.py cli status
4. High-performance WebSocket bridge to the Chrome Extension on port 8765.
"""

import os
import sys
import json
import time
import uuid
import base64
import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flow_bridge")

# Connected Chrome extension WebSocket clients
active_websockets: List[WebSocket] = []
# Map WebSocket client to its metadata { "email": str, "url": str, "connected_at": float, "client_id": str }
client_metadata: Dict[WebSocket, Dict[str, Any]] = {}
# Round-robin index for parallel multi-account generation
_round_robin_idx = 0
# Pending request futures indexed by request id
pending_requests: Dict[str, asyncio.Future] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Google Flow Bridge Server starting on http://127.0.0.1:8765")
    yield
    logger.info("Google Flow Bridge Server shutting down")

app = FastAPI(
    title="Google Flow Bridge",
    description="OpenAI-compatible REST API & MCP Server for Google Flow",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# WebSocket Communication with Chrome Extension
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_websockets.append(websocket)
    client_metadata[websocket] = {
        "email": None,
        "url": None,
        "connected_at": time.time(),
        "client_id": str(uuid.uuid4())[:8]
    }
    logger.info(f"Chrome extension connected. Total active clients: {len(active_websockets)}")
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            action = data.get("action")
            if action == "register":
                payload = data.get("payload", {})
                client_metadata[websocket]["email"] = payload.get("email")
                client_metadata[websocket]["url"] = payload.get("url")
                logger.info(f"Registered client {client_metadata[websocket]['client_id']} -> Account: {payload.get('email')}")
                continue

            req_id = data.get("id")
            if req_id and req_id in pending_requests:
                future = pending_requests.pop(req_id)
                if not future.done():
                    if data.get("success"):
                        future.set_result(data.get("result"))
                    else:
                        future.set_exception(Exception(data.get("error", "Unknown extension error")))
    except WebSocketDisconnect:
        if websocket in active_websockets:
            active_websockets.remove(websocket)
        client_metadata.pop(websocket, None)
        logger.info(f"Chrome extension disconnected. Remaining: {len(active_websockets)}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in active_websockets:
            active_websockets.remove(websocket)
        client_metadata.pop(websocket, None)

async def dispatch_to_extension(
    action: str,
    payload: Dict[str, Any],
    timeout: float = 120.0,
    account: Optional[str] = None
) -> Dict[str, Any]:
    global _round_robin_idx
    if not active_websockets:
        raise HTTPException(
            status_code=503,
            detail="No Chrome Extension connected. Please open flow.google.com and ensure Google Flow Bridge extension is enabled."
        )

    # 1. Target specific account if requested
    target_ws: Optional[WebSocket] = None
    if account:
        for ws, meta in client_metadata.items():
            email = (meta.get("email") or "").lower()
            if account.lower() in email or account.lower() == meta.get("client_id", "").lower():
                target_ws = ws
                break
        if not target_ws:
            available = [meta.get("email") or meta.get("client_id") for meta in client_metadata.values()]
            raise HTTPException(
                status_code=404,
                detail=f"Requested account '{account}' not found among connected clients: {available}"
            )

    # 2. If no specific account requested, use round-robin load balancing across all connected accounts
    if not target_ws:
        valid_ws_list = [ws for ws in active_websockets if ws in client_metadata]
        if not valid_ws_list:
            valid_ws_list = active_websockets
        target_ws = valid_ws_list[_round_robin_idx % len(valid_ws_list)]
        _round_robin_idx += 1

    req_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[req_id] = future

    message = json.dumps({"id": req_id, "action": action, "payload": payload})
    await target_ws.send_text(message)

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        pending_requests.pop(req_id, None)
        raise HTTPException(status_code=504, detail=f"Operation timed out after {timeout} seconds.")

# ---------------------------------------------------------------------------
# OpenAI-Compatible Models & Schemas
# ---------------------------------------------------------------------------

class ImageGenerationRequest(BaseModel):
    prompt: str
    model: Optional[str] = "🍌 Nano Banana 2.1"
    n: Optional[int] = Field(default=1, ge=1, le=4)
    size: Optional[str] = "1024x1024" # standard OpenAI size format or 1:1, 16:9, etc.
    aspect_ratio: Optional[str] = None
    agent_mode: Optional[bool] = False
    response_format: Optional[str] = "url"

class VideoGenerationRequest(BaseModel):
    prompt: str
    model: Optional[str] = "Veo 3.1 - Lite"
    aspect_ratio: Optional[str] = "16:9"
    n: Optional[int] = 1

class MediaDownloadRequest(BaseModel):
    url: str
    output_path: Optional[str] = None

async def _download_media_bytes(url: str) -> tuple[bytes, str]:
    if not url:
        raise HTTPException(status_code=400, detail="Missing media url")
    res = await dispatch_to_extension("download_media", {"url": url}, timeout=60.0)
    b64 = res.get("base64")
    if not b64:
        raise HTTPException(status_code=502, detail="Extension returned empty media payload")
    content_type = res.get("contentType") or "application/octet-stream"
    return base64.b64decode(b64), content_type

def _save_media_bytes(raw_bytes: bytes, content_type: str, output_path: Optional[str] = None) -> Path:
    if output_path:
        dest = Path(output_path).expanduser()
    else:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
        if ext == ".jpe":
            ext = ".jpg"
        dest = Path("/tmp") / f"flow_media_{int(time.time() * 1000)}{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw_bytes)
    return dest

def map_size_to_aspect_ratio(size: Optional[str]) -> str:
    if not size:
        return "1:1"
    if size in ["1:1", "16:9", "4:3", "3:4", "9:16"]:
        return size
    mapping = {
        "1024x1024": "1:1",
        "512x512": "1:1",
        "1792x1024": "16:9",
        "1920x1080": "16:9",
        "1024x1792": "9:16",
        "1080x1920": "9:16",
        "1024x768": "4:3",
        "768x1024": "3:4"
    }
    return mapping.get(size, "1:1")

# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    connected_accounts = [
        {
            "client_id": meta.get("client_id"),
            "email": meta.get("email"),
            "url": meta.get("url"),
            "connected_at": meta.get("connected_at")
        }
        for ws, meta in client_metadata.items()
    ]
    return {
        "status": "healthy",
        "extension_connected": len(active_websockets) > 0,
        "active_clients": len(active_websockets),
        "accounts": connected_accounts,
        "timestamp": time.time()
    }

@app.get("/v1/models")
async def list_models():
    models = [
        {"id": "nano-banana-2.1", "object": "model", "owned_by": "google", "type": "image"},
        {"id": "nano-banana-pro", "object": "model", "owned_by": "google", "type": "image"},
        {"id": "nano-banana-2-lite", "object": "model", "owned_by": "google", "type": "image"},
        {"id": "veo-3.1-lite", "object": "model", "owned_by": "google", "type": "video"},
        {"id": "omni-1.2-flash", "object": "model", "owned_by": "google", "type": "video"},
        {"id": "omni-1.1-flash", "object": "model", "owned_by": "google", "type": "video"}
    ]
    return {"object": "list", "data": models}

@app.post("/v1/images/generations")
async def generate_image(req: ImageGenerationRequest):
    aspect_ratio = req.aspect_ratio or map_size_to_aspect_ratio(req.size)
    
    payload = {
        "mode": "image",
        "prompt": req.prompt,
        "model": req.model,
        "count": req.n,
        "aspect_ratio": aspect_ratio,
        "agent_mode": req.agent_mode
    }

    result = await dispatch_to_extension("generate", payload, timeout=120.0)
    
    new_media = result.get("new_media", [])
    data = []
    for item in new_media:
        data.append({"url": item})
    
    if not data and result.get("media_url"):
        data.append({"url": result.get("media_url")})

    return {
        "created": int(time.time()),
        "data": data
    }

@app.post("/v1/videos/generations")
async def generate_video(req: VideoGenerationRequest):
    payload = {
        "mode": "video",
        "prompt": req.prompt,
        "model": req.model,
        "count": req.n,
        "aspect_ratio": req.aspect_ratio or "16:9",
        "agent_mode": False
    }

    result = await dispatch_to_extension("generate", payload, timeout=180.0)
    return {
        "created": int(time.time()),
        "data": [{"url": item} for item in result.get("new_media", [])]
    }

@app.get("/v1/media/download")
async def download_media_get(url: str):
    raw_bytes, content_type = await _download_media_bytes(url)
    return Response(content=raw_bytes, media_type=content_type)

@app.post("/v1/media/download")
async def download_media_post(req: MediaDownloadRequest):
    raw_bytes, content_type = await _download_media_bytes(req.url)
    if req.output_path:
        dest = _save_media_bytes(raw_bytes, content_type, req.output_path)
        return {
            "success": True,
            "url": req.url,
            "output_path": str(dest),
            "content_type": content_type,
            "size": len(raw_bytes)
        }
    return Response(content=raw_bytes, media_type=content_type)

# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) Implementation
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "flow_list_accounts",
        "description": "List all connected Google Flow browser profiles/accounts, their client IDs, and active tab URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "flow_generate_image",
        "description": "Generate high quality images using Google Flow (Nano Banana models) with custom aspect ratio, count, and agent mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Descriptive generation prompt."
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["16:9", "4:3", "1:1", "3:4", "9:16"],
                    "default": "1:1"
                },
                "count": {
                    "type": "integer",
                    "enum": [1, 2, 3, 4],
                    "default": 1
                },
                "model": {
                    "type": "string",
                    "enum": ["Nano Banana 2.1", "Nano Banana Pro", "Nano Banana 2 Lite"],
                    "default": "Nano Banana 2.1"
                },
                "agent_mode": {
                    "type": "boolean",
                    "default": False
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "flow_generate_video",
        "description": "Generate cinematic videos using Google Flow (Veo 3.1 & Omni Flash models).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Video motion and scene description."},
                "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16"], "default": "16:9"},
                "model": {"type": "string", "enum": ["Veo 3.1 - Lite", "Omni 1.2 Flash"], "default": "Veo 3.1 - Lite"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "flow_inspect_dom",
        "description": "Inspect live accessible DOM snapshot of Google Flow or running Community Tools, returning elements with assigned ref IDs (e.g. e1, e2) and active dialogs.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "flow_click_ref",
        "description": "Click any interactive element in Google Flow, dialog, or community tool by its assigned ref ID (e.g. e5).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The target ref ID returned by flow_inspect_dom."}
            },
            "required": ["ref"]
        }
    },
    {
        "name": "flow_type_ref",
        "description": "Type text into an input, textbox, or rich editor by its assigned ref ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The target ref ID."},
                "text": {"type": "string", "description": "The text to type."}
            },
            "required": ["ref", "text"]
        }
    },
    {
        "name": "flow_evaluate",
        "description": "Execute arbitrary JavaScript expression directly in the Google Flow page context (and its iframes) and return the evaluation result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "JavaScript code to execute in the page context."}
            },
            "required": ["script"]
        }
    },
    {
        "name": "flow_get_balance",
        "description": "Get user account credits and reset date from Google Flow.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "flow_list_projects",
        "description": "List existing Flow projects or view the active project.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "flow_create_project",
        "description": "Create a new project on Google Flow.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "flow_select_project",
        "description": "Switch to a specific project by project ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "The target project ID (UUID format)."}
            },
            "required": ["project_id"]
        }
    },
    {
        "name": "flow_attach_media",
        "description": "Attach a project asset or ingredient image to the prompt box.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_keyword": {"type": "string", "description": "Search keyword for the asset title or name."}
            }
        }
    },
    {
        "name": "flow_download_media",
        "description": "Download an authenticated Google Flow image or video URL through the Chrome extension session and save it to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The Flow media URL (e.g. https://flow.google.com/asb/...)."},
                "output_path": {"type": "string", "description": "Optional local file path to save the media. Defaults to /tmp/flow_media_<ts>.<ext>."}
            },
            "required": ["url"]
        }
    }
]

@app.post("/mcp")
async def mcp_rpc(request: Request):
    body = await request.json()
    method = body.get("method")
    msg_id = body.get("id", 1)

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}})
    
    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        account = args.get("account")

        if tool_name == "flow_list_accounts":
            connected_accounts = [
                {
                    "client_id": meta.get("client_id"),
                    "email": meta.get("email"),
                    "url": meta.get("url"),
                    "connected_at": meta.get("connected_at")
                }
                for ws, meta in client_metadata.items()
            ]
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"accounts": connected_accounts, "count": len(connected_accounts)}, indent=2)}]
                }
            })

        elif tool_name == "flow_generate_image":
            payload = {
                "mode": "image",
                "prompt": args.get("prompt"),
                "aspect_ratio": args.get("aspect_ratio", "1:1"),
                "count": args.get("count", 1),
                "model": args.get("model", "2.1"),
                "agent_mode": args.get("agent_mode", False)
            }
            res = await dispatch_to_extension("generate", payload, timeout=120.0, account=account)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(res, indent=2)}
                    ]
                }
            })

        elif tool_name == "flow_generate_video":
            payload = {
                "mode": "video",
                "prompt": args.get("prompt"),
                "aspect_ratio": args.get("aspect_ratio", "16:9"),
                "count": 1,
                "model": args.get("model", "Lite"),
                "agent_mode": False
            }
            res = await dispatch_to_extension("generate", payload, timeout=180.0)
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(res, indent=2)}
                    ]
                }
            })

        elif tool_name == "flow_inspect_dom":
            res = await dispatch_to_extension("inspect_dom", {})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_click_ref":
            res = await dispatch_to_extension("click_ref", {"ref": args.get("ref")})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_type_ref":
            res = await dispatch_to_extension("type_ref", {"ref": args.get("ref"), "text": args.get("text")})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_evaluate":
            res = await dispatch_to_extension("evaluate", {"script": args.get("script")})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_get_balance":
            res = await dispatch_to_extension("get_balance", {})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_list_projects":
            res = await dispatch_to_extension("list_projects", {})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_create_project":
            res = await dispatch_to_extension("create_project", {})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_select_project":
            res = await dispatch_to_extension("select_project", {"projectId": args.get("project_id")})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_attach_media":
            res = await dispatch_to_extension("attach_ingredient", {"assetKeyword": args.get("asset_keyword")})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })

        elif tool_name == "flow_download_media":
            url = args.get("url")
            raw_bytes, content_type = await _download_media_bytes(url)
            dest = _save_media_bytes(raw_bytes, content_type, args.get("output_path"))
            res = {
                "success": True,
                "url": url,
                "output_path": str(dest),
                "content_type": content_type,
                "size": len(raw_bytes)
            }
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res, indent=2)}]
                }
            })
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {tool_name}"}
            })

    elif method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "google-flow-bridge", "version": "1.0.0"}
            }
        })

    return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {}})

# ---------------------------------------------------------------------------
# CLI Implementation
# ---------------------------------------------------------------------------

def run_cli():
    import argparse
    import urllib.request

    parser = argparse.ArgumentParser(description="Google Flow CLI Client")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Generate Image CLI
    gen_parser = subparsers.add_parser("generate-image", help="Generate an image via Flow Bridge")
    gen_parser.add_argument("--prompt", required=True, help="Image description prompt")
    gen_parser.add_argument("--aspect-ratio", default="1:1", choices=["16:9", "4:3", "1:1", "3:4", "9:16"])
    gen_parser.add_argument("--count", type=int, default=1, choices=[1, 2, 3, 4])
    gen_parser.add_argument("--model", default="🍌 Nano Banana 2.1")
    gen_parser.add_argument("--agent", action="store_true", help="Enable Agent mode")

    # Generate Video CLI
    vid_parser = subparsers.add_parser("generate-video", help="Generate a video via Flow Bridge")
    vid_parser.add_argument("--prompt", required=True, help="Video prompt")
    vid_parser.add_argument("--aspect-ratio", default="16:9", choices=["16:9", "9:16"])
    vid_parser.add_argument("--model", default="Veo 3.1 - Lite")

    # Status CLI
    subparsers.add_parser("status", help="Check Bridge Server and extension connection")

    # Download CLI
    dl_parser = subparsers.add_parser("download", help="Download a Flow media URL via the authenticated extension")
    dl_parser.add_argument("--url", required=True, help="Flow media URL to download")
    dl_parser.add_argument("--output", required=True, help="Local output file path")

    args = parser.parse_args(sys.argv[2:])

    port = int(os.environ.get("PORT", 8770))
    base_url = f"http://127.0.0.1:{port}"

    if args.command == "status":
        req = urllib.request.Request(f"{base_url}/health")
        try:
            with urllib.request.urlopen(req) as resp:
                print(resp.read().decode())
        except Exception as e:
            print(f"Error connecting to server: {e}")

    elif args.command == "generate-image":
        payload = json.dumps({
            "prompt": args.prompt,
            "aspect_ratio": args.aspect_ratio,
            "n": args.count,
            "model": args.model,
            "agent_mode": args.agent
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/images/generations",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                print(resp.read().decode())
        except Exception as e:
            print(f"Generation error: {e}")

    elif args.command == "generate-video":
        payload = json.dumps({
            "prompt": args.prompt,
            "aspect_ratio": args.aspect_ratio,
            "model": args.model
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/videos/generations",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                print(resp.read().decode())
        except Exception as e:
            print(f"Video generation error: {e}")

    elif args.command == "download":
        payload = json.dumps({
            "url": args.url,
            "output_path": args.output
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/v1/media/download",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                print(resp.read().decode())
        except Exception as e:
            print(f"Download error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        run_cli()
    else:
        import uvicorn
        port = int(os.environ.get("PORT", 8769))
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
