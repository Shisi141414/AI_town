"""关系机制：恋爱、挚友等关系的建立（请求-接受）、解除与查询。

- 建立：A 向 B 发出请求（恋爱/挚友），B 在有效期内选择接受即成立；
  拒绝方式是选择任何其他行动（或到期自动作废）
- 解除：任一方单方面「结束关系」立即生效；结束后双方各获得一次
  ±5 额度的好感增减结算（由各自模型依相处经历判定）
- 关系是共享状态（存 sim.relations），随存档持久化；后期可加新类型
"""
from __future__ import annotations

RELATION_TYPES = ('恋爱', '挚友')
PROPOSAL_TTL = 72            # 关系邀请有效期（刻）＝12小时


def pair_key(a_pid: str, b_pid: str) -> str:
    return '|'.join(sorted((a_pid, b_pid)))


def all_of(sim, pid: str) -> list[dict]:
    """某人参与的全部关系。"""
    out = []
    for key, rel in sim.relations.items():
        if pid in (rel['a'], rel['b']):
            out.append(rel)
    return out


def between(sim, a_pid: str, b_pid: str) -> dict | None:
    return sim.relations.get(pair_key(a_pid, b_pid))


def establish(sim, a, b, rtype: str) -> dict:
    """建立关系：状态、双方记忆、好感小幅提升、播报。"""
    rel = {'type': rtype, 'a': a.pid, 'b': b.pid, 'since': sim.global_tick,
           'key': pair_key(a.pid, b.pid)}
    sim.relations[rel['key']] = rel
    for p, o in ((a, b), (b, a)):
        p.affinity[o.pid] = min(100.0, p.affinity.get(o.pid, 50) + 3)
        p.remember(f'我和{o.name}正式确立了{rtype}关系', 8)
    sim.log('social', f'💞 {a.name} 和 {b.name} 确立了{rtype}关系',
            [a.pid, b.pid], scope='game')
    return rel


def end(sim, p, target=None) -> dict | None:
    """单方面结束关系：立即生效；双方各获得一次±5好感增减结算。"""
    rels = all_of(sim, p.pid)
    if target is not None:
        rels = [r for r in rels if target.pid in (r['a'], r['b'])]
    if not rels:
        return None
    rel = rels[0]
    rtype = rel['type']
    other_pid = rel['b'] if rel['a'] == p.pid else rel['a']
    other = sim.by_pid(other_pid)
    sim.relations.pop(rel['key'], None)
    p.remember(f'我结束了和{other.name}的{rtype}关系', 8)
    if other is not None:
        other.remember(f'{p.name}结束了我们的{rtype}关系', 8)
    sim.log('social', f'💔 {p.name} 结束了与{other.name if other else other_pid}的{rtype}关系',
            [p.pid, other_pid], scope='game')
    # 结束后的好感结算：双方各一次 ±5（模型依相处经历判定；60分钟=全额）
    if other is not None:
        sim.request_affinity(p, other, f'结束{rtype}关系', 60)
        sim.request_affinity(other, p, f'被结束{rtype}关系', 60)
    return rel


def describe(p, sim) -> str:
    """提示词「我的关系」行。"""
    rels = all_of(sim, p.pid)
    if not rels:
        return '暂无（可用 propose_love 表白或 propose_friend 结为挚友）'
    parts = []
    for rel in rels:
        other = sim.by_pid(rel['b'] if rel['a'] == p.pid else rel['a'])
        days = (sim.global_tick - rel['since']) // 144 + 1
        parts.append(f"与{other.name if other else '室友'}{rel['type']}中（第{days}天）")
    return '；'.join(parts)


def invitation_text(sim, p) -> str:
    """发给被邀请方的提示词文本；无邀请返回'无'。"""
    prop = sim.relation_proposals.get(p.pid)
    if not prop:
        return '无'
    sender = sim.by_pid(prop['from'])
    if sender is None:
        return '无'
    left = max(0, prop['expires'] - sim.global_tick)
    return (f"{sender.name}向你提出建立「{prop['type']}」关系（{left}刻内有效）："
            f"接受请选 action=relation_accept；选择其他行动视为婉拒")
