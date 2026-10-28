# 🏘️ AI 小镇

一个基于大语言模型的多智能体生活模拟游戏：每个「玩家」由一个 LLM 驱动，在同一份提示词下，凭借各自的数值状态、记忆与人际关系，在 2D 小镇中自由生活——吃饭、做饭、运动、看电视、打游戏、思考、聊天、攻击、破坏、建筑、治疗、恋爱、工作、睡觉。

**核心特点**

- 🧠 **模型即人格**：5 个玩家可以各接一个模型（DeepSeek / GLM / Qwen / ERNIE / MiniMax…），同一提示词下观察不同模型的行为差异
- 👤 **玩家框架**：`Player` 类封装「感知 → 记忆 → 决策 → 行动」生命周期，可动态添加任意数量玩家并指定模型
- 🔌 **CCswitch 风格 API 管理**：多档案、OpenAI/Anthropic 双上游格式、连接测试、一键切换
- 🛡️ **永不下线**：LLM 不可用时自动降级为内置规则引擎，模拟永远能跑
- 📜 **全量事件日志**：时间范围/类型/关键词筛选，可查看任意玩家的记忆流

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（会自动打开浏览器；或手动访问 http://127.0.0.1:8300）
python server.py
```

**首次使用**：右侧「API 设置」→ 新增档案 → 填入 Base URL / API Key / 默认模型 → 点「测试」→ 启用。然后在「玩家模型绑定」里给每个玩家指定各自模型。不配置 API 时小镇以规则引擎模式运行。

## 操作

| 功能 | 位置 |
|------|------|
| 开始/暂停/单步/重置/速度 | 顶部控制栏 |
| 观察居民数值与当前活动 | 「居民」面板 |
| 事件日志（按天/类型/关键词） | 「事件日志」面板 |
| API 档案与模型绑定 | 「API 设置」面板 |
| 对白生成开关/温度/超时/添加玩家/记忆查看 | 「模拟设置」面板 |

## 架构

```
engine/               模拟引擎（纯 Python，无 Web 依赖）
├── models.py         pydantic 数据模型
├── world.py          ASCII 地图 + BFS 寻路
├── activities.py     14 种活动注册表（数值效果/约束）
├── memory.py         记忆流（recency × importance 检索）
├── llm.py            OpenAI/Anthropic 双格式客户端
├── brain.py          统一提示词模板 + 规则引擎兜底
├── player.py         玩家框架（感知→记忆→决策→行动）
└── simulation.py     世界时钟 + tick 调度 + 对白生成
server.py             FastAPI：REST 控制 / WS 广播 / 档案管理
web/index.html        单文件前端：Canvas 渲染 + 四面板
```

### 设计说明

**为什么所有人的提示词相同？** 借鉴斯坦福 Generative Agents 论文的核心发现——「人格」不是设定出来的，而是经历沉淀出来的。提示词只包含：当前时间、自身数值、视野内的人、好感表、记忆流摘要、活动菜单。个体差异完全由记忆与状态涌现。

**为什么后端代理 LLM 请求？** 浏览器直连 API 网关会遇到 CORS 限制（很多中转站不允许网页直接访问）。Python 后端转发则天然无此问题，同时 API Key 不暴露在前端。

**数值动力学**：饱食度自然衰减 → 归零后扣血 → 血量归零强制急救（扣 30 元）→ 穷了必须工作 → 工作消耗精力 → 精力不足要睡觉。这条压力链保证角色必须「过日子」而非随机游走，也为 LLM 决策提供了清晰的权衡空间。

**开源致谢**：架构思想来自 [joonspk-lab/generative_agents](https://github.com/joonspk-lab/generative_agents)（斯坦福 Generative Agents）与 [a16z/ai_town](https://github.com/a16z/ai_town)；API 档案管理交互参考 [CCswitch](https://github.com/farion1231/cc-switch)。工程依赖 FastAPI / pydantic / uvicorn / requests。

## API 速览

```
POST /api/control        {cmd: start|pause|resume|stop|step|speed|reset, speed?}
GET  /api/state          全量快照
GET  /api/events         ?day_from=&day_to=&type_=&q=&after_id=&limit=
GET/POST/DELETE /api/profiles[...]   档案 CRUD + /test + /active
GET/PUT /api/config      设置与玩家模型绑定
POST/DELETE /api/players 动态玩家管理
GET  /api/players/{name}/memories    记忆流
WS   /ws                 每 tick 推送快照+事件+对白
```
