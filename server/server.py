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
import asyncio
import logging
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flow_bridge")

# Connected Chrome extension WebSocket clients
active_websockets: List[WebSocket] = []
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
    logger.info(f"Chrome extension connected. Total active clients: {len(active_websockets)}")
    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            req_id = data.get("id")
            if req_id and req_id in pending_requests:
                future = pending_requests.pop(req_id)
                if not future.done():
                    if data.get("success"):
                        future.set_result(data.get("result"))
                    else:
                        future.set_exception(Exception(data.get("error", "Unknown extension error")))
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
        logger.info(f"Chrome extension disconnected. Remaining: {len(active_websockets)}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in active_websockets:
            active_websockets.remove(websocket)

async def dispatch_to_extension(action: str, payload: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    if not active_websockets:
        raise HTTPException(
            status_code=503,
            detail="No Chrome Extension connected. Please open flow.google.com and ensure Google Flow Bridge extension is enabled."
        )
    
    ws = active_websockets[-1] # use latest connected extension instance
    req_id = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[req_id] = future

    message = json.dumps({"id": req_id, "action": action, "payload": payload})
    await ws.send_text(message)

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
    return {
        "status": "healthy",
        "extension_connected": len(active_websockets) > 0,
        "active_clients": len(active_websockets),
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

# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) Implementation
# ---------------------------------------------------------------------------

MCP_TOOLS = [
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
        "name": "flow_get_status",
        "description": "Get current connection status and active UI settings from Google Flow.",
        "inputSchema": {
            "type": "object",
            "properties": {}
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

        if tool_name == "flow_generate_image":
            payload = {
                "mode": "image",
                "prompt": args.get("prompt"),
                "aspect_ratio": args.get("aspect_ratio", "1:1"),
                "count": args.get("count", 1),
                "model": args.get("model", "2.1"),
                "agent_mode": args.get("agent_mode", False)
            }
            res = await dispatch_to_extension("generate", payload, timeout=120.0)
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

        elif tool_name == "flow_get_status":
            res = await dispatch_to_extension("get_state", {})
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

    args = parser.parse_args(sys.argv[2:])

    base_url = "http://127.0.0.1:8766"

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

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        run_cli()
    else:
        import uvicorn
        port = int(os.environ.get("PORT", 8766))
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
