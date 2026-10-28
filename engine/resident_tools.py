"""居民自主工具：文字电脑、表情和不限定动机的资金渠道。"""
from decimal import Decimal, InvalidOperation
import json
import uuid
import math

from . import world

EMOJIS={'❤️','❤','😊','😂','😢','😭','😠','😡','👍','👋','😮','🥰','💔'}


def computer_seat(p):
    i=min(range(len(world.BEDROOM_ANCHORS)),key=lambda i:abs(world.BEDROOM_ANCHORS[i][0]-p.bedroom[0])+abs(world.BEDROOM_ANCHORS[i][1]-p.bedroom[1]))
    x,y,_,_=world.BEDROOM_RECTS[i]
    # 桌子前方的地面格，家具实体仍然阻挡。
    return world.nearest_walkable('house',x+(3 if i==4 else 4),y+(2 if i==4 else 3))


def computer_context(p):
    files=p.computer_files[:20]
    lines=[f'共{len(p.computer_files)}条记录；'+('列表如下：' if files else '暂无记录。')]
    lines.extend(f"- {f['id']}｜{f['title']}（{len(f['content'])}字）" for f in files)
    if len(p.computer_files)>20:lines.append('更多记录可用 computer/list 和 offset 翻页。')
    if p.computer_feedback:lines.append('上次电脑操作结果：'+p.computer_feedback)
    return '\n'.join(lines)


def use_computer(sim,p,d):
    if d is None or d.action!='computer' or p.computer_operation_done:return
    p.computer_operation_done=True
    file=next((f for f in p.computer_files if f['id']==d.file_id),None)
    op=d.operation
    if op=='create':
        file={'id':uuid.uuid4().hex[:12],'title':d.title or '未命名记录','content':d.content,'created':sim.global_tick,'updated':sim.global_tick}
        p.computer_files.append(file)
        result={'created':file['id'],'title':file['title']}
    elif op in ('edit','delete','read'):
        if not file:
            p.computer_feedback='记录不存在，请先list查看自己的记录编号。'
            return
        if op=='edit':
            file.update(title=d.title or file['title'],content=d.content,updated=sim.global_tick)
            result={'edited':file['id'],'title':file['title']}
        elif op=='delete':
            p.computer_files.remove(file);result={'deleted':file['id']}
        else:
            result={'id':file['id'],'title':file['title'],'offset':d.offset,'content':file['content'][d.offset:d.offset+4000],'length':len(file['content'])}
    elif op=='list':
        result={'files':[{'id':f['id'],'title':f['title'],'length':len(f['content'])} for f in p.computer_files[d.offset:d.offset+20]],'total':len(p.computer_files),'offset':d.offset}
    else:
        p.computer_feedback='电脑操作无效，请使用list/create/edit/read/delete。'
        return
    p.computer_feedback=json.dumps(result,ensure_ascii=False)
    sim.log('activity',f'💻 {p.name}在自己的电脑上'+{'create':'创建了记录','edit':'编辑了记录','read':'查看了记录','delete':'删除了记录','list':'查看了记录目录'}[op],[p.pid])


def instant_action(sim,p,d):
    target=next((o for o in sim.players if o.pid==d.target or o.name==d.target),None)
    if target is None:raise ValueError('未找到资金或表情接收人')
    if d.action=='emoji':
        if target is p or d.emoji not in EMOJIS:raise ValueError('请选择一位室友并发送支持的简单表情')
        p.speech=f'{d.emoji} → {target.name}';p.speech_tick=p.world_tick
        sim.log('social',f'{p.name} → {target.name}：{d.emoji}',[p.pid,target.pid])
        return
    sender=p if not d.money_from else next((o for o in sim.players if o.pid==d.money_from or o.name==d.money_from),None)
    if sender is None or sender is target or p not in (sender,target):raise ValueError('资金转移需要自己作为付款或收款方，双方必须不同')
    try:
        amount=Decimal(str(d.amount))
        if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('.01')):raise ValueError('金额必须为正数，最多两位小数')
    except (InvalidOperation,TypeError):raise ValueError('金额无效')
    balance=Decimal(str(sender.stats.money));receive=Decimal(str(target.stats.money))
    if balance<amount:raise ValueError('付款方余额不足，资金未转移')
    if not math.isfinite(float(receive+amount)):raise ValueError('金额超出可用范围，资金未转移')
    sender.stats.money=float(balance-amount);target.stats.money=float(receive+amount)
    entry={'id':uuid.uuid4().hex,'day':sim.day,'clock':sim.clock,'tick':sim.global_tick,'actor':p.name,'from':sender.name,'to':target.name,'amount':float(amount),'reason':d.reason}
    sender.transactions.append(dict(entry));target.transactions.append(dict(entry))
    sim.log('social',f'💰 {sender.name} → {target.name}：{amount:.2f}元'+(f'（{p.name}：{d.reason}）' if d.reason else ''),[sender.pid,target.pid])
