"""记忆流 —— 借鉴斯坦福 Generative Agents 论文的简化版

论文三层机制我们保留两层：
1. 观察记忆：发生的事情进流，带重要度分数
2. 检索：recency(时间越近权重越高) × importance(重要度) 加权排序
（反思层 reflection 暂未实现——留给后期迭代，接口已预留）
"""
from __future__ import annotations

from .models import MemoryItem

MAX_MEMORIES = 120          # 超出后丢弃最不重要的旧记忆
RECENCY_HALF_LIFE = 60.0    # tick 数：60 tick(游戏内10小时) 权重减半


class MemoryStream:
    def __init__(self) -> None:
        self.items: list[MemoryItem] = []

    def add(self, content: str, day: int, tick: int, importance: float = 3.0) -> None:
        self.items.append(MemoryItem(content=content, day=day, tick=tick,
                                     importance=importance))
        if len(self.items) > MAX_MEMORIES:
            # 淘汰：先按重要度升序、再按时间升序，去掉最没价值的
            ranked = sorted(self.items,
                            key=lambda m: (m.importance, m.tick))
            self.items.remove(ranked[0])

    def _score(self, m: MemoryItem, now_tick: int) -> float:
        age = max(0, now_tick - m.tick)
        recency = 0.5 ** (age / RECENCY_HALF_LIFE)
        return recency * 0.6 + (m.importance / 10.0) * 0.4

    def retrieve(self, now_tick: int, k: int = 12) -> list[MemoryItem]:
        """取检索分数最高的 k 条（含时间戳前缀，喂给 LLM 做自我认知）"""
        if not self.items:
            return []
        ranked = sorted(self.items, key=lambda m: -self._score(m, now_tick))
        return ranked[:k]

    def brief(self, now_tick: int, k: int = 8) -> str:
        """紧凑文本形式，直接拼进提示词"""
        picked = self.retrieve(now_tick, k)
        if not picked:
            return "(暂无记忆)"
        return "\n".join(f"[第{m.day}天{m.tick:03d}] {m.content}" for m in picked)
