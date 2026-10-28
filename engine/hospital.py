"""紧急送医、室友借款、住院与死亡判定。

流程（被动结算每刻检查）：
1. 生命 ≤ hospital_trigger_hp（默认10）且未在住院/借款流程中 → 紧急送医：
   - 先用自有现金支付 hospital_cost（默认1200）
   - 现金不足 → 逐个室友“打电话”借钱（室友的模型决定借多少，上限为其现金），
     逐笔入账直至补齐 → 收治住院
   - 全部室友问完仍不足 → 不收治：留在原地继续承受数值结算
2. 收治后进入住院状态（hospitalized_until=0 表示住院中，正值为借款到期）：
   - 每半天（72刻）结算一次房费100元，换来生命+20、心情+15，
     饱食与精力保持满值——付不起时自动出院
   - 模型随时可选“继续住院/出院”（hospital_stay/hospital_leave）
3. 未收治者生命归零 → 判定死亡：移除玩家、清理关系、室友获得记忆与播报。

借款记账：loans 字典（债主pid → 累计借款），双方 transactions 记流水。
"""
from __future__ import annotations

import random

HALF_DAY_TICKS = 72          # 144刻/天 ÷ 2


def trigger(sim, p) -> bool:
    """送医触发：生命低于阈值，且不在住院/急救流程中，且未被拒收过。
    被拒收后只有等生命回升到阈值以上再跌破才会重新走借款流程。"""
    if getattr(p, 'refused_hospital', False):
        return False
    return (p.stats.hp <= sim.mechanics['hospital_trigger_hp']
            and not getattr(p, 'hospitalized', False)
            and not getattr(p, 'awaiting_admission', False))


def hospital_tiles(sim):
    import engine.world as world
    return world.find_tiles("town", "B")


def start_emergency(sim, p) -> None:
    """生命过低触发紧急送医：付现金、不够则发起借款。"""
    cost = sim.mechanics['hospital_cost']
    cash = p.stats.money
    p.awaiting_admission = True
    if cash >= cost:
        pay(sim, p, None, cost)
        admit(sim, p)
        return
    need = cost - cash
    if cash > 0:
        pay(sim, p, None, cash)
    p.debt_goal = need
    p.loans = {}
    lenders = [o for o in sim.players
               if o is not p and not getattr(o, 'hospitalized', False)]
    for other in lenders:
        sim.loan_queue.append((p.pid, other.pid))
    if not sim.loan_queue:
        p.awaiting_admission = False
        refuse(sim, p, need)
        return
    p.remember(f"急救需要{cost:.0f}元，我只剩{cash:.0f}元，正在打电话向室友借钱", 9)
    sim.log("system", f"📞 {p.name} 现金不足，正打电话向室友借{need:.0f}元急救费",
            [p.name], scope="game")
    sim._kick_loan_queue()


def pay(sim, p, lender, amount: float) -> None:
    """支付急救费（lender=None 为自有现金）。借来的钱直接转付医院。"""
    if lender is not None:
        lender.stats.money = max(0.0, lender.stats.money - amount)
        p.loans[lender.pid] = p.loans.get(lender.pid, 0.0) + amount
        lender.transactions.append({
            "tick": sim.global_tick, "delta": -amount, "balance": lender.stats.money,
            "reason": f"借钱给{p.name}支付急救费"})
        p.transactions.append({
            "tick": sim.global_tick, "delta": +amount, "balance": p.stats.money,
            "reason": f"向{lender.name}借款支付急救费"})
    else:
        p.stats.money = max(0.0, p.stats.money - amount)
        p.transactions.append({
            "tick": sim.global_tick, "delta": -amount, "balance": p.stats.money,
            "reason": "支付急救费"})


def refuse(sim, p, need: float) -> None:
    """借款不足：不收治。标记拒收，避免每刻重复借款。"""
    p.refused_hospital = True
    p.remember(f"凑不齐{need:.0f}元急救费，诊所拒绝收治，我只能硬扛", 9)
    sim.log("system", f"💸 {p.name} 凑不齐{sim.mechanics['hospital_cost']:.0f}元急救费，诊所拒绝收治",
            [p.name], scope="game")


def admit(sim, p, note: str = "") -> None:
    """收治住院：传送诊所、暂停行动、置住院标记。"""
    tiles = hospital_tiles(sim)
    if tiles:
        bx, by = random.choice(tiles)
        p.scene = "town"
        p.x, p.y = sim.free_position("town", bx, by, p)
    sim.leave_vehicle(p)
    sim.drop_passengers(p)
    sim.release_car(p)
    p.activity = None
    p.activity_key = ""
    p.path = []
    p._pending_key = ""
    p.state = "idle"
    p.awaiting_admission = False
    p.hospitalized = True
    p.hospital_fee_ticks = 0            # 距下次房费结算已过刻数
    p.illness_severity = 0
    p.illness_until = 0
    p.illness_name = ""
    p.stats.hp = max(p.stats.hp, 30.0)  # 急救处置：先脱离危险线
    p.ask_stay = True                   # 收治后先问一次是否续住
    p.remember(f"我被送进诊所急救{note}，已缴费收治住院", 9)
    sim.log("system", f"🏥 {p.name} 缴清{sim.mechanics['hospital_cost']:.0f}元急救费，收治住院（房费每半天{sim.mechanics['hospital_fee_halfday']:.0f}元）",
            [p.name], scope="game")


def halfday_tick(sim, p) -> None:
    """住院每半天结算：付100元 → 生命+20、心情+15、饱食/精力保持满。
    付不起房费 → 自动出院。"""
    fee = sim.mechanics['hospital_fee_halfday']
    if p.stats.money < fee:
        p.remember(f"我付不起{fee:.0f}元房费，只能出院了", 4)
        sim.log("system", f"💵 {p.name} 付不起住院房费，已自动出院",
                [p.name], scope="game")
        discharge(sim, p)
        return
    p.stats.money -= fee
    p.transactions.append({
        "tick": sim.global_tick, "delta": -fee, "balance": p.stats.money,
        "reason": "住院房费（半天）"})
    p.stats.hp = min(100.0, p.stats.hp + sim.mechanics['hospital_heal_hp'])
    p.stats.mood = min(100.0, p.stats.mood + sim.mechanics['hospital_mood_gain'])
    p.stats.satiety = 100.0
    p.stats.energy = 100.0
    p.stats.clamp()
    p.remember(f"住院半天，付了{fee:.0f}元房费，生命和心情都有恢复", 3)
    sim.log("system", f"🛏 {p.name} 住院治疗：房费{fee:.0f}元/半天，生命+{sim.mechanics['hospital_heal_hp']:.0f}、心情+{sim.mechanics['hospital_mood_gain']:.0f}",
            [p.name], scope="game")
    p.ask_stay = True                   # 结算后请模型决定是否续住


def discharge(sim, p, reason: str = "康复出院") -> None:
    """出院：清除住院状态，恢复到最低出院水平（不低于现有值）。"""
    m = sim.mechanics
    p.hospitalized = False
    p.hospital_fee_ticks = 0
    p.ask_stay = False
    p.stats.hp = max(p.stats.hp, m['post_hospital_hp'])
    p.stats.satiety = max(p.stats.satiety, m['post_hospital_satiety'])
    p.stats.energy = max(p.stats.energy, m['post_hospital_energy'])
    p.stats.mood = max(p.stats.mood, m['post_hospital_mood'])
    p.stats.clamp()
    p.state = "idle"
    p.remember(f"我{reason}了", 5)
    sim.log("system", f"🎉 {p.name} {reason}", [p.name], scope="game")


def death(sim, p) -> None:
    """生命归零且未被收治 → 死亡。"""
    name = p.name
    for other in sim.players:
        if other is not p:
            other.remember(f"{name}因没有得到及时救治，永远离开了我们", 9)
    sim.log("system", f"🕯 {name} 伤重不治，永远离开了小镇", scope="game")
    sim.remove_player(p.pid)
