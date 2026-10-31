"""三级记忆：有界工作层、模型/随机共同筛选、永久深层档案。

模型先选择完整数量的候选，再随机抽取其中 80%；其余 20% 从全部待整理记忆中抽取并去重。
原文只在提升成功的事务中删除；三级内容没有淘汰逻辑。
每级晋升压缩比约 10:1（batch 40→4、20→2），深层档案增速受控。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import random
import re
import sqlite3
import uuid

from .models import MemoryItem

ENTRY_CHARS = 240
PROMPT_CHARS = 2400
THRESHOLDS = {1: (48, 10000), 2: (24, 5000)}


_RUNTIME_NOTICE=re.compile(
    r'等待模型|模型.{0,16}(?:回复|决策|决定|重试|重新选择|超时|请求|回应|暂不可执行)'
    r'|整合了记忆|记忆(?:整理|整合|压缩|精简|升级)|(?:整理|整合|压缩)了.{0,4}记忆'
    r'|等待重试|暂未获得有效模型|LLM(?:Error|错误|超时|降级)'
    r'|API.{0,5}(?:请求|调用|连接|密钥|错误)|HTTP\s*[45]\d\d'
    r'|ConnectionError|ReadTimeout|ConnectTimeout|网络(?:请求)?(?:超时|连接失败|异常)'
    r'|上次选择.{0,30}未能执行，请检查',re.I)


def clean_memory_text(text):
    # 历史污染按句过滤；不删除同条记忆里可分离的真实生活经历。
    return ''.join(part for part in re.split(r'(?<=[。！？；;\n])|[\r\n]+',str(text))
                   if not _RUNTIME_NOTICE.search(part)).strip()


class MemoryStream:
    def __init__(self, path: Path | None = None, rng=None) -> None:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, tier INTEGER, data TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS memory_tier ON memories(tier)')
        self.db.commit()
        self.rng = rng or random.Random()
        self._lock = asyncio.Lock()
        self.sink = None
        self.last_selection = '尚未整理'

    def close(self):
        self.db.close()

    def __del__(self):
        if hasattr(self, "db"):
            self.db.close()

    def _insert(self, item):
        self.db.execute('INSERT INTO memories VALUES (?,?,?)',
                        (item.id, item.tier, item.model_dump_json()))

    def add(self, content: str, day: int, tick: int, importance: float = 3.0) -> None:
        text = clean_memory_text(content)
        # 长聊天/活动记录分段入一级，不在入口静默截断。
        added=[]
        with self.db:
            for start in range(0, len(text), ENTRY_CHARS):
                item=MemoryItem(id=uuid.uuid4().hex, content=text[start:start+ENTRY_CHARS],
                                day=day, tick=tick, importance=max(1, min(10, importance)))
                self._insert(item)
                added.append(item.model_dump())
        if self.sink and added:
            self.sink({"added":added,"removed":[]})

    def purge_runtime_notices(self):
        removed,updated=[],[]
        with self.db:
            for m in self.items:
                clean=clean_memory_text(m.content)
                if clean==m.content:continue
                removed.append(m.id)
                self.db.execute('DELETE FROM memories WHERE id=?',(m.id,))
                if clean:
                    m.content=clean;self._insert(m);updated.append(m.model_dump())
        if removed and self.sink:self.sink({'added':updated,'removed':removed})
        return len(removed)

    @property
    def items(self):
        return self.page(None, limit=None)

    def page(self, tier=None, offset=0, limit=40):
        sql, args = 'SELECT data FROM memories', []
        if tier is not None:
            sql += ' WHERE tier=?'
            args.append(tier)
        sql += ' ORDER BY rowid DESC'
        if limit is not None:
            sql += ' LIMIT ? OFFSET ?'
            args += [limit, offset]
        return [MemoryItem.model_validate_json(row[0]) for row in self.db.execute(sql, args)]

    def counts(self):
        result = {str(tier): 0 for tier in (1, 2, 3)}
        for tier, n in self.db.execute('SELECT tier,COUNT(*) FROM memories GROUP BY tier'):
            result[str(tier)] = n
        return result

    def needs_compaction(self, tier):
        n, chars = self.db.execute('SELECT COUNT(*),COALESCE(SUM(length(json_extract(data,\'$.content\'))),0) FROM memories WHERE tier=?', (tier,)).fetchone()
        max_n, max_chars = THRESHOLDS[tier]
        return n >= max_n or chars >= max_chars

    async def maintain(self, selector=None):
        """selector(candidates, count) 返回有序 keep:[{id,summary}]；异常用重要度兜底。"""
        async with self._lock:
            self.purge_runtime_notices()
            promotions = []
            for tier in (1, 2):
                while self.needs_compaction(tier):
                    source = list(reversed(self.page(tier, limit=None)))
                    batch = source[:min(40 if tier == 1 else 20, len(source)-(8 if tier == 1 else 4))]
                    if len(batch) < 2:
                        break
                    # 晋升压缩比约 10:1：batch 40→4 条（L1）、20→2 条（L2）
                    count = max(1, len(batch)//10)
                    active_count = max(1, round(count*0.8))
                    valid = {m.id: m for m in batch}
                    choices, mode = [], '重要度兜底'
                    if selector:
                        try:
                            proposed = await selector(batch, count)
                            seen = set()
                            for choice in proposed:
                                mid = choice.get('id') if isinstance(choice, dict) else None
                                if mid in valid and mid not in seen:
                                    summary = choice.get('summary')
                                    summary = clean_memory_text(summary)[:80] if isinstance(summary, str) else ''
                                    choices.append((valid[mid], summary or valid[mid].content))
                                    seen.add(mid)
                                if len(choices) == count:
                                    break
                            if choices:
                                mode = '模型主动选择'
                        except Exception:
                            # 包括超时和格式错误；取消不捕获，保留原始记录供恢复后重试。
                            choices = []
                    model_ids = {m.id for m, _ in choices}
                    if model_ids and len(model_ids) < count:
                        mode = '模型选择＋重要度补足'
                    ranked = sorted(batch, key=lambda m: (-m.importance, -m.tick))
                    chosen_ids = {m.id for m, _ in choices}
                    for m in ranked:
                        if len(choices) >= count:
                            break
                        if m.id not in chosen_ids:
                            choices.append((m, m.content))
                            chosen_ids.add(m.id)
                    # 模型先提名完整 count 条，随后随机抽取其中 80%，不是直接取前 80%。
                    choices = self.rng.sample(choices, active_count)
                    chosen_ids = {m.id for m, _ in choices}
                    # 全部待整理记录均有机会，包括模型提名但未在上一步抽中的条目。
                    remaining = [m for m in batch if m.id not in chosen_ids]
                    lucky = self.rng.sample(remaining, count-active_count)
                    promoted = []
                    for m, summary in choices + [(m, m.content) for m in lucky]:
                        active = m.id in chosen_ids
                        promoted.append(MemoryItem(id=uuid.uuid4().hex, tier=tier+1,
                            content=re.sub(r'\s+', ' ', summary), day=m.day, tick=m.tick,
                            importance=m.importance, selection_weight=0.8 if active else 0.2,
                            selected_by=('模型主动选择' if m.id in model_ids else '重要度兜底') if active else '随机选择', source_count=m.source_count))
                    # 同一个事务提交目标层和源层删除，失败不会吞掉原记忆。
                    with self.db:
                        for m in promoted:
                            self._insert(m)
                        self.db.executemany('DELETE FROM memories WHERE id=?', [(m.id,) for m in batch])
                    if self.sink:
                        self.sink({"added":[m.model_dump() for m in promoted],"removed":[m.id for m in batch]})
                    self.last_selection = mode
                    promotions.append((tier, len(batch), len(promoted), mode))
            return promotions

    def retrieve(self, now_tick: int, k: int = 8, query: str = '') -> list[MemoryItem]:
        terms = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-zA-Z]{3,}', query))
        candidates = []
        for tier in (1, 2, 3):
            recent = self.page(tier, limit=40)
            # 三级不全部加载进上下文/内存，额外从整个档案中按关键词检索。
            if tier == 3 and terms:
                terms_list = sorted(terms)[:8]
                sql = 'SELECT data FROM memories WHERE tier=3 AND (' + ' OR '.join('json_extract(data,\'$.content\') LIKE ?' for _ in terms_list) + ') ORDER BY rowid DESC LIMIT 40'
                recent += [MemoryItem.model_validate_json(r[0]) for r in self.db.execute(sql, ['%'+t+'%' for t in terms_list])]
            unique={}
            for m in recent:
                clean=clean_memory_text(m.content)
                if clean:
                    m=m.model_copy(update={'content':clean});unique[m.id]=m
            def score(m):
                age = max(0, now_tick-m.tick)
                recency = 1 if m.tier == 3 else 0.5**(age/60)
                return sum(t in m.content for t in terms)*2 + m.importance/10 + recency*0.6 + m.selection_weight*0.4
            ranked = sorted(unique.values(), key=score, reverse=True)
            candidates.append(ranked)
        # 每层至少获得一个检索名额，避免深层被大量新记忆挤掉。
        picked = []
        while len(picked) < k and any(candidates):
            for group in candidates:
                if group and len(picked) < k:
                    picked.append(group.pop(0))
        return picked

    def brief(self, now_tick: int, k: int = 8, query: str = '', budget: int = PROMPT_CHARS) -> str:
        lines, used = [], 0
        for m in self.retrieve(now_tick, k, query):
            line = f'[L{m.tier}·第{m.day}天·{m.tick}] {m.content}'
            if used+len(line)+1 > budget:
                continue
            lines.append(line)
            used += len(line)+1
        return '\n'.join(lines) or '(暂无记忆)'
