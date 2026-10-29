"""商店货架；内置商品沿用原购买逻辑，新商品成为可购买的活动。"""
from __future__ import annotations

from copy import deepcopy
import math
import re


BUILTIN=('buy_flower','buy_plant','car')


def catalog(sim):
    from .activity_rules import catalog as activities
    activity=activities(sim)
    rows={}
    for key in BUILTIN:
        if key=='car':
            rows[key]={'name':sim.shop_car['name'],'emoji':sim.shop_car['emoji'],
                       'price':sim.town_config['S']['car']['price'],'enabled':sim.shop_car['enabled'],
                       'builtin':True}
        else:
            row=activity[key]
            rows[key]={'name':row['name'],'emoji':row['emoji'],'price':row['cost'],
                       'enabled':row['enabled'],'builtin':True}
    for key,row in sim.custom_activities.items():
        if row.get('product_item'):
            rows[key]={'name':row['name'],'emoji':row['emoji'],'price':row['cost'],
                       'enabled':row['enabled'],'builtin':False}
    return rows


def validate(rows):
    if not isinstance(rows,dict) or not set(BUILTIN)<=set(rows) or len(rows)>53:
        raise ValueError('商品列表不完整或超过50件自建商品')
    clean={}
    for key,row in rows.items():
        if key not in BUILTIN and not re.fullmatch(r'buy_[a-z][a-z0-9_]{1,34}',key):
            raise ValueError('商品代码只能使用小写英文、数字与下划线')
        if not isinstance(row,dict) or row.get('builtin')!=(key in BUILTIN) or type(row.get('enabled')) is not bool:
            raise ValueError(f'{key} 商品类型无效')
        name=row.get('name');emoji=row.get('emoji');price=row.get('price')
        if not isinstance(name,str) or not 1<=len(name.strip())<=40 or not isinstance(emoji,str) or not 1<=len(emoji)<=8:
            raise ValueError(f'{key} 名称或图标无效')
        if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or not 0<=price<=100000:
            raise ValueError(f'{key} 价格无效')
        clean[key]={'name':name.strip(),'emoji':emoji,'price':price,'enabled':row['enabled'],'builtin':key in BUILTIN}
    return clean


def apply(sim,rows):
    rows=validate(rows)
    overrides=deepcopy(sim.activity_overrides)
    disabled=set(sim.disabled_activities)
    custom=deepcopy(sim.custom_activities)
    town=deepcopy(sim.town_config)
    car=dict(sim.shop_car)
    for key,row in rows.items():
        if key=='car':
            town['S']['car']['price']=row['price']
            car={field:row[field] for field in ('name','emoji','enabled')}
        elif key in BUILTIN:
            town['S'][key]['price']=row['price']
            ov=overrides.setdefault(key,{})
            ov['name']=row['name'];ov['emoji']=row['emoji']
            ov.pop('cost',None)
            if row['enabled']:disabled.discard(key)
            else:disabled.add(key)
        else:
            old=custom.get(key,{})
            from .activities import ACTIVITIES
            if key in ACTIVITIES or old and not old.get('product_item'):
                raise ValueError(f'{key} 与现有活动代码冲突')
            item='item_'+key[4:]
            custom[key]={**old,'name':row['name'],'emoji':row['emoji'],
                'description':old.get('description',f'在商店购买{row["name"]}'),
                'scene':'town','location':'town:S','places':{'town':['town:S']},
                'target_mode':'none','duration':old.get('duration',1),'cost':row['price'],
                'effects':old.get('effects',{'hp':0,'satiety':0,'energy':0,'mood':0,'money':0}),
                'joint_affinity_per_tick':old.get('joint_affinity_per_tick',0),
                'self_affinity_per_tick':old.get('self_affinity_per_tick',0),
                'target_affinity_per_tick':old.get('target_affinity_per_tick',0),
                'enabled':row['enabled'],'builtin':False,'product_item':item}
            if row['enabled']:disabled.discard(key)
            else:disabled.add(key)
    for key,row in list(custom.items()):
        if row.get('product_item') and key not in rows:
            del custom[key];disabled.discard(key)
    return overrides,list(disabled),custom,town,car
