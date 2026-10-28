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
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from pathlib import Path

import requests as req_lib
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).parent))

from engine import brain, world
from engine.recording import recordings, read_frame
from engine.saves import SaveStore
from engine import simulation as sim_mod
from engine.llm import LLMError, test_profile
from engine.models import ApiProfile, AppConfig, Settings, Vehicle

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
WEB_DIR = ROOT / "web"

@asynccontextmanager
async def lifespan(app):
    await startup()
    background = asyncio.create_task(tick_broadcaster())
    try:
        yield
    finally:
        pending = [t for t in (sim._loop_task, sim._affinity_task) if t]
        sim.stop()
        background.cancel()
        tasks = [background, *pending]
        if _identity_task:
            _identity_task.cancel()
            tasks.append(_identity_task)
        await asyncio.gather(*tasks, return_exceptions=True)
        if getattr(sim,"save_id",None):save_store().checkpoint(sim,"正常退出自动存档")
        sim.finish_recording()
        for p in sim.players:
            p.memory.close()


app = FastAPI(title="AI 小镇", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def trusted_origin(origin: str | None, host: str) -> bool:
    if not origin:
        return True  # 本地命令行客户端没有 Origin
    parsed = urlsplit(origin)
    return parsed.scheme in ("http", "https") and parsed.netloc == host


@app.middleware("http")
async def local_access(request: Request, call_next):
    if not trusted_origin(request.headers.get("origin"), request.headers.get("host", "")):
        return JSONResponse(status_code=403, content={"detail": "只允许本机同源页面访问"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response

sim = sim_mod.Simulation()
config = AppConfig()
_identity_started = False
_identity_task: asyncio.Task | None = None
_control_lock = asyncio.Lock()


def load_config() -> None:
    global config
    if CONFIG_PATH.exists():
        try:
            config = AppConfig.model_validate_json(CONFIG_PATH.read_text("utf-8"))
        except Exception as e:                          # noqa: BLE001
            print(f"[config] 读取失败，使用默认（{type(e).__name__}，请检查本地配置）")
    _sync_from_config()


def save_config() -> None:
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(config.model_dump_json(indent=2), "utf-8")
    temp.replace(CONFIG_PATH)


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
    # 默认居民按入住槽位持久绑定；旧 UUID 配置按原入住顺序迁移。
    if (sim.players and config.player_bindings
            and not any(p.pid in config.player_bindings for p in sim.players)):
        config.player_bindings = {p.pid: binding for p, binding in
                                  zip(sim.players, config.player_bindings.values())}
    sim.binding_of = {pid: b.get("profile_id", "") for pid, b in
                      config.player_bindings.items()}
    sim.model_of = {pid: b.get("model", "") for pid, b in config.player_bindings.items()}
    sim.record("configuration",{"settings":config.settings.model_dump(),"system_prompt":config.system_prompt,"bindings":config.player_bindings})
    for p in sim.players:
        profile = sim.profile_for(p.pid)
        p.model = sim.model_of.get(p.pid) or (profile.default_model if profile else "")


def save_store():
    return SaveStore(CONFIG_PATH.parent / "data" / "saves")


def apply_saved_context(data):
    context=data.get('runtime_context')
    if not context:return
    config.settings=Settings.model_validate(context['settings'])
    config.system_prompt=context['system_prompt']
    config.active_profile_id=context.get('active_profile_id')
    config.player_bindings=context.get('bindings',{})
    config.player_models={}
    existing={p.id:p for p in config.profiles}
    for profile in context.get('profiles',[]):
        key=existing[profile['id']].api_key if profile['id'] in existing else ''
        existing[profile['id']]=ApiProfile(**profile,api_key=key)
    config.profiles=list(existing.values())
    save_config()


async def startup() -> None:
    global sim, _identity_started
    load_config()
    store=save_store()
    current=store.current()
    if current:
        sim=store.restore(current)
    else:
        sid=store.allocate()
        legacy=CONFIG_PATH.parent/'data'/'legacy-import.json'
        if legacy.exists():
            sim=store.import_legacy(json.loads(legacy.read_text('utf-8')),sid)
        else:
            sim.save_id=sid
            sim.memory_dir=store.folder(sid)/'memories'
            if not sim.players:sim.spawn_players(sim_mod.DEFAULT_PLAYER_COUNT)
        store.checkpoint(sim,'首次创建存档')
        store.activate(sid)
    sim.replay_dir=CONFIG_PATH.parent/'data'/'replays'
    if current:apply_saved_context(current)
    _sync_from_config()
    _identity_started=all(p.identity_done for p in sim.players)
    sim.log("system", "🏘️ 小镇服务启动")


async def quiesce():
    global _identity_task
    pending=[t for t in (sim._loop_task,sim._affinity_task,_identity_task) if t]
    sim.stop()
    if _identity_task:_identity_task.cancel();_identity_task=None
    await asyncio.gather(*pending,return_exceptions=True)
    sim.identity_phase=False



# ================================================================ 控制
class ControlCmd(BaseModel):
    cmd: str
    speed: float | None = Field(default=None, ge=0.1, le=10, allow_inf_nan=False)


@app.post("/api/control")
async def control(c: ControlCmd):
    async with _control_lock:
        return await _control(c)


async def _control(c: ControlCmd):
    global sim, _identity_started, _identity_task
    if c.cmd in ("start","resume","step"):
        sim.ensure_recording()
    if c.cmd == "start":
        if not _identity_started:
            _identity_started = True
            _identity_task = asyncio.create_task(_start_with_identity(sim))
        if not sim.running:
            sim.log("system", "▶ 模拟开始")
            sim.start()
    elif c.cmd == "pause":
        sim.pause()
    elif c.cmd == "resume":
        sim.resume()
    elif c.cmd == "stop":
        sim.stop()
        sim.finish_recording()
        if _identity_task:
            _identity_task.cancel()
            _identity_task = None
        sim.identity_phase = False
        _identity_started = False
    elif c.cmd == "step":
        if sim.running:
            raise HTTPException(409, "请先暂停再单步推进")
        if not _identity_started:
            _identity_started = True
            await sim.run_identity_phase()
        if sim.identity_phase:
            return {"ok": False, "note": "身份阶段进行中，请稍候"}
        await sim.step_tick()
    elif c.cmd == "speed":
        if c.speed:
            sim.speed = max(0.1, min(10.0, c.speed))
            sim.log("system", f"🐇 速度调至 {sim.speed}x")
    elif c.cmd == "reset":
        await quiesce()
        old=sim
        store=save_store()
        # 旧世界先安全落盘；保存失败会中止重置，旧世界和记忆仍在。
        store.checkpoint(old,'重置前自动存档')
        old.finish_recording()
        sid=store.allocate()
        fresh=sim_mod.Simulation(memory_dir=store.folder(sid)/'memories')
        fresh.save_id=sid
        fresh.replay_dir=CONFIG_PATH.parent/'data'/'replays'
        try:
            world.reset_maps()
            fresh.spawn_players(sim_mod.DEFAULT_PLAYER_COUNT)
            store.checkpoint(fresh,'新存档')
            store.activate(sid)
        except Exception:
            for scene,rows in store.read(old.save_id)['maps'].items():
                world.grid(scene)[:]=[list(r) for r in rows]
            for p in fresh.players:p.memory.close()
            raise
        for p in old.players:p.memory.close()
        sim=fresh
        _sync_from_config()
        _identity_started=False
        sim.log('system','🔄 旧世界已自动保存，新存档已创建，居民记忆已清空')
        globals()["_last_event_id"]=0
        globals()["_dialogue_sent"]=0
        return {'ok':True,'note':'旧世界已存档，新存档记忆为空','save_id':sid,'archived_save_id':old.save_id}
    else:
        raise HTTPException(400, f"未知命令 {c.cmd}")
    return {"ok": True, "running": sim.running, "speed": sim.speed,
            "day": sim.day, "clock": sim.clock,
            "identity_phase": sim.identity_phase}


async def _start_with_identity(target):
    """首次开始：先跑身份阶段再进入主循环"""
    await target.run_identity_phase()


@app.get("/api/state")
async def state():
    name_of = {p.pid: p.name for p in sim.players}
    return {
        "save_id":getattr(sim,"save_id",None),
        "recording": bool(sim.recorder and not sim.recorder.closed),
        "recording_id": sim.recorder.id if sim.recorder else None,
        "running": sim.running, "speed": sim.speed,
        "day": sim.day, "tick": sim.tick, "clock": sim.clock,
        "identity_phase": sim.identity_phase,
        "town_config":sim.town_config,"market_price":sim.market_price,
        "decor_common": sim.decor_common,
        "players": [p.snapshot(name_of).model_dump() for p in sim.players],
        "vehicles": [v.model_dump() for v in sim.vehicles],
        "active_profile": (sim.profile.name if sim.profile else None),
        "llm_mode": bool(sim.profile and sim.profile.api_key),
    }


@app.get('/api/town/buildings')
async def town_buildings():
    from engine.town_services import catalog
    return catalog(sim)


@app.get('/api/mechanics')
async def mechanics_view():
    from engine.town_services import catalog,prompt_context
    from engine.mechanics import activity_defaults
    from engine.activities import ACTIVITIES
    resident=sim.players[0] if sim.players else None
    sample=''
    if resident:
        resident.town_context=prompt_context(sim,resident)
        resident.transport_context=sim.travel_context(resident)
        resident.user_prompt_template=sim.user_prompt_template
        nearby=[o.name for o in resident.nearby_players([o for o in sim.players if o is not resident])]
        try:sample=brain.build_user_prompt(resident,nearby,sim.clock,sim.decor_common)
        except (ValueError,KeyError) as exc:sample=f'提示词模板错误：{exc}'
    return {'rules':sim.mechanics,'hours':sim.opening_hours,'activities':sim.activity_config,
            'activity_names':{key:ACTIVITIES[key]['name'] for key in activity_defaults()},
            'buildings':catalog(sim),'prompt_template':sim.user_prompt_template or brain.USER_TEMPLATE,
            'system_prompt':brain.get_system_prompt(sim.system_prompt),'prompt_preview':sample,
            'resident_name':resident.name if resident else ''}


class MechanicsUpdate(BaseModel):
    rules: dict
    hours: dict
    activities: dict
    prompt_template: str


@app.put('/api/mechanics')
async def mechanics_update(update:MechanicsUpdate):
    from engine.mechanics import validate_rules,validate_hours,validate_activities
    from string import Formatter
    async with _control_lock:
        if sim.running or sim.identity_phase or sim._tick_lock.locked() or (_identity_task and not _identity_task.done()):
            raise HTTPException(409,'模拟运行中只能查看，请先暂停')
        try:
            rules=validate_rules(update.rules);hours=validate_hours(update.hours)
            activities=validate_activities(update.activities)
            template=update.prompt_template.strip()
            if len(template)>20000:raise ValueError('提示词模板不能超过20000字')
            allowed={'day','clock','daypart','name','gender','scene_name','hp','satiety','energy','mood','money','illness','location','transport','items','nearby','sex_invitation','affinity','decor_common','memories','menu','computer','town','transactions'}
            fields={field for _,field,_,_ in Formatter().parse(template) if field}
            if not fields <= allowed or not {'town','menu','memories'} <= fields:
                raise ValueError('提示词只能使用原有占位符，且必须保留 {town}、{menu}、{memories}')
        except (ValueError,KeyError) as exc:raise HTTPException(400,str(exc))
        old=(sim.mechanics,sim.opening_hours,sim.activity_config,sim.user_prompt_template)
        sim.mechanics,sim.opening_hours,sim.activity_config,sim.user_prompt_template=rules,hours,activities,template
        try:save_store().checkpoint(sim,'修改机制')
        except Exception:
            sim.mechanics,sim.opening_hours,sim.activity_config,sim.user_prompt_template=old
            raise
        sim.record('configuration',{'mechanics':rules,'opening_hours':hours,'activity_config':activities,'user_prompt_template':template})
        sim.log('system','机制参数已更新',resident_memory=False)
        return {'ok':True}


class BuildingParameters(BaseModel):
    parameters: dict[str,dict[str,float]]


@app.put('/api/town/buildings/{code}')
async def update_building(code:str,update:BuildingParameters):
    from engine.town_services import validate_parameters
    async with _control_lock:
        if sim.running or sim.identity_phase or sim._tick_lock.locked() or (_identity_task and not _identity_task.done()):
            raise HTTPException(409,'模拟运行中只能查看，请先暂停')
        try:values=validate_parameters(code,update.parameters)
        except ValueError as exc:raise HTTPException(400,str(exc))
        previous=sim.town_config.get(code)
        sim.town_config[code]=values
        try:save_store().checkpoint(sim,'修改建筑参数')
        except Exception:
            if previous is not None:sim.town_config[code]=previous
            raise
        sim.record('configuration',{'town_config':sim.town_config})
        sim.log('system','建筑参数已更新，新活动使用新参数')
        return {'ok':True}


@app.get("/api/events")
async def events(day_from: int | None = None, day_to: int | None = None,
                 type_: str | None = None, q: str | None = None,
                 scope: Literal['game','user'] | None = None,
                 after_id: int = 0, limit: int = Query(default=200, ge=1, le=1000)):
    out = []
    for e in reversed(sim.events):
        if e.id <= after_id:
            break
        if scope and e.scope != scope: continue
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
    format: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: str
    default_model: str = ""


class ProfileEdit(ProfileIn):
    api_key: str = ""  # 编辑时留空沿用本机密钥，不回传给页面。


@app.get("/api/profiles")
async def list_profiles():
    return {"profiles": [p.model_dump(exclude={"api_key"}) for p in config.profiles],
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
    return prof.model_dump(exclude={"api_key"})


@app.delete("/api/profiles/{pid}")
async def del_profile(pid: str):
    config.profiles = [p for p in config.profiles if p.id != pid]
    if config.active_profile_id == pid:
        config.active_profile_id = config.profiles[0].id if config.profiles else None
    for binding in config.player_bindings.values():
        if binding.get("profile_id") == pid:
            binding["profile_id"] = ""
    save_config(); _sync_from_config()
    return {"ok": True}


@app.put("/api/profiles/{pid}")
async def edit_profile(pid: str, update: ProfileEdit):
    async with _control_lock:
        old = next((p for p in config.profiles if p.id == pid), None)
        if old is None:
            raise HTTPException(404, "档案不存在")
        values = update.model_dump()
        for key in ("name", "base_url", "default_model", "api_key"):
            values[key] = values[key].strip()
        if not values["name"] or not values["base_url"]:
            raise HTTPException(400, "名称和地址不能为空")
        values["api_key"] = values["api_key"] or old.api_key
        prof = ApiProfile(id=pid, **values)
        config.profiles = [prof if p.id == pid else p for p in config.profiles]
        save_config(); _sync_from_config()
        # 下次启动会恢复世界中的档案元数据，因此同时更新当前检查点。
        if getattr(sim, "save_id", None):
            save_store().checkpoint(sim, "修改模型档案")
        return prof.model_dump(exclude={"api_key"})


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
    model = (t.model or prof.default_model or "").strip()
    if not model:
        model = next((b.get("model", "").strip() for b in config.player_bindings.values()
                      if (b.get("profile_id") or config.active_profile_id) == prof.id
                      and b.get("model", "").strip()), "")
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, test_profile, prof, model, config.settings.decision_timeout,
        config.settings.temperature)
    return {"ok": ok, "message": msg}


# ---------- 模型列表（新增） ----------
@app.get("/api/models")
async def list_models(profile_id: str | None = None):
    return await _list_models(profile_id=profile_id)


class ModelDraft(ProfileEdit):
    profile_id: str | None = None


@app.post("/api/models")
async def draft_models(draft: ModelDraft):
    key = draft.api_key.strip()
    if not key and draft.profile_id:
        prof = next((p for p in config.profiles if p.id == draft.profile_id), None)
        if not prof:
            raise HTTPException(404, "档案不存在")
        key = prof.api_key
    if not draft.base_url.strip() or not key:
        raise HTTPException(400, "请填写地址和密钥")
    return await _list_models(base_url=draft.base_url.strip(), api_key=key, format=draft.format)


async def _list_models(profile_id=None, base_url=None, api_key=None, format="openai"):
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
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    decision_timeout: float | None = Field(default=None, ge=5, le=300, allow_inf_nan=False)
    ticks_per_second: float | None = Field(default=None, ge=0.1, le=10, allow_inf_nan=False)
    player_bindings: dict[str, "PlayerBinding"] | None = None


class PlayerBinding(BaseModel):
    profile_id: str = ""
    model: str = ""


@app.put("/api/config")
async def put_config(s_in: SettingsIn):
    if s_in.player_bindings is not None:
        valid_players = {p.pid for p in sim.players}
        valid_profiles = {p.id for p in config.profiles} | {""}
        if any(pid not in valid_players or b.profile_id not in valid_profiles
               for pid, b in s_in.player_bindings.items()):
            raise HTTPException(400, "玩家或 API 档案不存在")
    if s_in.dialogue_generation is not None:
        config.settings.dialogue_generation = s_in.dialogue_generation
    if s_in.temperature is not None:
        config.settings.temperature = max(0.0, min(2.0, s_in.temperature))
    if s_in.decision_timeout is not None:
        config.settings.decision_timeout = max(5.0, min(300.0, s_in.decision_timeout))
    if s_in.ticks_per_second is not None:
        config.settings.ticks_per_second = max(0.1, min(10.0, s_in.ticks_per_second))
        sim.speed = config.settings.ticks_per_second
    if s_in.player_bindings is not None:
        config.player_bindings = {pid: b.model_dump() for pid, b in s_in.player_bindings.items()}
        _sync_from_config()
        save_config()
        return {"ok": True}
    save_config(); _sync_from_config()
    return {"ok": True}


# ================================================================ 玩家
class PlayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=20, pattern=r"^[^<>\x00-\x1f]+$")
    color: str = Field(default="#7f8c8d", pattern=r"^#[0-9a-fA-F]{6}$")
    emoji: str = Field(default="🙂", max_length=16)


class TravelIn(BaseModel):
    mode: Literal["walk", "bicycle", "car"]


@app.put("/api/players/{pid}/travel")
async def player_travel(pid: str, travel: TravelIn):
    p = sim.by_pid(pid)
    if not p:
        raise HTTPException(404, "玩家不存在")
    try:
        sim.set_travel_mode(p, travel.mode)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return p.snapshot().model_dump()


@app.post("/api/players/{pid}/car")
async def buy_car(pid: str):
    p = sim.by_pid(pid)
    if not p:
        raise HTTPException(404, "玩家不存在")
    try:
        car = sim.buy_car(p)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "vehicle": car.model_dump()}


class RideIn(BaseModel):
    driver_id: str | None = None


@app.put("/api/players/{pid}/ride")
async def player_ride(pid: str, ride: RideIn):
    p = sim.by_pid(pid)
    if not p:
        raise HTTPException(404, "玩家不存在")
    if ride.driver_id is None:
        sim.leave_vehicle(p)
        sim.log("system", f"{p.name} 已下车", [p.pid],scope="game")
        return {"ok": True}
    try:
        sim.board_car(p, sim.by_pid(ride.driver_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


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
async def player_memories(name: str, tier: int | None = Query(default=None, ge=1, le=3),
                          offset: int = Query(default=0, ge=0), limit: int = Query(default=40, ge=1, le=100)):
    p = next((p for p in sim.players if p.name == name or p.pid == name), None)
    if not p:
        raise HTTPException(404, "玩家不存在")
    entries = [m.model_dump() for m in p.memory.page(tier, offset, limit)]
    if tier is None:  # 兼容旧版只读取数组的客户端。
        return entries
    counts = p.memory.counts()
    return {"items": entries, "counts": counts, "tier": tier,
            "has_more": offset+len(entries) < counts[str(tier)], "last_selection": p.memory.last_selection}


@app.get('/api/saves')
async def list_saves():
    return {'current':getattr(sim,'save_id',None),'items':save_store().listing()}


@app.post('/api/saves/current')
async def save_current():
    async with _control_lock:
        await quiesce()
        store=save_store();data=store.checkpoint(sim,'当前世界自动检查点')
        sid=store.allocate();data={**data,'id':sid,'reason':'手动保存',**store.metadata(sid)}
        path=store.folder(sid)/'state.json';temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),'utf-8');temp.replace(path)
        return {'ok':True,'id':sid}


class SaveMetadataUpdate(BaseModel):
    name: str | None = Field(default=None,min_length=1,max_length=80)
    favorite: bool | None = None


@app.put('/api/saves/{sid}/metadata')
async def update_save_metadata(sid:str,update:SaveMetadataUpdate):
    async with _control_lock:
        try:return save_store().update_metadata(sid,update.model_dump(exclude_none=True))
        except FileNotFoundError:raise HTTPException(404,'未找到存档')
        except ValueError as exc:raise HTTPException(400,str(exc))


@app.delete('/api/saves/{sid}')
async def delete_save(sid:str):
    async with _control_lock:
        try:save_store().delete(sid)
        except FileNotFoundError:raise HTTPException(404,'未找到存档')
        except ValueError as exc:raise HTTPException(409,str(exc))
        return {'ok':True}


async def continue_checkpoint(data,source):
    global sim,_identity_started,config
    store=save_store()
    await quiesce()
    old=sim
    store.checkpoint(old,'切换存档前自动保存')
    old.finish_recording()
    branch=store.allocate();candidate=None
    previous_config=config.model_copy(deep=True)
    try:
        candidate=store.restore(data,branch)
        store.checkpoint(candidate,'从所选位置继续')
        apply_saved_context(data)
        store.activate(branch)
    except Exception:
        config=previous_config
        save_config()
        for scene,rows in store.read(old.save_id)['maps'].items():
            world.grid(scene)[:]=[list(r) for r in rows]
        if candidate:
            for p in candidate.players:p.memory.close()
        raise
    for p in old.players:p.memory.close()
    sim=candidate;sim.replay_dir=CONFIG_PATH.parent/'data'/'replays'
    _sync_from_config();_identity_started=all(p.identity_done for p in sim.players)
    globals()['_last_event_id']=0;globals()['_dialogue_sent']=0
    return {'ok':True,'id':branch,'source':source}


@app.post('/api/saves/{sid}/load')
async def load_save(sid:str):
    async with _control_lock:
        store=save_store()
        try:data=store.read(sid)
        except (ValueError,FileNotFoundError):raise HTTPException(404,'未找到存档')
        return await continue_checkpoint(data,sid)


@app.get('/api/saves/{sid}/frames/{index}')
async def save_replay_frame(sid:str,index:int):
    try:
        store=save_store()
        result=store.replay_frame(store.read(sid),CONFIG_PATH.parent/'data'/'replays',index)
        result['can_continue']=bool(result['frame'].get('checkpoint'))
        return result
    except (ValueError,FileNotFoundError,IndexError) as exc:raise HTTPException(404,str(exc))


@app.post('/api/saves/{sid}/frames/{index}/continue')
async def continue_save_frame(sid:str,index:int):
    async with _control_lock:
        try:
            store=save_store();frame=store.replay_frame(store.read(sid),CONFIG_PATH.parent/'data'/'replays',index)['frame']
        except (ValueError,FileNotFoundError,IndexError) as exc:raise HTTPException(404,str(exc))
        if not frame.get('checkpoint'):raise HTTPException(409,'旧录制帧没有完整上下文，可从存档条目的保存位置继续')
        return await continue_checkpoint(frame['checkpoint'],sid)


@app.post('/api/replays/{rid}/frames/{index}/continue')
async def continue_replay_frame(rid:str,index:int):
    async with _control_lock:
        try:frame=read_frame(CONFIG_PATH.parent/'data'/'replays',rid,index)['frame']
        except (ValueError,FileNotFoundError,IndexError) as exc:raise HTTPException(404,str(exc))
        if not frame.get('checkpoint'):raise HTTPException(409,'旧录制帧没有完整上下文，可从世界存档继续')
        return await continue_checkpoint(frame['checkpoint'],rid)


@app.get("/api/replays")
async def list_replays():
    return recordings(CONFIG_PATH.parent / "data" / "replays")


@app.get("/api/replays/{rid}/frames/{index}")
async def replay_frame(rid: str,index: int):
    try:
        result=read_frame(CONFIG_PATH.parent / "data" / "replays",rid,index)
        result["can_continue"]=bool(result["frame"].get("checkpoint"))
        return result
    except (ValueError,FileNotFoundError,IndexError) as exc:
        raise HTTPException(404,str(exc))


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
    last_version = None
    while True:
        await asyncio.sleep(0.15)
        version = (id(sim), sim.global_tick, sim.running, sim.identity_phase, sim.event_id, sim.speed)
        if version != last_version and manager.clients:
            last_version = version
            new_events = [e.model_dump() for e in sim.events if e.id > _last_event_id]
            if new_events:
                globals()["_last_event_id"] = max(e["id"] for e in new_events)
            name_of = {p.pid: p.name for p in sim.players}
            payload = {
                "type": "tick",
                "day": sim.day, "clock": sim.clock, "tick": sim.global_tick,
                "recording": bool(sim.recorder and not sim.recorder.closed),
                "recording_id": sim.recorder.id if sim.recorder else None,
                "save_id": sim.save_id,
                "running": sim.running, "speed": sim.speed,
                "identity_phase": sim.identity_phase,
                "town_config":sim.town_config,"market_price":sim.market_price,
                "decor_common": sim.decor_common,
                "players": [p.snapshot(name_of).model_dump() for p in sim.players],
                "vehicles": [v.model_dump() for v in sim.vehicles],
                "events": new_events,
                "maps": {scene: ["".join(row) for row in world.grid(scene)] for scene in ("house", "town")},
            }
            await manager.broadcast(payload)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if (ws.headers.get("host", "").split(":")[0] not in ("127.0.0.1", "localhost", "testserver")
            or not trusted_origin(ws.headers.get("origin"), ws.headers.get("host", ""))):
        await ws.close(code=1008)
        return
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.exception_handler(Exception)
async def any_error(request, exc):
    return JSONResponse(status_code=500, content={"detail": "服务发生异常，请查看本地运行日志"})


if __name__ == "__main__":
    import uvicorn
    print("=" * 56)
    print("  AI Town  http://127.0.0.1:8300")
    print("=" * 56)
    uvicorn.run(app, host="127.0.0.1", port=8300)
