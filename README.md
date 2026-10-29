# 🏘️ AI 小镇

一个探索**不同模型在相同提示词下可能产生性格自然涌现**的项目。我们构建了一个小镇场景，将至多 5 位居民（默认 3 位）置于其中，给他们**相同的提示词、几乎完全对称的机会和资源**，完全由他们自行决定如何生活与交流。你可以**给每个居民接入不同的模型**，观察每个模型在长期生活中表现出的截然不同的兴趣与性格。本项目长期免费开源，欢迎讨论和贡献！

## 目录

- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [开发说明](#开发说明)
  - [总体架构](#总体架构)
  - [目录结构](#目录结构)
  - [核心机制](#核心机制)
  - [数据与持久化](#数据与持久化)
  - [扩展指引](#扩展指引)
  - [测试与检查](#测试与检查)
- [API 速览](#api-速览)
- [文档与预览](#文档与预览)
- [开源致谢](#开源致谢)

## 核心特性

- 🎭 **开局身份自选**：每个居民的模型自己决定叫什么（小x 格式）、性别与颜色
- 🧠 **模型即人格**：各居民可绑定不同模型（DeepSeek / GLM / Qwen / ERNIE / MiniMax…），同一提示词下观察行为差异
- 🏠🌆 **双场景世界**：室内合租之家（5 卧 / 客厅 / 厨房 / 餐厅 / 双卫 / 洗衣区 / 大阳台）+ 室外小镇（商店 / 公园 / 办公楼 / 电影院 / 健身房 / 咖啡馆 / 饭店 / 学校 / 医院 / 诊所 / 田地 / 池塘），两图独立，居民从大门出入
- 🤝 **协同增益社交**：一起打游戏/吃饭/工作有数值加成，好感度调制加成幅度——关系差一起做事反而互相拖累；每次互动后由模型自己决定好感增减（-5~+5）
- 🧑‍💻 **居民自主工具**：卧室电脑可写作与检索小镇信息，可互发表情、不受系统约束地转账（借款、还钱、送礼全凭角色自己表述）
- 💐 **物品与装饰**：商店购买鲜花/盆栽布置房间或公共区（持续心情加成），或送给心上人
- 💰 **生存与经济闭环**：饱食/精力/心情/生命四维数值、生病送医、室友借款、住院计费、多股票市场（场所股价受居民消费影响）、饭菜保质期
- 🎓 **技能与职业**：六学科课程与分级岗位，时薪按实际工作时间结算
- 🕸 **好感网络图**：实时可视化的居民关系网（单向好感，两段独立显示）
- 💾 **世界存档与过程回放**：按时间命名的独立存档目录、逐帧回放、从任意回放位置创建副本继续
- ✏️ **可编辑参数**：统一提示词、数值规则、场所参数、活动数值、商店货架均可在网页上修改
- 🛡️ **永不下线**：LLM 不可用时自动降级内置规则引擎；对白生成失败用内置文案兜底

## 快速开始

**你需要**：Python 3.10+ 环境、一个浏览器，以及（可选）一个大模型的 API Key。不配 Key 也能玩（内置规则模式），配了居民才会由 LLM 操控。

```bash
pip install -r requirements.txt   # 安装依赖
python server.py                  # 启动（保持窗口开启，关掉窗口=关掉游戏）
```

浏览器打开 `http://127.0.0.1:8300`，点「开始」即可看到居民开始生活。

### 接入大模型（推荐）

1. 页面右侧「模型」页 → **新增档案**，填 Base URL（服务商接口地址）、API Key、格式（OpenAI / Anthropic，看服务商文档）
2. 点**「获取模型列表」**→ 从列表挑一个 → **启用**
3. 到**「玩家模型绑定」**给每位居民挑模型（可以每人不同，观察不同"人格"），建议选择 flash 等响应快速的模型
4. 回主界面点「开始」——现在居民会自己起名字、选形象、谈恋爱、吵架、深夜打游戏……

> 💡 国内可用的低价/免费网关很多（硅基流动、智谱 BigModel、DeepSeek 官方等），注册即可拿到 Key。页面右上角 **📖 指南** 有更详细的图文说明。
> ⚠️ 你的 Key 只保存在本机 `config.json`（已加入 .gitignore），本项目不会上传到任何地方。

### 常见问题

- 端口被占用：上一次的服务还在运行，结束对应 python 进程后重新启动。
- 页面打不开：确认启动窗口仍开着、地址是 `127.0.0.1:8300`（不要加 www）。
- 想重来一局：页面点「重置」；想彻底删除所有存档：关掉服务，删除项目文件夹里的 `data` 文件夹。

---

## 开发说明

面向希望理解、拓展本项目的开发者，按当前代码库的结构与机制说明。

### 总体架构

项目分三层，依赖方向单向（无环）：

```
web/（原生 JS + Canvas 前端）
    │  REST / WebSocket
server.py（FastAPI 服务层：端点、配置、WS 广播）
    │  直接调用
engine/（纯 Python 模拟内核，不依赖任何 Web 框架）
```

- **engine/** 是可独立运行的模拟世界：世界时钟每 tick（游戏内 10 分钟）推进一步，驱动每位居民「感知 → 记忆检索 → LLM/规则决策 → 行动 → 数值结算」的完整生命周期。
- **server.py** 把 Simulation 实例包成 HTTP/WebSocket 服务，负责控制指令（开始/暂停/单步/变速/重置）、API 档案管理、存档/回放条目管理，并把每 tick 的快照+事件+对白广播给所有前端。
- **web/** 无构建步骤、无框架，直接由 FastAPI 托管静态文件；地图像素素材全部程序化绘制（无图片资源）。

### 目录结构

```
engine/                模拟内核（纯 Python，无 Web 依赖）
├── models.py          pydantic 数据模型（Stats/Decision/GameEvent/ApiProfile/Settings 等）
├── world.py           双 26×26 地图、设施注册、BFS 寻路、场景切换
├── collision.py       家具与室外物体的地面占位（16px/格），碰撞阻挡
├── interaction_points.py  家具使用位（床/灶台/餐椅/电脑/沙发等），仅对使用者开放
├── activities.py      内置活动注册表（场景标注/协同增益公式/物品/装饰）
├── activity_rules.py  活动的统一编辑视图：字段覆盖 + 自定义活动 + 冻结生效条款
├── shop_products.py   商店货架（内置商品沿用旧逻辑，自定义商品成为可购买活动）
├── mechanics.py       全部数值规则（DEFAULT_RULES），可整体调参
├── town_services.py   场所服务/课程/岗位/技能/小镇股市，参数按世界隔离
├── memory.py          三级记忆流（sqlite3 存储，recency × importance 检索，分层压缩）
├── llm.py             OpenAI/Anthropic 双格式客户端（直接 requests，不用官方 SDK）
├── brain.py           决策层：统一提示词模板 + 宽容 JSON 解析 + 规则引擎兜底
├── player.py          玩家框架：状态机（idle→deciding→moving→doing）+ 跨场景出行
├── simulation.py      世界时钟、tick 主循环、身份阶段、好感异步队列、事件日志
├── conversation.py    回合制对话引擎（状态机：轮流说话/离开/追赶）
├── hospital.py        急救-借款-住院-死亡闭环
├── resident_tools.py  居民自主工具：电脑文档、表情、转账
├── random_events.py   低频随机事件（生病/好感波动），独立随机源
├── saves.py           按时间命名的世界存档（JSON 检查点原子替换）
├── recording.py       过程录制（sqlite：事件、模型请求、记忆变化、状态帧）
└── （engine 从不 import server —— 依赖方向严格单向）
server.py              FastAPI：控制/档案/模型列表/提示词/参数/存档/回放/WS 广播
web/index.html         页面结构：居民 / 关系网 / 事件 / 模型 / 设置 / 存档 六面板
web/app.js             页面交互、安全文本渲染、出行/购车/搭乘、回放控制
web/pixel-scene.js     程序化像素素材与双场景地图绘制（地形坐标来自服务端）
web/mechanics.js       数值规则编辑界面
web/town-buildings.js  室外建筑参数对话框
web/base.css           基础布局与组件
web/pixel-theme.css    暖色像素主题与手机适配
tests/                 22 个 Python unittest 模块（约 268 项）+ 14 个 Playwright 浏览器检查
docs/                  详细机制文档与界面预览图
```

### 核心机制

**决策循环（`simulation.py` + `player.py` + `brain.py`）**。开局为第 1 天 08:00，每 tick = 游戏内 10 分钟（144 刻/天）。居民状态机为 `idle → deciding → [出门 crossing] → moving → doing → idle`：决策时，`brain.py` 用统一提示词模板拼装当前状态、检索到的记忆、好感、活动菜单，向该居民绑定的模型请求一个 JSON 行动；解析成功后由 `simulation._apply_decision` 执行寻路、入场或即时结算。模型可在 JSON 中附 `duration_minutes`（10~1440，按 10 分钟取整）指定活动时长，计时期间不再重复决策。

**LLM 集成与容错（`llm.py` + `brain.py`）**。客户端同时支持 OpenAI（`/chat/completions`）与 Anthropic（`/v1/messages`）格式，请求由 Python 后端发出，天然无 CORS 问题。解析层很宽容：接受代码块围栏、多个 JSON 对象、单引号/尾逗号、中文活动名。每轮决策最多请求 3 次，网络/限流错误可重试，鉴权等不可重试错误直接进入可见等待状态（15 秒后重试，不可重试错误 60 秒），**不静默替换成规则行动**；等待期间世界时钟暂缓，不消耗居民数值。无绑定 API 的居民一直走规则引擎（`brain.py` 内置的启发式兜底）。单次决策等待默认 90 秒，可在设置中调 5~300 秒。

**三级记忆（`memory.py`）**。一级记忆收录活动、聊天、相遇、出行等生活事件；接近 48 条或 10000 字时整理最旧的最多 40 条为 10 条二级记忆，二级接近 24 条或 5000 字时再压缩为三级。压缩流程：模型先提名想保留的记录，从中随机抽 80%，另从全池随机抽 20%（去重）；模型缺失/超时按重要度补足候选并标记兜底，不冒充模型选择。三级无淘汰逻辑。决策只检索最多 8 条、2400 字，聊天 4 条、1200 字——不会把全部记忆塞进上下文。事件分 `scope=game/user`：只有游戏事件写入居民记忆，"等待模型"“记忆整理"等运行提示只给用户看，且在旧档读取、检索、压缩入口再次过滤。

**好感与社交（`simulation.py` + `conversation.py`）**。好感是单向的（A 对 B 与 B 对 A 独立），互动结束后异步向该居民的模型提问「你对 TA 的好感变化多少」（-5~+5 整数），由 `_drain_affinity()` 在 tick 间消费，不阻塞主循环——规则只框边界不框方向，让社交关系成为真正的涌现现象。对话是回合制状态机：一人说话对方静默、可离开、可 follow 追赶（被追者好感 -2），结束后摘要写入双方记忆。电话与面谈同机制（无数值增减，按轮收话费）。

**协同增益（`activities.py`）**。`倍率 = 人数增益 × 好感调制`：每多 1 人 +0.35；好感 50 → ×1.0，100 → ×1.5，0 → ×0.5；关系不睦（倍率 <0.75）时正收益再打五折。

**数值与随机（`mechanics.py` + `random_events.py` + `hospital.py`）**。四维数值（生命/饱食/精力/心情）每刻结算，全部速率集中在 `DEFAULT_RULES` 一张表里，网页可改。随机事件用独立随机源每 6 刻检查：每日患病概率约 2%、好感波动约 4%；疾病分轻/中/重症（初始比例 85/13/2%），可加重、可自然恢复或诊所治疗，生病后 3 天免疫。生命 ≤10 触发紧急送医：现金不足时逐个室友"打电话"借钱（对方的模型决定借多少），住院半天计费，付不起自动出院；未收治且生命归零判定死亡，移除玩家并播报。

**出行与碰撞（`world.py` + `collision.py` + `interaction_points.py`）**。跨场景出行分两段寻路：先到本场景大门，切换场景坐标，再走到目的地（"先下楼"）。同一场景居民逐格占位、互不重叠，寻路绕开家具；家具使用位（床/灶台/餐椅/电脑/沙发/卫浴）仅对前往该活动的角色开放，被占用则等待。交通分步行/骑行/汽车三档（汽车仅限道路、按格收油费），全镇开局 1 辆共用汽车，居民可花 1000 元购买私家车，可搭乘他人车辆。

**活动与参数体系（`activity_rules.py` + `town_services.py` + `shop_products.py`）**。内置活动构成基础注册表；用户修改产生 `activity_overrides`（字段级覆盖），自定义活动存 `custom_activities`，三者合并成统一目录。已开始的活动沿用开始时冻结的条款（费用/时长），修改只作用于新活动。场所参数（课程学费、岗位时薪、技能经验率）、商店货架同理，全部随世界存档保存、重置恢复默认。

**存档与回放（`saves.py` + `recording.py`）**。两套独立体系：世界存档是按时间命名的目录（`data/saves/<时间编号>/`），保存完整世界状态 + 分层记忆 sqlite，JSON 原子替换写入；过程录制（`data/replays/`）逐帧记录事件、模型请求、记忆增删，每帧携带完整检查点。回放读取已保存状态、不请求模型；可从当前回放帧创建副本继续生活。重置流程为：暂停并取消未完成的模型应用 → 原子保存旧世界 → 新建空目录，失败则中止并保留旧世界。API 密钥只存本机 `config.json`，不写入任何存档。

### 数据与持久化

| 路径 | 内容 | 说明 |
|---|---|---|
| `config.json` | API 档案、模型绑定、设置、统一提示词 | 已 gitignore，含密钥，勿提交 |
| `data/saves/<时间编号>/` | 世界快照 + 三级记忆 sqlite | 整目录拷贝即完整备份 |
| `data/replays/` | 过程录制 sqlite | 可能被多个存档引用，删除世界条目不会删它 |

模拟运行状态存于服务进程内存；正常关闭自动存档，强杀进程恢复到最近检查点。默认居民的记忆与模型绑定按稳定入住槽位（pid）恢复，重置不删深层记忆。

### 扩展指引

- **调数值**：全部结算速率在 `engine/mechanics.py` 的 `DEFAULT_RULES`，网页「数值规则」页可实时改；场所参数在 `town_services.py` 的 `DEFS`。
- **加活动**：在 `engine/activities.py` 注册表加一项（name/scene/location/effects/duration/cost），提示词菜单自动带上；或运行时经网页添加自定义活动，无需改代码。
- **加场所**：在 `engine/world.py` 的 `FACILITY_CHARS` 加设施字符并画进两张地图，再到 `town_services.py` 的 `DEFS/NAMES` 挂服务；前端建筑参数对话框（`web/town-buildings.js`）自动展示可调项。
- **改提示词**：`engine/brain.py` 的 `DEFAULT_SYSTEM_PROMPT` / `USER_TEMPLATE` 是决策提示词全文；网页设置页可覆盖系统提示词（存 `config.system_prompt`）。
- **前端绘制**：地图像素全部在 `web/pixel-scene.js` 程序化生成，地形数据来自服务端 `/api/map`，改视觉不影响逻辑坐标。

### 测试与检查

```bash
pip install -r requirements-dev.txt        # 含 httpx
python -m unittest discover -s tests -v    # 22 个模块，约 268 项，纯离线
node --check web/app.js                    # 前端语法检查
node --check web/pixel-scene.js
```

浏览器检查基于 Playwright + Edge，先用无真实密钥的预览服务（`tests/serve_preview.py`）起本地端口，再跑 `tests/*.cjs`（冒烟、回放、车辆、记忆、存档、参数编辑等 14 个脚本）。检查过程不读取真实配置、不调用真实模型网关。

## API 速览

```
POST /api/control        start|pause|resume|stop|step|speed|reset
GET  /api/state          世界快照（含身份阶段/公共装饰度）
GET  /api/events         事件日志（可按天/类型筛选）
GET  /api/map?scene=     house|town 双地图
GET  /api/relationships  好感网络数据
GET/POST/PUT/DELETE /api/profiles[/{pid}]   API 档案 CRUD，另有 /test 与 /active
GET/POST /api/models     模型列表（POST 为草稿查询，密钥走请求体不进网址）
GET/PUT /api/prompt      统一提示词
GET/PUT /api/config      设置 + pid→模型绑定
GET/PUT /api/mechanics   数值规则
GET/PUT /api/town/buildings[/{code}]        场所参数
GET/PUT /api/town/shop   商店货架
POST/DELETE /api/players[...]               居民增减（仅未运行时）
GET  /api/players/{name}/memories           ?tier=1|2|3&offset=&limit= 分层分页
PUT  /api/players/{pid}/travel              walk | bicycle | car
POST /api/players/{pid}/car                 花费1000购买私家车
PUT  /api/players/{pid}/ride                {driver_id} 上车；null 下车
GET  /api/replays[/{rid}/frames/{index}]    回放档案列表与帧
WS   /ws                  每 tick 快照 + 事件 + 对白
```

## 文档与预览

- [检查与改造说明](docs/检查与改造说明.md) —— 测试与检查流程、出行与车辆规则、历史改造记录
- [小镇技能与场所参数](docs/小镇技能与场所参数.md) —— 场所分工、课程与岗位表、结算说明
- [室内活动使用位置](docs/室内活动使用位置.md) —— 家具交互格坐标
- [场景与活动地点坐标手册](docs/场景与活动地点坐标手册.md) —— 旧版坐标参考（当前地图以代码为准）
- 界面预览：[室内](docs/previews/house.png) / [室外](docs/previews/town.png) / [角色形象](docs/previews/characters.png) / [共用车](docs/previews/car-sharing.png) / [存档页](docs/previews/saves.png) / [回放](docs/previews/replay.png) / [手机布局](docs/previews/mobile.png)

## 开源致谢

架构思想来自 [joonspk-lab/generative_agents](https://github.com/joonspk-lab/generative_agents)（斯坦福 Generative Agents）与 [a16z/ai_town](https://github.com/a16z/ai_town)；API 档案管理交互参考 [CCswitch](https://github.com/farion1231/cc-switch)。工程依赖 FastAPI / pydantic / uvicorn / requests。
