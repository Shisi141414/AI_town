"""世界层 v2 —— 双场景方形地图 + 分区 + 场景切换

两张 26×26 方形地图：
  室内 HOUSE —— 合租大平层
  室外 TOWN  —— 小镇场馆

设计原则（吸取 v1 教训）：
- 室内参考合租平面图，五间 6×4 卧室围绕公共餐厅，客厅与阳台位于右侧。
- 场景切换：室内东墙门 E(25,12) ⇄ 室外西门 E(1,12)。
"""
from __future__ import annotations

from collections import deque
import heapq

SIZE = 26

WALL = "■"          # 墙（不可通行）
FLOOR = "."

FACILITY_CHARS: dict[str, dict] = {
    # 室内
    "h": {"name": "卧室", "icon": "🛏"},
    "L": {"name": "客厅", "icon": "🛋"},
    "K": {"name": "厨房", "icon": "🍳"},
    "D": {"name": "餐厅", "icon": "🍽"},
    "b": {"name": "卫生间", "icon": "🚻"},
    "a": {"name": "阳台", "icon": "🌿"},
    "w": {"name": "洗衣区", "icon": "🧺"},
    "E": {"name": "大门", "icon": "🚪"},
    # 室外
    "S": {"name": "商店", "icon": "🛒"},
    "P": {"name": "公园", "icon": "🌳"},
    "O": {"name": "办公楼", "icon": "🏢"},
    "M": {"name": "电影院", "icon": "🎬"},
    "G": {"name": "健身房", "icon": "🏋"},
    "C": {"name": "咖啡馆", "icon": "☕"},
    "R": {"name": "饭店", "icon": "🍜"},
    "U": {"name": "学校", "icon": "🏫"},
    "F": {"name": "田地", "icon": "🌾"},
    "~": {"name": "施工地", "icon": "🧱"},
    "B": {"name": "医院", "icon": "🏥"},
    # 通用
    "#": {"name": "道路", "icon": ""},
    "T": {"name": "树木", "icon": "🌲"},
    ".": {"name": "空地", "icon": ""},
    "W": {"name": "湖", "icon": "🌊"},
    "J": {"name": "小镇花园", "icon": "🌷"},
    "X": {"name": "损坏", "icon": "💥"},
}

WALKABLE_CHARS = set(FACILITY_CHARS) - {"T", "W"}


def _rect(g, x0, y0, x1, y1, ch):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if 0 <= y < SIZE and 0 <= x < SIZE:
                g[y][x] = ch


def _hwall(g, y, x0, x1, holes):
    """横墙 + 门洞"""
    _rect(g, x0, y, x1, y, WALL)
    for hx in holes:
        g[y][hx] = FLOOR


def _vwall(g, x, y0, y1, holes):
    """竖墙 + 门洞"""
    _rect(g, x, y0, x, y1, WALL)
    for hy in holes:
        g[hy][x] = FLOOR


# ================================================================== 室内
BEDROOM_RECTS = [(4, 1, 9, 4), (1, 6, 6, 9), (1, 11, 6, 14),
                 (1, 16, 6, 19), (4, 21, 9, 24)]
BEDROOM_ANCHORS = [(6, 3), (3, 8), (3, 13), (3, 18), (6, 23)]


def build_house() -> list[list[str]]:
    """参照合租平面图：五个相同的 6×4 卧室围绕公共餐厅。"""
    g = [[" "] * SIZE for _ in range(SIZE)]
    # 主体与左侧三卧形成错层外轮廓；空白格属于屋外，不可通行。
    _rect(g, 3, 0, 25, 25, WALL)
    _rect(g, 4, 1, 24, 24, FLOOR)
    _rect(g, 0, 5, 7, 20, WALL)
    _rect(g, 8, 6, 16, 19, "D")
    _rect(g, 18, 1, 24, 17, "L")
    _rect(g, 18, 18, 24, 24, "a")
    _rect(g, 18, 23, 20, 24, "w")
    _rect(g, 11, 1, 16, 4, "K")
    _hwall(g, 5, 3, 17, holes=(8, 13))
    _vwall(g, 10, 1, 4, holes=())
    _vwall(g, 17, 1, 10, holes=())
    _vwall(g, 7, 6, 19, holes=(8, 13, 18))
    _hwall(g, 10, 0, 7, holes=())
    _hwall(g, 15, 0, 7, holes=())
    _hwall(g, 20, 3, 18, holes=(8, 12, 16))
    _vwall(g, 10, 21, 24, holes=())
    _vwall(g, 14, 21, 24, holes=())
    _vwall(g, 18, 21, 24, holes=())
    _rect(g, 11, 21, 13, 24, "b")
    _rect(g, 15, 21, 17, 24, "b")
    _hwall(g, 17, 22, 24, holes=())
    for rect in BEDROOM_RECTS:
        _rect(g, *rect, "h")
    g[12][25] = "E"
    return g


# ================================================================== 室外
def build_town() -> list[list[str]]:
    """小镇 26×26。西侧家门 E(1,12)，十字主干道，四大象限：
    西北：商店+咖啡馆；东北：事务所和饭店；西南：公园+湖；东南：健身房+医院+施工地+田地。
    电影院在南中。"""
    g = [[FLOOR] * SIZE for _ in range(SIZE)]
    # 外围树带
    _rect(g, 0, 0, SIZE - 1, 0, "T")
    _rect(g, 0, SIZE - 1, SIZE - 1, SIZE - 1, "T")
    _rect(g, 0, 0, 0, SIZE - 1, "T")
    _rect(g, SIZE - 1, 0, SIZE - 1, SIZE - 1, "T")
    # 家门与主路（E 门最后画，防止被道路覆盖）
    _rect(g, 1, 12, SIZE - 2, 12, "#")
    _rect(g, 8, 1, 8, SIZE - 2, "#")
    _rect(g, 18, 1, 18, SIZE - 2, "#")
    g[12][1] = "E"
    # 西北：商店 / 咖啡馆
    _rect(g, 2, 2, 7, 5, "S")
    _rect(g, 2, 7, 7, 10, "C")
    # 东北：事务所 / 饭店
    _rect(g, 19, 2, 24, 5, "O")
    _rect(g, 19, 7, 24, 10, "R")
    _rect(g, 10, 2, 16, 10, "J")
    # 西南：公园 + 湖
    _rect(g, 2, 14, 7, 19, "P")
    _rect(g, 3, 21, 6, 23, "W")
    # 南中：电影院
    _rect(g, 10, 14, 16, 17, "M")
    # 东南：健身房 / 医院 / 施工地 / 田地
    _rect(g, 19, 14, 24, 17, "G")
    _rect(g, 19, 19, 24, 21, "B")
    _rect(g, 10, 20, 13, 23, "U")
    _rect(g, 14, 22, 24, 22, "#")
    _rect(g, 15, 23, 17, 24, "F")
    _rect(g, 19, 23, 24, 24, "F")
    # 点缀树（避开道路与场馆）
    g[1][13] = "T"; g[13][1] = "T"; g[11][24] = "T"; g[13][24] = "T"
    # 家门与主路连通
    g[12][2] = "#"
    return g


HOUSE: list[list[str]] = build_house()
TOWN: list[list[str]] = build_town()
SCENES: dict[str, list[list[str]]] = {"house": HOUSE, "town": TOWN}
SCENE_NAMES = {"house": "室内·合租之家", "town": "室外·小镇"}


# ------------------------------------------------------------------ 查询
def grid(scene: str) -> list[list[str]]:
    return SCENES[scene]


def tile(scene: str, x: int, y: int) -> str:
    g = SCENES.get(scene)
    if g and 0 <= y < SIZE and 0 <= x < SIZE:
        return g[y][x]
    return WALL


_SOLID_CELLS={}


def walkable(scene: str, x: int, y: int) -> bool:
    from .collision import blocked_cells
    ch=tile(scene,x,y)
    if ch not in WALKABLE_CHARS:return False
    if scene not in _SOLID_CELLS:
        original=build_house() if scene=='house' else build_town()
        _SOLID_CELLS[scene]=blocked_cells(scene,tuple(''.join(row) for row in original))
    return ch=='X' or (x,y) not in _SOLID_CELLS[scene]


def facility_at(scene: str, x: int, y: int) -> dict | None:
    return FACILITY_CHARS.get(tile(scene, x, y))


def find_tiles(scene: str, ch: str) -> list[tuple[int, int]]:
    g = SCENES[scene]
    return [(x, y) for y in range(SIZE) for x in range(SIZE) if g[y][x] == ch]


def zone_of(scene: str, x: int, y: int) -> str:
    f = facility_at(scene, x, y)
    return f["name"] if f else "路上"


def same_place(p1, p2) -> bool:
    """相遇判定：同场景且同设施区，或路上近距离偶遇"""
    if p1.scene != p2.scene:
        return False
    z1 = tile(p1.scene, p1.x, p1.y)
    z2 = tile(p2.scene, p2.x, p2.y)
    if z1 in ("#", ".", "E", "X"):
        return max(abs(p1.x - p2.x), abs(p1.y - p2.y)) <= 2
    if z1 != z2:
        return False
    # 分隔的卧室、办公楼不应隔墙社交。
    seen = {(p1.x, p1.y)}
    q = deque(seen)
    while q:
        x, y = q.popleft()
        if (x, y) == (p2.x, p2.y):
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            pos = (x + dx, y + dy)
            if pos not in seen and tile(p1.scene, *pos) == z1:
                seen.add(pos)
                q.append(pos)
    return False


TRAVEL_MODES = {
    "walk": {"name": "步行", "speed": 1, "energy": 1.0, "cost": 0.0},
    "bicycle": {"name": "骑行", "speed": 4, "energy": 0.7, "cost": 0.0},
    "car": {"name": "开车", "speed": 12, "energy": 0.15, "cost": 0.15},
}


def movement_mode(scene, start, end, preference, money=100.0):
    """车辆只在室外道路上使用；入楼和资金不足时改为步行。"""
    if scene != "town" or preference not in TRAVEL_MODES:
        return "walk"
    if preference == "car":
        road = ("#", "E")
        if tile(scene, *start) not in road or tile(scene, *end) not in road or money < 0.15:
            return "walk"
    return preference


def travel_path(scene, sx, sy, tx, ty, mode="walk", blocked=None, allowed=None):
    """按旅行时间寻路；汽车可选择更快的道路路线。"""
    if scene != "town" or mode != "car":
        return bfs_path(scene, sx, sy, tx, ty, blocked=blocked,allowed=allowed)
    start, goal = (sx, sy), (tx, ty)
    costs, previous = {start: 0.0}, {start: None}
    queue = [(0.0, start)]
    while queue:
        cost, pos = heapq.heappop(queue)
        if cost > costs[pos]:
            continue
        if pos == goal:
            path = []
            while previous[pos] is not None:
                path.append(pos)
                pos = previous[pos]
            return path[::-1]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            dest = (pos[0] + dx, pos[1] + dy)
            if not walkable(scene, *dest) or (blocked and dest in blocked):
                continue
            actual = movement_mode(scene, pos, dest, mode)
            new_cost = cost + 1 / TRAVEL_MODES[actual]["speed"]
            if new_cost < costs.get(dest, float("inf")):
                costs[dest], previous[dest] = new_cost, pos
                heapq.heappush(queue, (new_cost, dest))
    return []


def reset_maps():
    HOUSE[:] = build_house()
    TOWN[:] = build_town()


# ------------------------------------------------------------------ 寻路
def bfs_path(scene: str, sx: int, sy: int, tx: int, ty: int,
             limit: int = 2500, blocked=None, allowed=None) -> list[tuple[int, int]]:
    if not walkable(scene, tx, ty) and (not allowed or (tx,ty) not in allowed):
        return []
    if (sx, sy) == (tx, ty):
        return []
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    q: deque[tuple[int, int]] = deque([(sx, sy)])
    while q and len(prev) < limit:
        cx, cy = q.popleft()
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in prev or (not walkable(scene, nx, ny) and (not allowed or (nx,ny) not in allowed)) or (blocked and (nx,ny) in blocked):
                continue
            prev[(nx, ny)] = (cx, cy)
            if (nx, ny) == (tx, ty):
                path = []
                cur: tuple[int, int] | None = (nx, ny)
                while cur and prev[cur] is not None:
                    path.append(cur)
                    cur = prev[cur]
                path.reverse()
                return path
            q.append((nx, ny))
    return []


def nearest_walkable(scene: str, tx: int, ty: int) -> tuple[int, int]:
    if walkable(scene, tx, ty):
        return tx, ty
    for r in range(1, 8):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                if walkable(scene, tx + dx, ty + dy):
                    return tx + dx, ty + dy
    return -1, -1
