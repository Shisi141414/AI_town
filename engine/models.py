"""数据模型层 —— 所有可序列化状态的定义

pydantic v2 BaseModel：既做运行时类型校验，也直接用于 API/WS 序列化。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 数值
class Stats(BaseModel):
    """玩家生存数值。hp/satiety/energy/mood 范围 0~100，money >= 0"""
    hp: float = 100.0        # 生命值：归零触发强制急救
    satiety: float = 80.0    # 饱食度：归零后持续扣血
    energy: float = 90.0     # 精力：大多数活动消耗，睡觉恢复
    mood: float = 70.0       # 心情
    money: float = 100.0     # 财产

    def clamp(self) -> None:
        self.hp = min(100.0, max(0.0, self.hp))
        self.satiety = min(100.0, max(0.0, self.satiety))
        self.energy = min(100.0, max(0.0, self.energy))
        self.mood = min(100.0, max(0.0, self.mood))
        self.money = max(0.0, self.money)


# ---------------------------------------------------------------- 玩家快照
class PlayerSnapshot(BaseModel):
    """每个 tick 广播给前端的玩家视图"""
    pid: str                     # 稳定内部 ID（模型可改名，绑定靠它）
    name: str
    color: str
    emoji: str
    scene: str = "house"         # house / town
    x: int = 0
    y: int = 0
    stats: Stats = Stats()
    state: str = "idle"          # idle / deciding / moving / doing / away
    activity: str = ""
    activity_emoji: str = ""
    target_name: str = ""
    plan: str = ""
    model: str = ""
    home: str = ""
    speech: str = ""
    affinity: dict[str, float] = Field(default_factory=dict)
    items: dict[str, int] = Field(default_factory=dict)     # 物品栏
    decor_room: float = 0.0      # 自己卧室装饰度
    identity_done: bool = False  # 开局身份选择是否完成


# ---------------------------------------------------------------- 记忆
class MemoryItem(BaseModel):
    content: str
    day: int = 1
    tick: int = 0
    importance: float = 3.0      # 1~10，影响检索权重


# ---------------------------------------------------------------- 事件
class GameEvent(BaseModel):
    """事件日志条目 —— 后台『查看一切事情』的数据基础"""
    id: int = 0
    day: int = 1
    tick: int = 0
    clock: str = ""              # 游戏内时刻 "08:30"
    wall: float = 0.0            # 真实世界 unix 时间戳
    type: str = "info"           # system/activity/social/combat/dialogue/error
    text: str = ""
    players: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- 决策
class Decision(BaseModel):
    action: str = ""             # 活动 key（英文）或中文名
    target: str = ""             # 目标玩家名 / 设施名 / 空
    reason: str = ""
    source: str = "rule"         # llm / rule —— 用于对比观察


# ---------------------------------------------------------------- API 档案（CCswitch 风格）
class ApiProfile(BaseModel):
    id: str
    name: str
    format: str = "openai"       # openai / anthropic —— 上游格式
    base_url: str = ""           # OpenAI 格式填到 /v1；Anthropic 填根地址
    api_key: str = ""
    default_model: str = ""      # 新玩家/未指定模型玩家的默认模型


class Settings(BaseModel):
    ticks_per_second: float = 1.0     # 模拟速度（1 tick = 游戏内 10 分钟）
    dialogue_generation: bool = True  # 聊天/恋爱时是否额外调用 LLM 生成对白
    decision_timeout: float = 25.0    # LLM 决策超时秒数，超时走规则引擎
    temperature: float = 0.8


class AppConfig(BaseModel):
    """config.json 的完整结构，任何修改即时落盘"""
    profiles: list[ApiProfile] = Field(default_factory=list)
    active_profile_id: str | None = None
    player_bindings: dict[str, dict[str, str]] = Field(
        default_factory=dict)      # pid → {"profile_id":..., "model":...}
    player_models: dict[str, str] = Field(default_factory=dict)  # 旧版迁移用
    settings: Settings = Settings()
    system_prompt: str = ""       # 可编辑的统一提示词（空=用内置默认）
