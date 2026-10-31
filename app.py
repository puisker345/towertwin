"""FastAPI REST and SSE server for TowerTwin (Section 10.3)."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .agent.briefing import briefing_store
from .agent.gemini_client import gemini_client
from .agent.orchestrator import orchestrator
from .events import event_bus
from .live.engine import live_engine
from .sim.scenarios import Scenario, validate_and_clamp_scenario

_WEB_DIR = Path(__file__).resolve().parent / "web"


class RunRequest(BaseModel):
    position_limit: int | None = None
    mode: str = "repair"
    limits: dict[str, Any] | None = None
    wait: bool = False


class ApproveRequest(BaseModel):
    artifact_hash: str = ""


class CrashRequest(BaseModel):
    scenario: dict[str, Any] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    loop = asyncio.get_running_loop()
    event_bus.bind_loop(loop)
    await live_engine.start()
    try:
        yield
    finally:
        await live_engine.stop()


app = FastAPI(
    title="TowerTwin",
    description="Argon plans. Python proves. You approve.",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "limits": dict(live_engine.limits),
        "desk_limits": dict(live_engine.limits),
        "default_limits": config.get_desk_limits(),
        "tightened_limits": config.get_tightened_desk_limits(),
        "mock_llm": gemini_client.is_mock,
        "voice_consented": config.has_voice_sample(),
        "has_voice_sample": config.has_voice_sample(),
        "bot_status": live_engine.bot_status,
        "artifact_hash": live_engine.loaded_artifact_hash,
        "artifact_id": live_engine.loaded_artifact_id,
        "running": orchestrator.running,
    }


@app.post("/api/run")
async def start_run(req: RunRequest) -> JSONResponse:
    if orchestrator.running:
        raise HTTPException(status_code=409, detail="A verification run is already in progress.")

    mode = req.mode if req.mode in ("repair", "scratch") else "repair"
    if mode == "repair" and req.position_limit is None and req.limits is None:
        active_limits = config.get_tightened_desk_limits()
    else:
        active_limits = config.get_desk_limits()

    if req.limits:
        active_limits.update(req.limits)

    if req.position_limit is not None:
        pos_lim = max(40, min(100, int(req.position_limit)))
        active_limits["position_limit"] = pos_lim

    live_engine.set_desk_limits(active_limits)
    live_engine.set_pending_artifact(None)
    event_bus.clear_history()

    starting_code = live_engine.loaded_code if live_engine.loaded_code else None
    orchestrator.running = True
    task = asyncio.create_task(
        orchestrator.run(
            mode=mode,
            limits=active_limits,
            starting_code=starting_code,
            on_before_signoff=live_engine.set_pending_artifact,
        )
    )
    orchestrator.current_task = task

    if req.wait:
        art = await task
        return JSONResponse(
            status_code=200,
            content={
                "status": "completed",
                "mode": mode,
                "limits": active_limits,
                "passed": bool(art and art.passed),
                "artifact_hash": art.artifact_hash if art else None,
                "artifact_id": art.artifact_id if art else None,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "started",
            "mode": mode,
            "limits": active_limits,
        },
    )


@app.get("/api/events")
async def sse_events(request: Request) -> StreamingResponse:
    queue = event_bus.subscribe(replay_history=True)

    async def _event_generator() -> AsyncGenerator[str, None]:
        try:
            # Emit initial mock_mode state to the newly connected client
            init_evt = {
                "type": "mock_mode",
                "ts": 0.0,
                "mock_llm": gemini_client.is_mock,
                "limits": dict(live_engine.limits),
                "bot_status": live_engine.bot_status,
                "artifact_hash": live_engine.loaded_artifact_hash,
                "artifact_id": live_engine.loaded_artifact_id,
            }
            yield f"data: {json.dumps(init_evt)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield f"data: {json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/approve")
async def approve_artifact(req: ApproveRequest) -> JSONResponse:
    try:
        result = live_engine.approve(req.artifact_hash)
        return JSONResponse(status_code=200, content={"status": "approved", **result})
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/crash")
async def trigger_crash(req: CrashRequest | None = None) -> dict[str, Any]:
    scenario_obj: Scenario | None = None
    if req and req.scenario:
        scenario_obj = validate_and_clamp_scenario(req.scenario)
    out = live_engine.trigger_crash(scenario_obj)
    return {"status": "crash_started", **out}


@app.post("/api/kill")
async def trigger_kill() -> dict[str, Any]:
    out = live_engine.kill_switch()
    return {"status": "killed", **out}


@app.post("/api/reset")
async def trigger_reset() -> dict[str, Any]:
    if orchestrator.current_task and not orchestrator.current_task.done():
        orchestrator.current_task.cancel()
        try:
            await orchestrator.current_task
        except asyncio.CancelledError:
            pass
    orchestrator.running = False
    event_bus.clear_history()
    out = await live_engine.reset()
    return {"status": "reset", **out}


@app.get("/api/briefing.wav")
async def get_briefing_wav() -> Response:
    return Response(
        content=briefing_store.wav_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_model=None)
async def serve_index() -> Response:
    index_path = _WEB_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse(
        "<!doctype html><html><head><title>TowerTwin</title></head>"
        "<body><h1>TowerTwin</h1><p>Argon plans. Python proves. You approve.</p></body></html>"
    )


@app.get("/favicon.ico", response_model=None)
async def serve_favicon() -> Response:
    return Response(status_code=204)


@app.get("/{asset_name}", response_model=None)
async def serve_web_asset(asset_name: str) -> Response:
    if asset_name in ("style.css", "app.js", "panels.js", "twin3d.js", "audio.js"):
        target = _WEB_DIR / asset_name
        if target.is_file():
            media = "text/css" if asset_name.endswith(".css") else "application/javascript"
            return FileResponse(target, media_type=media)
    raise HTTPException(status_code=404, detail="Not found")


if _WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


if _WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")
