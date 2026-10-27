"""FastAPI 服务层 v2 —— 双场景 + 模型列表 + 提示词编辑

新增端点：
  GET  /api/models?profile_id=  获取该档案可用模型列表（代理网关 /models）
  GET/PUT /api/prompt           统一提示词读写
  GET  /api/map?scene=          双场景地图
  GET  /api/relationships       好感关系网络数据
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import requests as req_lib
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).parent))

from engine import brain, world
from engine import simulation as sim_mod
from engine.llm import LLMError, test_profile
from engine.models import ApiProfile, AppConfig, Settings

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
WEB_DIR = ROOT / "web"

app = FastAPI(title="AI 小镇 v2")

sim = sim_mod.Simulation()
config = AppConfig()
_identity_started = False


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
    prof = next((p for p in config.profiles if p.id == config.active_profile_id), None)
    sim.profile = prof
    sim.profiles_by_id = {p.id: p for p in config.profiles}
    sim.settings = config.settings
    sim.system_prompt = config.system_prompt
    # 旧版迁移：player_models(pid→model) → player_bindings(pid→{profile_id,model})
    if config.player_models and not config.player_bindings:
        for pid, model in config.player_models.items():
            config.player_bindings[pid] = {
                "profile_id": config.active_profile_id or "", "model": model}
        config.player_models = {}
    sim.binding_of = {pid: b["profile_id"] for pid, b in
                      config.player_bindings.items()}
    sim.model_of = {pid: b["model"] for pid, b in config.player_bindings.items()}


@app.on_event("startup")
async def startup() -> None:
    global _identity_started
    load_config()
    if not sim.players:
        sim.spawn_players(sim_mod.DEFAULT_PLAYER_COUNT)
        _sync_from_config()
    sim.log("system", "🏘️ 小镇服务启动")


# ================================================================ 控制
class ControlCmd(BaseModel):
    cmd: str
    speed: float | None = None


@app.post("/api/control")
async def control(c: ControlCmd):
    global sim, _identity_started
    if c.cmd == "start":
        if not _identity_started:
            _identity_started = True
            asyncio.get_event_loop().create_task(_start_with_identity())
        if not sim.running:
            sim.log("system", "▶ 模拟开始")
            sim.start()
    elif c.cmd == "pause":
        sim.pause()
    elif c.cmd == "resume":
        sim.resume()
    elif c.cmd == "stop":
        sim.stop()
        _identity_started = False
    elif c.cmd == "step":
        if sim.identity_phase:
            return {"ok": False, "note": "身份阶段进行中，请稍候"}
        await sim.step_tick()
    elif c.cmd == "speed":
        if c.speed:
            sim.speed = max(0.1, min(10.0, c.speed))
            sim.log("system", f"🐇 速度调至 {sim.speed}x")
    elif c.cmd == "reset":
        sim.stop()
        sim = sim_mod.Simulation()
        _sync_from_config()
        sim.spawn_players(sim_mod.DEFAULT_PLAYER_COUNT)
        _identity_started = False
        sim.log("system", "🔄 世界已重置")
        globals()["_last_event_id"] = 0
        globals()["_dialogue_sent"] = 0
        return {"ok": True, "note": "已重置（身份阶段将重新进行）"}
    else:
        raise HTTPException(400, f"未知命令 {c.cmd}")
    return {"ok": True, "running": sim.running, "speed": sim.speed,
            "day": sim.day, "clock": sim.clock,
            "identity_phase": sim.identity_phase}


async def _start_with_identity():
    """首次开始：先跑身份阶段再进入主循环"""
    await sim.run_identity_phase()


@app.get("/api/state")
async def state():
    name_of = {p.pid: p.name for p in sim.players}
    return {
        "running": sim.running, "speed": sim.speed,
        "day": sim.day, "tick": sim.tick, "clock": sim.clock,
        "identity_phase": sim.identity_phase,
        "decor_common": sim.decor_common,
        "players": [p.snapshot(name_of).model_dump() for p in sim.players],
        "active_profile": (sim.profile.name if sim.profile else None),
        "llm_mode": bool(sim.profile and sim.profile.api_key),
    }


@app.get("/api/events")
async def events(day_from: int | None = None, day_to: int | None = None,
                 type_: str | None = None, q: str | None = None,
                 after_id: int = 0, limit: int = 200):
    out = []
    for e in reversed(sim.events):
        if e.id <= after_id:
            break
        if day_from and e.day < day_from: continue
        if day_to and e.day > day_to: continue
        if type_ and e.type != type_: continue
        if q and q not in e.text: continue
        out.append(e.model_dump())
        if len(out) >= limit:
            break
    return out


# ================================================================ 地图
@app.get("/api/map")
async def map_info(scene: str = "house"):
    if scene not in ("house", "town"):
        raise HTTPException(400, "scene 必须是 house 或 town")
    return {"scene": scene, "rows": ["".join(r) for r in world.grid(scene)],
            "width": world.SIZE, "height": world.SIZE,
            "facilities": world.FACILITY_CHARS}


# ================================================================ 关系网络
@app.get("/api/relationships")
async def relationships():
    """好感网络：节点=玩家，边=互相好感（各方向的平均）"""
    nodes = [{"pid": p.pid, "name": p.name, "color": p.color, "emoji": p.emoji}
             for p in sim.players]
    edges = []
    for i, a in enumerate(sim.players):
        for b in sim.players[i + 1:]:
            ab = a.affinity.get(b.pid)
            ba = b.affinity.get(a.pid)
            if ab is None and ba is None:
                continue
            avg = ((ab if ab is not None else 50) + (ba if ba is not None else 50)) / 2
            edges.append({"a": a.pid, "b": b.pid, "a_name": a.name, "b_name": b.name,
                          "ab": round(ab, 1) if ab is not None else None,
                          "ba": round(ba, 1) if ba is not None else None,
                          "avg": round(avg, 1)})
    return {"nodes": nodes, "edges": edges, "decor_common": sim.decor_common}


# ================================================================ 档案
class ProfileIn(BaseModel):
    name: str
    format: str = "openai"
    base_url: str
    api_key: str
    default_model: str = ""


@app.get("/api/profiles")
async def list_profiles():
    return {"profiles": [p.model_dump() for p in config.profiles],
            "active": config.active_profile_id}


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


# ---------- 模型列表（新增） ----------
@app.get("/api/models")
async def list_models(profile_id: str | None = None,
                      base_url: str | None = None,
                      api_key: str | None = None,
                      format: str = "openai"):
    """获取可用模型列表。两种用法：
    1. profile_id：已保存档案
    2. base_url + api_key + format：表单草稿直查（保存前就能拉列表）
    """
    if base_url and api_key:
        prof = ApiProfile(id="draft", name="draft", format=format,
                          base_url=base_url, api_key=api_key)
    else:
        prof = next((p for p in config.profiles if p.id == profile_id), None) \
            or next((p for p in config.profiles if p.id == config.active_profile_id), None) \
            or (config.profiles[0] if config.profiles else None)
    if prof is None:
        raise HTTPException(400, "请先填写 API 地址和 Key，或保存一个档案")
    base = prof.base_url.rstrip("/")
    if prof.format == "anthropic":
        url = f"{base}/v1/models"
        headers = {"x-api-key": prof.api_key, "anthropic-version": "2023-06-01"}
    else:
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        headers = {"Authorization": f"Bearer {prof.api_key}"}
    try:
        r = await asyncio.get_event_loop().run_in_executor(
            None, lambda: req_lib.get(url, headers=headers, timeout=(5, 15)))
        if r.status_code != 200:
            return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:150]}",
                    "models": []}
        data = r.json()
        models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        return {"ok": True, "models": sorted(models), "count": len(models)}
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "message": str(e)[:150], "models": []}


# ================================================================ 提示词
@app.get("/api/prompt")
async def get_prompt():
    return {"system_prompt": brain.get_system_prompt(config.system_prompt),
            "is_default": not config.system_prompt.strip(),
            "default_prompt": brain.DEFAULT_SYSTEM_PROMPT}


class PromptIn(BaseModel):
    system_prompt: str


@app.put("/api/prompt")
async def put_prompt(p_in: PromptIn):
    config.system_prompt = p_in.system_prompt
    save_config(); _sync_from_config()
    sim.log("system", "📝 统一提示词已更新（下一决策生效）")
    return {"ok": True}


# ================================================================ 配置
@app.get("/api/config")
async def get_config():
    return {
        "settings": config.settings.model_dump(),
        "profiles": [{"id": p.id, "name": p.name, "default_model": p.default_model}
                     for p in config.profiles],
        "active_profile": config.active_profile_id,
        "players": [{"pid": p.pid, "name": p.name,
                     "binding": config.player_bindings.get(p.pid,
                         {"profile_id": "", "model": ""})}
                    for p in sim.players],
    }


class SettingsIn(BaseModel):
    dialogue_generation: bool | None = None
    temperature: float | None = None
    decision_timeout: float | None = None
    ticks_per_second: float | None = None
    player_bindings: dict[str, dict[str, str]] | None = None  # pid → {profile_id, model}


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
    if s_in.player_bindings is not None:
        config.player_bindings = s_in.player_bindings
        _sync_from_config()
        save_config()
        return {"ok": True}
    save_config(); _sync_from_config()
    return {"ok": True}


# ================================================================ 玩家
class PlayerIn(BaseModel):
    name: str
    color: str = "#7f8c8d"
    emoji: str = "🙂"


@app.post("/api/players")
async def add_player(p_in: PlayerIn):
    # 未运行时才允许手动增减（运行中玩家身份/状态会与模拟冲突）
    if sim.running or sim.identity_phase:
        raise HTTPException(400, "模拟运行中无法添加玩家，请先暂停/停止")
    try:
        p = sim.add_player_manual(p_in.name, p_in.color, p_in.emoji)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return p.snapshot().model_dump()


@app.delete("/api/players/{name}")
async def remove_player(name: str):
    if sim.running or sim.identity_phase:
        raise HTTPException(400, "模拟运行中无法移除玩家，请先暂停/停止")
    p = next((p for p in sim.players if p.name == name), None)
    if not p:
        raise HTTPException(404, "玩家不存在")
    sim.remove_player(p.pid)
    # 清理该玩家的绑定
    config.player_bindings.pop(p.pid, None)
    save_config()
    return {"ok": True}


@app.get("/api/players/{name}/memories")
async def player_memories(name: str):
    p = next((p for p in sim.players if p.name == name), None)
    if not p:
        raise HTTPException(404, "玩家不存在")
    return [m.model_dump() for m in
            sorted(p.memory.items, key=lambda m: -m.tick)[:60]]


# ================================================================ 静态页
@app.get("/")
async def index():
    return HTMLResponse((WEB_DIR / "index.html").read_text("utf-8"))


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
_dialogue_sent = 0


async def tick_broadcaster() -> None:
    global _last_event_id
    last_tick = -1
    while True:
        await asyncio.sleep(max(0.05, 1.0 / max(0.2, sim.speed)) / 2)
        if sim.global_tick != last_tick and manager.clients:
            last_tick = sim.global_tick
            new_events = [e.model_dump() for e in sim.events if e.id > _last_event_id]
            if new_events:
                globals()["_last_event_id"] = max(e["id"] for e in new_events)
            name_of = {p.pid: p.name for p in sim.players}
            payload = {
                "type": "tick",
                "day": sim.day, "clock": sim.clock, "tick": sim.global_tick,
                "running": sim.running, "speed": sim.speed,
                "identity_phase": sim.identity_phase,
                "decor_common": sim.decor_common,
                "players": [p.snapshot(name_of).model_dump() for p in sim.players],
                "events": new_events,
            }
            await manager.broadcast(payload)


@app.on_event("startup")
async def start_background() -> None:
    loop = asyncio.get_event_loop()
    loop.create_task(tick_broadcaster())
    # 对白已由 Conversation 引擎实时写入事件流，无需单独 worker


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.exception_handler(Exception)
async def any_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)[:300]})


if __name__ == "__main__":
    import uvicorn
    print("=" * 56)
    print("  🏘️ AI 小镇 v2  http://127.0.0.1:8300")
    print("=" * 56)
    uvicorn.run(app, host="0.0.0.0", port=8300)
