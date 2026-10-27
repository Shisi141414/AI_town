"""FastAPI 服务层 —— REST 控制 + WebSocket 广播 + API 档案管理

端点一览：
  GET  /                     前端页面
  GET  /api/state            全量快照（玩家+时钟+事件）
  POST /api/control          开始/暂停/继续/停止/速度/单步
  GET  /api/events           事件日志（支持 day 范围、type 筛选、分页）
  GET/POST/DELETE /api/profiles   API 档案 CRUD（CCswitch 风格）
  POST /api/profiles/test    连接测试
  POST /api/profiles/active  切换激活档案
  GET/PUT /api/config        设置（对白开关/温度/超时/玩家→模型绑定）
  POST /api/players          动态添加玩家
  DELETE /api/players/{name} 移除玩家
  GET  /api/players/{name}/memories  查看某玩家记忆流（后台观察）
  WS   /ws                   实时推送（每 tick 快照 + 事件 + 对白）
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))

from engine import simulation as sim_mod
from engine import world
from engine.llm import LLMError, test_profile
from engine.models import ApiProfile, AppConfig, Settings

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
WEB_DIR = ROOT / "web"

app = FastAPI(title="AI 小镇")

# ---------------------------------------------------------------- 全局状态
sim = sim_mod.Simulation()
config = AppConfig()


def load_config() -> None:
    global config
    if CONFIG_PATH.exists():
        try:
            config = AppConfig.model_validate_json(CONFIG_PATH.read_text("utf-8"))
        except Exception as e:                          # noqa: BLE001
            print(f"[config] 读取失败，使用默认: {e}")
    _sync_from_config()


def save_config() -> None:
    CONFIG_PATH.write_text(config.model_dump_json(indent=2), "utf-8")


def _sync_from_config() -> None:
    """把配置应用到模拟器：激活档案 + 每玩家模型绑定"""
    prof = next((p for p in config.profiles if p.id == config.active_profile_id), None)
    sim.profile = prof
    sim.settings = config.settings
    for p in sim.players:
        p.model = config.player_models.get(p.name, "")


@app.on_event("startup")
async def startup() -> None:
    load_config()
    if not sim.players:
        sim.spawn_preset_players()
        _sync_from_config()
    sim.log("system", "🏘️ 小镇服务启动，5 位居民已就位")


# ================================================================ REST：控制
class ControlCmd(BaseModel):
    cmd: str            # start/pause/resume/stop/step/speed/reset
    speed: float | None = None


@app.post("/api/control")
async def control(c: ControlCmd):
    global sim                     # reset 分支会整体替换模拟器实例
    if c.cmd == "start":
        if not sim.running:
            sim.log("system", "▶ 模拟开始")
            sim.start()
    elif c.cmd == "pause":
        sim.pause()
    elif c.cmd == "resume":
        sim.resume()
    elif c.cmd == "stop":
        sim.stop()
    elif c.cmd == "step":
        await sim.step_tick()
    elif c.cmd == "speed":
        if c.speed:
            sim.speed = max(0.1, min(10.0, c.speed))
            sim.log("system", f"🐇 速度调至 {sim.speed}x")
    elif c.cmd == "reset":
        sim.stop()
        sim = sim_mod.Simulation()
        sim.spawn_preset_players()
        _sync_from_config()
        globals()["_last_event_id"] = 0
        globals()["_dialogue_sent"] = 0
        sim.log("system", "🔄 世界已重置，5 位居民重新入住")
        return {"ok": True, "note": "已重置"}
    else:
        raise HTTPException(400, f"未知命令 {c.cmd}")
    return {"ok": True, "running": sim.running, "speed": sim.speed,
            "day": sim.day, "clock": sim.clock}


@app.get("/api/state")
async def state():
    return {
        "running": sim.running, "speed": sim.speed,
        "day": sim.day, "tick": sim.tick, "clock": sim.clock,
        "players": [p.snapshot().model_dump() for p in sim.players],
        "active_profile": (sim.profile.name if sim.profile else None),
        "llm_mode": bool(sim.profile and sim.profile.api_key),
    }


@app.get("/api/events")
async def events(day_from: int | None = None, day_to: int | None = None,
                 type_: str | None = None, q: str | None = None,
                 after_id: int = 0, limit: int = 200):
    """事件日志查询：支持天数范围、类型、关键词、增量拉取"""
    out = []
    for e in reversed(sim.events):
        if e.id <= after_id:
            break
        if day_from and e.day < day_from: continue
        if day_to and e.day > day_to: continue
        if type_ and e.type != type_: continue
        if q and q not in e.text and q not in " ".join(e.players): continue
        out.append(e.model_dump())
        if len(out) >= limit:
            break
    return out


# ================================================================ REST：档案
class ProfileIn(BaseModel):
    name: str
    format: str = "openai"
    base_url: str
    api_key: str
    default_model: str = ""


@app.get("/api/profiles")
async def list_profiles():
    return {
        "profiles": [p.model_dump() for p in config.profiles],
        "active": config.active_profile_id,
    }


@app.post("/api/profiles")
async def add_profile(p_in: ProfileIn):
    if p_in.format not in ("openai", "anthropic"):
        raise HTTPException(400, "format 必须是 openai 或 anthropic")
    prof = ApiProfile(id=str(uuid.uuid4())[:8], **p_in.model_dump())
    config.profiles.append(prof)
    if config.active_profile_id is None:
        config.active_profile_id = prof.id
    save_config(); _sync_from_config()
    return prof.model_dump()


@app.delete("/api/profiles/{pid}")
async def del_profile(pid: str):
    config.profiles = [p for p in config.profiles if p.id != pid]
    if config.active_profile_id == pid:
        config.active_profile_id = config.profiles[0].id if config.profiles else None
    save_config(); _sync_from_config()
    return {"ok": True}


class ActiveIn(BaseModel):
    id: str


@app.post("/api/profiles/active")
async def set_active(a: ActiveIn):
    if not any(p.id == a.id for p in config.profiles):
        raise HTTPException(404, "档案不存在")
    config.active_profile_id = a.id
    save_config(); _sync_from_config()
    sim.log("system", f"🔌 已切换 API 档案: {sim.profile.name if sim.profile else '?'}")
    return {"ok": True}


class TestIn(BaseModel):
    id: str
    model: str | None = None


@app.post("/api/profiles/test")
async def profile_test(t: TestIn):
    prof = next((p for p in config.profiles if p.id == t.id), None)
    if not prof:
        raise HTTPException(404, "档案不存在")
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, test_profile, prof, t.model)
    return {"ok": ok, "message": msg}


# ================================================================ REST：配置
@app.get("/api/config")
async def get_config():
    return {
        "settings": config.settings.model_dump(),
        "player_models": config.player_models,
        "default_model": sim.profile.default_model if sim.profile else "",
    }


class SettingsIn(BaseModel):
    dialogue_generation: bool | None = None
    temperature: float | None = None
    decision_timeout: float | None = None
    ticks_per_second: float | None = None
    player_models: dict[str, str] | None = None


@app.put("/api/config")
async def put_config(s_in: SettingsIn):
    if s_in.dialogue_generation is not None:
        config.settings.dialogue_generation = s_in.dialogue_generation
    if s_in.temperature is not None:
        config.settings.temperature = max(0.0, min(2.0, s_in.temperature))
    if s_in.decision_timeout is not None:
        config.settings.decision_timeout = max(5.0, min(120.0, s_in.decision_timeout))
    if s_in.ticks_per_second is not None:
        config.settings.ticks_per_second = max(0.1, min(10.0, s_in.ticks_per_second))
        sim.speed = config.settings.ticks_per_second
    if s_in.player_models is not None:
        config.player_models = s_in.player_models
    save_config(); _sync_from_config()
    return {"ok": True}


# ================================================================ REST：玩家
class PlayerIn(BaseModel):
    name: str
    color: str = "#7f8c8d"
    emoji: str = "🙂"


@app.post("/api/players")
async def add_player(p_in: PlayerIn):
    try:
        p = sim.add_player(p_in.name, p_in.color, p_in.emoji)
    except ValueError as e:
        raise HTTPException(400, str(e))
    p.model = config.player_models.get(p.name, "")
    return p.snapshot().model_dump()


@app.delete("/api/players/{name}")
async def remove_player(name: str):
    if not any(p.name == name for p in sim.players):
        raise HTTPException(404, "玩家不存在")
    sim.remove_player(name)
    return {"ok": True}


@app.get("/api/players/{name}/memories")
async def player_memories(name: str):
    p = next((p for p in sim.players if p.name == name), None)
    if not p:
        raise HTTPException(404, "玩家不存在")
    return [m.model_dump() for m in
            sorted(p.memory.items, key=lambda m: -m.tick)[:60]]


# ================================================================ 静态页面
@app.get("/")
async def index():
    html = (WEB_DIR / "index.html").read_text("utf-8")
    return HTMLResponse(html)


@app.get("/api/map")
async def map_info():
    return {"rows": world.MAP_ROWS,
            "width": world.WIDTH, "height": world.HEIGHT,
            "facilities": world.FACILITY_CHARS}


# ================================================================ WebSocket
class ConnectionManager:
    def __init__(self) -> None:
        self.clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:                      # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
_last_event_id = 0
_dialogue_sent = 0            # dialogue_worker 已消费的对白数（reset 时重置）


async def tick_broadcaster() -> None:
    """后台任务：每个 tick 广播快照 + 增量事件"""
    global _last_event_id
    last_tick = -1
    while True:
        await asyncio.sleep(max(0.05, 1.0 / max(0.2, sim.speed)) / 2)
        if sim.global_tick != last_tick and manager.clients:
            last_tick = sim.global_tick
            new_events = [e.model_dump() for e in sim.events if e.id > _last_event_id]
            if new_events:
                globals()["_last_event_id"] = max(e["id"] for e in new_events)
            # 对白：从 dialogue_log 取新的
            payload = {
                "type": "tick",
                "day": sim.day, "clock": sim.clock, "tick": sim.global_tick,
                "running": sim.running, "speed": sim.speed,
                "players": [p.snapshot().model_dump() for p in sim.players],
                "events": new_events,
            }
            await manager.broadcast(payload)


async def dialogue_worker() -> None:
    """消费对白请求：调 LLM 生成对白并广播"""
    while True:
        await asyncio.sleep(0.5)
        if not sim.dialogue_log or not sim.settings.dialogue_generation:
            continue
        if _dialogue_sent >= len(sim.dialogue_log):
            continue
        d = sim.dialogue_log[_dialogue_sent]
        globals()["_dialogue_sent"] = _dialogue_sent + 1
        a = next((p for p in sim.players if p.name == d["a"]), None)
        b = next((p for p in sim.players if p.name == d["b"]), None)
        if not a or not b:
            continue
        lines = await sim.generate_dialogue(a, b, d["kind"])
        if not lines:
            lines = [f"{a.name}: ...", f"{b.name}: ..."]
        ev = sim.log("dialogue",
                     " / ".join(lines) + f"  （{a.name}与{b.name}{'聊天' if d['kind'] == 'chat' else '约会'}）",
                     [a.name, b.name])
        await manager.broadcast({"type": "dialogue", "lines": lines,
                                 "a": a.name, "b": b.name,
                                 "event": ev.model_dump()})


@app.on_event("startup")
async def start_background() -> None:
    loop = asyncio.get_event_loop()
    loop.create_task(tick_broadcaster())
    loop.create_task(dialogue_worker())


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # 客户端心跳/指令（暂只保持连接）
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ================================================================ 错误兜底
@app.exception_handler(Exception)
async def any_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)[:300]})


if __name__ == "__main__":
    import uvicorn
    print("=" * 56)
    print("  🏘️ AI 小镇  http://127.0.0.1:8300")
    print("=" * 56)
    uvicorn.run(app, host="0.0.0.0", port=8300)
