"""世界层 —— ASCII 地图 + 设施 + BFS 寻路

地图设计原则：
- 用字符画定义小镇，直观可改（改地图 = 改字符串）
- 每种字符对应一种地物，设施(facility)挂在地物上
- 寻路用 BFS（网格小、无权重，BFS 比 A* 简单且最优）
"""
from __future__ import annotations

from collections import deque

# ------------------------------------------------------------------ 地图
# 字符含义：. 草地  # 道路  T 栄树  W 水  H 房屋门(小屋)  C 咖啡馆/餐厅
#            K 厨房(公共)  G 健身房  V 电视房  P 公园长椅  B 医院床
#            S 商店  F 田地(工作)  O 办公室  ~ 施工地(建筑)  X 损坏区
MAP_ROWS: list[str] = [
    "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT",
    "T..........T##########################################T.",
    "T.HHHHHH...T#....V..V.#..CCCCCC..#..GGGG..#..OOOO..#...T",
    "T.HHHHHH...T#....V..V.#..CCCCCC..#..GGGG..#..OOOO..#...T",
    "T.HHHHHH...T#..........#..CCCCI...#..GGGG..#..OOOO..#...T",
    "T.HHHHHH...T############..########..#######..#######....T",
    "T......................#..#......#...#....#..#..........T",
    "T.HHHHHH...#########...#..#..S...#...#....#..#..FFFF....T",
    "T.HHHHHH...#.......#...#..#..SS..#...#....#..#..FFFF....T",
    "T.HHHHHH...#.KKKK...#...#..#......#...#....#..#..FFFF....T",
    "T.HHHHHH...#.KKKK...#...####KK#####...######..####~~~....T",
    "T..........#..........#.....................#...........T",
    "T..........############....PPPP....#########....TTTT....T",
    "T..........................PPPP.........................T",
    "T..WWWW....####BB####......PPPP......####BBBB####....TT..T",
    "T..WWWW....#..BBBB..#..................#..BBBB..#.......T",
    "T..WWWW....#........#..................#........#.......T",
    "T..WWWW....##########..................##########.......T",
    "TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT TTTTTTTTTTTTTT TTTTTTTTTT",
]

WIDTH, HEIGHT = len(MAP_ROWS[0]), len(MAP_ROWS)

# ------------------------------------------------------------------ 设施注册
# 地物字符 → 设施定义。activity_key 对应 activities.py 里的 key。
# consume=True 表示设施可被占用（同格多玩家视为「共处」，不互斥）。
FACILITY_CHARS: dict[str, dict] = {
    "H": {"name": "小屋", "activity_key": "sleep", "label": "睡觉"},
    "C": {"name": "咖啡馆", "activity_key": "eat", "label": "用餐"},
    "K": {"name": "厨房", "activity_key": "cook", "label": "做饭"},
    "G": {"name": "健身房", "activity_key": "exercise", "label": "运动"},
    "V": {"name": "电视房", "activity_key": "watch_tv", "label": "看电视"},
    "S": {"name": "商店", "activity_key": "shop", "label": "购物"},
    "F": {"name": "田地", "activity_key": "work", "label": "劳作"},
    "O": {"name": "办公室", "activity_key": "work", "label": "办公"},
    "B": {"name": "医院", "activity_key": "treat", "label": "治疗"},
    "P": {"name": "公园长椅", "activity_key": "think", "label": "闲坐思考"},
    "~": {"name": "施工地", "activity_key": "build", "label": "建筑"},
    "W": {"name": "湖畔", "activity_key": "think", "label": "湖畔沉思"},
    "T": {"name": "树林", "activity_key": "think", "label": "林间漫步"},
    "#": {"name": "道路", "activity_key": None, "label": "道路上"},
    ".": {"name": "草地", "activity_key": None, "label": "草地上"},
    "I": {"name": "咖啡馆门", "activity_key": "eat", "label": "用餐"},
}

WALL_CHARS: set[str] = {"T", "W"}      # 不可通行的装饰地物
WALKABLE: set[str] = set(FACILITY_CHARS) | {"X"}  # X=被破坏的地面
WALKABLE.discard("T"); WALKABLE.discard("W")     # 树与水不可通行


def tile(x: int, y: int) -> str:
    if 0 <= y < HEIGHT and 0 <= x < WIDTH:
        return MAP_ROWS[y][x]
    return "T"


def walkable(x: int, y: int) -> bool:
    return tile(x, y) in WALKABLE


def facility_at(x: int, y: int) -> dict | None:
    ch = tile(x, y)
    return FACILITY_CHARS.get(ch)


# ------------------------------------------------------------------ 寻路
def bfs_path(sx: int, sy: int, tx: int, ty: int, limit: int = 4000) -> list[tuple[int, int]]:
    """BFS 最短路。返回从起点(不含)到终点(含)的路径；不可达返回 []"""
    if not walkable(tx, ty):
        return []
    if (sx, sy) == (tx, ty):
        return []
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    q: deque[tuple[int, int]] = deque([(sx, sy)])
    while q and len(prev) < limit:
        cx, cy = q.popleft()
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in prev or not walkable(nx, ny):
                continue
            prev[(nx, ny)] = (cx, cy)
            if (nx, ny) == (tx, ty):
                path: list[tuple[int, int]] = []
                cur = (nx, ny)
                while cur and prev[cur] is not None:
                    path.append(cur)
                    cur = prev[cur]  # type: ignore[assignment]
                path.reverse()
                return path
            q.append((nx, ny))
    return []


def nearest_reachable(sx: int, sy: int, tx: int, ty: int) -> tuple[int, int]:
    """若目标不可达，找目标周围 walkable 的替身格（绕着目标扫一圈）"""
    if walkable(tx, ty):
        return tx, ty
    for r in range(1, 8):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                nx, ny = tx + dx, ty + dy
                if walkable(nx, ny):
                    return nx, ny
    return sx, sy


def find_facility_tiles(char: str) -> list[tuple[int, int]]:
    out = []
    for y, row in enumerate(MAP_ROWS):
        for x, ch in enumerate(row):
            if ch == char:
                out.append((x, y))
    return out
