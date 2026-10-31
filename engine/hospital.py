"""紧急送医、室友借款、住院与死亡判定。

流程（被动结算每刻检查）：
1. 生命 ≤ hospital_trigger_hp（默认10）且未在住院/借款流程中 → 紧急送医：
   - 先用自有现金支付 hospital_cost（默认1200）
   - 现金不足 → 逐个室友“打电话”借钱（室友的模型决定借多少，上限为其现金），
     逐笔入账直至补齐 → 收治住院
   - 全部室友问完仍不足 → 不收治：留在原地继续承受数值结算
2. 住院半天计费制（hospital_fee_ticks 累计至 72 刻一结算）：
   - 收治立即做急救处置（输血/输液）：生命 +20、心情 +15、饱食/精力回满
   - 每半天结算房费 100 元，同样恢复生命 +20、心情 +15、饱食/精力满
   - 付不起房费 → 自动出院；模型随时可选 hospital_stay / hospital_leave
3. 未收治者生命归零 → 判定死亡：移除玩家、清理关系、室友获得记忆与播报。

借款记账：loans 字典（债主pid → 累计借款）。
流水格式与 resident_tools 统一：{id, day, clock, tick, actor, from, to, amount, reason}。
"""
from __future__ import annotations

import random
import uuid

HALF_DAY_TICKS = 72          # 144刻/天 ÷ 2


def _tx(sim, actor, from_p, to_p, amount: float, reason: str) -> None:
    """按前端资金往来面板的标准格式记账（双方都记一条）。"""
    entry = {'id': uuid.uuid4().hex, 'day': sim.day, 'clock': sim.clock,
             'tick': sim.global_tick, 'actor': actor.name,
             'from': from_p.name, 'to': to_p.name,
             'amount': float(amount), 'reason': reason}
    from_p.transactions.append(dict(entry))
    to_p.transactions.append(dict(entry))


def trigger(sim, p) -> bool:
    """送医触发：生命低于阈值，且不在住院/急救流程中。
    被拒收者要等生命回升到阈值以上（健康恢复过一次）才允许再走借款流程。"""
    if getattr(p, 'refused_hospital', False):
        return p.stats.hp > sim.mechanics['hospital_trigger_hp']  # 只用于清除标记的探测
    return (p.stats.hp <= sim.mechanics['hospital_trigger_hp']
            and not getattr(p, 'hospitalized', False)
            and not getattr(p, 'awaiting_admission', False))


def clear_refused(sim, p) -> bool:
    """生命回升到阈值以上时清除拒收标记（下次跌破可再送医）。"""
    if getattr(p, 'refused_hospital', False) and p.stats.hp > sim.mechanics['hospital_trigger_hp']:
        p.refused_hospital = False
        return True
    return False


def hospital_tiles(sim):
    import engine.world as world
    return world.find_tiles("town", "B")


def start_emergency(sim, p) -> None:
    """生命过低触发紧急送医：现金够直接收治；不够则借款凑钱，
    凑不齐做收费的基本处置（借款全额退还，绝不留在病人口袋）。"""
    cost = sim.mechanics['hospital_cost']
    cash = p.stats.money
    p.awaiting_admission = True
    if cash >= cost:
        admit(sim, p)          # 收费统一在 admit 内完成
        return
    need = cost - cash
    p.debt_goal = need         # 借款缺口；现金保留在身上，凑齐后由诊所统一收取
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


def _clinic(sim):
    """医院收款方（虚拟记账对象）。"""
    class _Clinic:
        name = "诊所"
        transactions = []      # 类级共享，仅作流水落地处
    return _Clinic()


def pay(sim, p, lender, amount: float) -> None:
    """借款转账：室友 → 患者（钱随后由患者转付医院）。"""
    lender.stats.money = max(0.0, lender.stats.money - amount)
    p.stats.money += amount
    p.loans[lender.pid] = p.loans.get(lender.pid, 0.0) + amount
    _tx(sim, lender, lender, p, amount, f"借钱给{p.name}支付急救费")


def refuse(sim, p, need: float) -> None:
    """借款不足：不收治住院。按「先救人后收费」做最低处置——
    借款全额退还债主（分文不留地退回，防止病人反而最有钱），
    处置收取基本费用 min(现金,100)，再保底生命/饱食/心情。"""
    p.refused_hospital = True
    refunded = 0.0
    for lender_pid, amount in list(p.loans.items()):
        lender = sim.by_pid(lender_pid)
        if lender is not None and amount > 0:
            lender.stats.money = round(lender.stats.money + amount, 2)
            _tx(sim, p, p, lender, amount, f"退还急救借款（{p.name}未被收治）")
            lender.remember(f"诊所没收治{p.name}，我把借出的{amount:.0f}元拿回来了", 5)
            refunded += amount
    p.stats.money = round(max(0.0, p.stats.money - refunded), 2)
    borrowed = sum(p.loans.values())
    p.loans = {}
    fee = min(p.stats.money, sim.mechanics.get('basic_treatment_fee', 100.0))
    if fee > 0:
        p.stats.money = round(p.stats.money - fee, 2)
        _tx(sim, p, p, _clinic(sim), fee, "基本处置费（未收治）")
    p.stats.hp = max(p.stats.hp, 25.0)
    p.stats.satiety = max(p.stats.satiety, 30.0)
    p.stats.mood = max(p.stats.mood, 20.0)
    p.stats.clamp()
    p.remember(f"凑不齐{need:.0f}元急救费，诊所收了{fee:.0f}元做了基本处置让我先活下来，借的钱都退给了室友", 9)
    sim.log("system", f"💸 {p.name} 凑不齐{sim.mechanics['hospital_cost']:.0f}元急救费，只做基本处置"
                      f"（处置费{fee:.0f}元，借款已退还{refunded:.0f}元；生命25/饱食30保底）",
            [p.name], scope="game")


def admit(sim, p, note: str = "") -> None:
    """收治住院：在此统一收取急救费（现金+借款），传送诊所、暂停行动、急救处置。"""
    cost = sim.mechanics['hospital_cost']
    pay = min(p.stats.money, cost)
    if pay > 0:
        p.stats.money = round(p.stats.money - pay, 2)
        _tx(sim, p, p, _clinic(sim), pay, "支付急救费" + (note or ""))
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
    p.refused_hospital = False
    p.hospitalized = True
    p.hospital_fee_ticks = 0            # 距下次房费结算已过刻数
    p.illness_severity = 0
    p.illness_until = 0
    p.illness_name = ""
    p.remember(f"我被送进诊所急救{note}，已缴费收治住院", 9)
    sim.log("system", f"🏥 {p.name} 缴清{sim.mechanics['hospital_cost']:.0f}元急救费，收治住院（房费每半天{sim.mechanics['hospital_fee_halfday']:.0f}元）",
            [p.name], scope="game")
    # 收治急救处置（与房费解耦：借款恰好只够急救费时也能住下，
    # 房费等72刻后的首次结算再收，付不起那时再出院）
    p.stats.hp = max(p.stats.hp, 30.0)
    p.stats.mood = min(100.0, p.stats.mood + sim.mechanics['hospital_mood_gain'])
    p.stats.satiety = 100.0
    p.stats.energy = 100.0
    p.stats.clamp()
    p.remember(f"诊所做了收治急救处置，付了急救费，生命持续恢复中", 3)
    p.ask_stay = True


def halfday_tick(sim, p) -> None:
    """住院半天结算：付房费 → 心情+15、饱食/精力满；付不起 → 自动出院。
    （收治时的急救处置已在 admit 内与房费解耦。）"""
    fee = sim.mechanics['hospital_fee_halfday']
    if p.stats.money < fee:
        p.remember(f"我付不起{fee:.0f}元房费，只能出院了", 4)
        sim.log("system", f"💵 {p.name} 付不起住院房费，已自动出院",
                [p.name], scope="game")
        discharge(sim, p)
        return
    p.stats.money -= fee
    _tx(sim, p, p, _clinic(sim), fee, "住院房费（半天）")
    p.stats.mood = min(100.0, p.stats.mood + sim.mechanics['hospital_mood_gain'])
    p.stats.satiety = 100.0
    p.stats.energy = 100.0
    p.stats.clamp()
    p.remember(f"住院半天，付了{fee:.0f}元房费，生命持续恢复中", 3)
    sim.log("system", f"🛏 {p.name} 住院半天：房费{fee:.0f}元，心情+{sim.mechanics['hospital_mood_gain']:.0f}，生命按自然恢复{sim.mechanics['hospital_heal_multiplier']:.0f}倍每刻恢复（住院期间饱食/精力保持满值）",
            [p.name], scope="game")
    p.ask_stay = True                   # 结算后请模型决定是否续住


def hospital_core_discharge(sim, p, reason: str) -> None:
    """付不起房费的最低处置出院：不做四维保底（钱已耗尽）。"""
    p.hospitalized = False
    p.hospital_fee_ticks = 0
    p.ask_stay = False
    p.state = "idle"
    p.remember(f"我{reason}", 4)
    sim.log("system", f"出院 {p.name}：{reason}", [p.name], scope="game")


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
