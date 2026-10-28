"""家具的地面占位与室外固定物体；16px为一格，阴影和地毯不阻挡。"""
from functools import lru_cache

# 与像素场景对应的实体范围；使用格子中心作为角色地面占位。
def furniture_rects():
    from .world import BEDROOM_RECTS
    out=[]
    for i,(x,y,_,_) in enumerate(BEDROOM_RECTS):
        px,py=x*16,y*16
        out.extend([(px+5,py+17,25,38),(px+6,py+2,11,13)])
        if i==4:out.extend([(px+39,py+13,26,22),(px+76,py+26,19,24)])
        else:out.extend([(px+58,py+28,26,22),(px+49,py+3,35,24)])
    out.extend([(180,20,82,13),(180,36,22,28),(246,40,18,31),
                (307,130,76,24),(327,104,36,17),(323,32,48,29),
                (291,19,29,24),(389,19,11,13),(152,96,48,24),(244,97,11,13),
                (167,188,60,37),(159,174,18,18),(211,174,18,18),
                (159,220,18,18),(211,220,18,18),(333,324,46,30),
                (306,374,14,22),(322,374,14,22)])
    for x in (176,240):out.extend([(x+3,340,18,30),(x+25,373,17,20)])
    return out

@lru_cache(maxsize=16)
def blocked_cells(scene,rows):
    if scene=='house':rects=furniture_rects()
    else:
        rects=[(188,66,56,41),(190,125,23,12),(228,125,23,12)]
        rects.extend((x,y,16,18) for x,y in [(179,40),(245,45),(181,133),(248,147),(38,231),(91,237),(41,282),(89,287)])
        rects.append((38,303,75,7))
    cells={(x,y) for y in range(26) for x in range(26)
           if any(a<=x*16+8<a+w and b<=y*16+8<b+h for a,b,w,h in rects)}
    if scene=='town':
        # 建筑内部不作为道路，门前最后一排保留作为设施交互入口。
        for y,row in enumerate(rows):
            for x,ch in enumerate(row):
                if ch in 'SCOMGBRU' and y+1<26 and rows[y+1][x]==ch:cells.add((x,y))
    return frozenset(cells)
