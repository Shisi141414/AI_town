"""世界层 v2 —— 双场景方形地图 + 分区 + 场景切换

两张 26×26 方形地图：
  室内 HOUSE —— 合租大平层
  室外 TOWN  —— 小镇场馆

设计原则（吸取 v1 教训）：
- 室内采用「井字形」结构：两条竖墙 + 两条横墙把 24×24 内区分为九宫格，
  每道内墙开 2~3 个门洞。九宫格天然保证所有房间互相可达。
- 阳台为南侧整条房间（九宫格下排），通过门洞与卧室和客厅相通。
- 场景切换：室内东墙门 E(25,12) ⇄ 室外西门 E(1,12)。
"""
from __future__ import annotations

from collections import deque

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
    "F": {"name": "田地", "icon": "🌾"},
    "~": {"name": "施工地", "icon": "🧱"},
    "B": {"name": "医院", "icon": "🏥"},
    # 通用
    "#": {"name": "道路", "icon": ""},
    "T": {"name": "树木", "icon": "🌲"},
    ".": {"name": "空地", "icon": ""},
    "W": {"name": "湖", "icon": "🌊"},
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
def build_house() -> list[list[str]]:
    """合租大平层（井字形九宫格布局）

    坐标规划（内区 1~24）：
      竖墙 x=7（洞 y=3,12,21）  x=16（洞 y=3,12,21）
      横墙 y=8（洞 x=3,11,19）  y=17（洞 x=3,11,19）
    九宫格：
      [ 卧1 | 卧2 | 卧3 ]     y 1~7
      [ 厨房 | 客厅 | 餐厅 ]   y 9~16
      [ 卫1+卫2 | 阳台 | 洗衣+卧4 ] y 18~24   （下排高7格）
    大门：东墙 (25,12)——与客厅右墙洞对齐；客厅东墙 x=16 洞 y=12 直通。
    """
    g = [[FLOOR] * SIZE for _ in range(SIZE)]
    # 外墙
    _rect(g, 0, 0, SIZE - 1, 0, WALL)
    _rect(g, 0, SIZE - 1, SIZE - 1, SIZE - 1, WALL)
    _rect(g, 0, 0, 0, SIZE - 1, WALL)
    _rect(g, SIZE - 1, 0, SIZE - 1, SIZE - 1, WALL)
    # 内墙（井字）
    _vwall(g, 7, 1, 24, holes=(3, 12, 21))
    _vwall(g, 16, 1, 24, holes=(3, 12, 21))
    _hwall(g, 8, 1, 24, holes=(3, 10, 14, 19))
    _hwall(g, 17, 1, 24, holes=(3, 11, 19))
    # ---- 上排（y 1~7）：卧1 | 卧2 | 卧3 | 卧4 ----
    _rect(g, 1, 1, 6, 7, "h")
    _rect(g, 8, 1, 11, 7, "h")
    _rect(g, 12, 1, 15, 7, "h")
    _rect(g, 17, 1, 24, 7, "h")
    # 上中格分隔墙（最后画，防止被房间填充覆盖）：卧2(x8~11) | 卧3(x12~15)
    _vwall(g, 12, 1, 7, holes=())
    # ---- 中排（y 9~16）：厨房 | 客厅 | 餐厅 ----
    _rect(g, 1, 9, 6, 16, "K")
    _rect(g, 8, 9, 15, 16, "L")
    _rect(g, 17, 9, 24, 16, "D")
    # ---- 下排（y 18~24）：卫1+卫2 | 阳台 | 洗衣+卧5 ----
    _rect(g, 1, 18, 3, 24, "b")       # 卫生间1
    _rect(g, 5, 18, 6, 24, "b")       # 卫生间2
    _rect(g, 8, 18, 15, 24, "a")      # 大阳台
    _rect(g, 17, 18, 20, 24, "w")     # 洗衣区
    _rect(g, 22, 18, 24, 24, "h")     # 卧室5
    # 电视与沙发：客厅内（渲染时在 L 区中央画图标）
    # 大门：东墙正中 y=12（客厅行），并把 x=16 竖墙的 y=12 洞保持畅通
    g[12][SIZE - 1] = "E"
    # 确保餐厅(东中格)也能到大门：x=16 洞 y=12 已开
    return g


# ================================================================== 室外
def build_town() -> list[list[str]]:
    """小镇 26×26。西侧家门 E(1,12)，十字主干道，四大象限：
    西北：商店+咖啡馆；东北：办公楼群；西南：公园+湖；东南：健身房+医院+施工地+田地。
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
    # 东北：办公楼 ×2
    _rect(g, 19, 2, 24, 5, "O")
    _rect(g, 19, 7, 24, 10, "O")
    # 西南：公园 + 湖
    _rect(g, 2, 14, 7, 19, "P")
    _rect(g, 3, 21, 6, 23, "W")
    # 南中：电影院
    _rect(g, 10, 14, 16, 17, "M")
    # 东南：健身房 / 医院 / 施工地 / 田地
    _rect(g, 19, 14, 24, 17, "G")
    _rect(g, 19, 19, 24, 21, "B")
    _rect(g, 10, 20, 13, 23, "~")
    _rect(g, 15, 20, 24, 23, "F")
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


def walkable(scene: str, x: int, y: int) -> bool:
    return tile(scene, x, y) in WALKABLE_CHARS


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
    return z1 == z2


# ------------------------------------------------------------------ 寻路
def bfs_path(scene: str, sx: int, sy: int, tx: int, ty: int,
             limit: int = 2500) -> list[tuple[int, int]]:
    if not walkable(scene, tx, ty):
        return []
    if (sx, sy) == (tx, ty):
        return []
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    q: deque[tuple[int, int]] = deque([(sx, sy)])
    while q and len(prev) < limit:
        cx, cy = q.popleft()
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in prev or not walkable(scene, nx, ny):
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
