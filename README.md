# 🏘️ AI 小镇

一个基于大语言模型的多智能体生活模拟游戏：五位「居民」合租一个大平层，各由一个 LLM 扮演。他们开局**自己给自己起名字、选颜色**，然后在同一份提示词下，凭借各自的记忆、数值状态和人际关系自主生活——做饭、打游戏、上班、看电影、买花送礼、交朋友、恋爱，也可能吵架搞破坏。

**核心特点**

- 🎭 **开局身份自选**：每个居民的模型自己决定叫什么（小x 格式）、什么颜色
- 🧠 **模型即人格**：5 个居民可各接一个模型（DeepSeek / GLM / Qwen / ERNIE / MiniMax…），同一提示词下观察行为差异
- 🏠🌆 **双场景世界**：室内合租之家（5 卧 / 客厅 / 厨房 / 餐厅 / 双卫 / 洗衣区 / 大阳台）+ 室外小镇（商店 / 公园 / 办公楼 / 电影院 / 健身房 / 医院 / 施工地 / 田地），两图独立比例尺，居民从大门出入
- 🤝 **协同增益社交**：一起打游戏/吃饭/工作有数值加成，好感度调制加成幅度——关系差一起做事反而互相拖累；每次互动后由模型自己决定好感增减（-5~+5）
- 💐 **物品与装饰**：去商店买鲜花/盆栽，布置自己的房间或公共区域（持续心情加成），或送给心上人
- 🕸 **好感网络图**：实时可视化的居民关系网
- 📋 **模型列表获取**：填好 API 后一键拉取网关全部可用模型，点选即用
- ✏️ **可编辑统一提示词**：网页上直接改，下次决策生效
- 🛡️ **永不下线**：LLM 不可用时自动降级内置规则引擎；对白生成失败用内置文案兜底

## 快速开始

```bash
pip install -r requirements.txt
python server.py        # 打开 http://127.0.0.1:8300
```

不配 API 直接点「开始」即可以规则引擎模式游玩。接入大模型：侧栏「API / 模型」→ 新增档案（Base URL / Key / 上游格式）→ 点「获取模型列表」拉取可用模型 → 测试 → 启用 → 在「玩家模型绑定」给每个居民指定模型。页面上有 📖 使用指南按钮，含完整说明。

## 架构

```
engine/               模拟引擎（纯 Python，无 Web 依赖）
├── models.py         pydantic 数据模型（pid 稳定 ID / 物品 / 装饰度）
├── world.py          双 26×26 方形地图 + 井字形室内布局 + BFS 寻路 + 相遇判定
├── activities.py     24 种活动（场景标注/协同增益公式/物品/装饰）
├── memory.py         记忆流（recency × importance 检索）
├── llm.py            OpenAI/Anthropic 双格式客户端
├── brain.py          统一提示词 + 身份自选 + LLM 好感增减 + 规则引擎
├── player.py         玩家框架（感知→记忆→决策→行动 + 跨场景出行）
└── simulation.py     世界时钟 + 身份阶段 + 好感异步队列 + 相遇播报
server.py             FastAPI：控制/档案/模型列表/提示词/关系网/WS 广播
web/index.html        单文件前端：双场景 Canvas + 关系网 + 指南
```

### 设计说明

**同一提示词，人格涌现**：提示词不含任何人设，只有时间、数值、记忆、好感、活动菜单。个体差异来自经历的沉淀（斯坦福 Generative Agents 思想）。身份自选把这个思想推到开局——连名字都是模型自己选的。

**协同增益公式**：`倍率 = 人数增益 × 好感调制`。每多 1 人 +0.35 倍；好感 50 → ×1.0，100 → ×1.5，0 → ×0.5。关系不睦（倍率 <0.75）时正收益再打五折——「不如一个人做」。

**跨场景出行**：去另一场景的设施时，寻路分两段——先到本场景大门，切换场景坐标，再走到目的地。就像真实世界的「先下楼」。

**好感决定权交给模型**：互动结束后向该居民的模型提问「你对 TA 的好感变化多少」（-5~+5 的整数），规则只框边界不框方向。这让社交关系成为真正的涌现现象而非参数脚本。

## API 速览

```
POST /api/control        start|pause|resume|stop|step|speed|reset
GET  /api/state          快照（含身份阶段状态/公共装饰度）
GET  /api/map?scene=     house|town 双地图
GET  /api/models         ?profile_id= 或 ?base_url=&api_key=&format= 直查
GET/PUT /api/prompt      统一提示词
GET  /api/relationships  好感网络数据
GET/POST/DELETE /api/profiles[...]   档案 CRUD + /test + /active
GET/PUT /api/config      设置 + pid→模型绑定
GET  /api/players/{name}/memories    记忆流
WS   /ws                 每 tick 快照 + 事件 + 对白
```

## 隐私

`config.json`（含 API Key）与 `API.txt` 已列入 `.gitignore`。请勿将密钥提交到公共仓库；推送前可用 `git log --all -p | grep <密钥>` 自查。

## 开源致谢

架构思想来自 [joonspk-lab/generative_agents](https://github.com/joonspk-lab/generative_agents)（斯坦福 Generative Agents）与 [a16z/ai_town](https://github.com/a16z/ai_town)；API 档案管理交互参考 [CCswitch](https://github.com/farion1231/cc-switch)。工程依赖 FastAPI / pydantic / uvicorn / requests。

---

*构建：claude · 2026-10 · v2*
