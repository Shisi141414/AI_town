"""AI 小镇 · 模拟引擎包

模块结构（依赖方向自上而下，无环）：
    models      —— 全部 pydantic 数据模型
    world       —— 地图瓦片、设施、BFS 寻路
    activities  —— 14 种活动的元数据注册表
    memory      —— 记忆流（借鉴斯坦福 Generative Agents）
    llm         —— 双上游格式 LLM 客户端（OpenAI / Anthropic）
    brain       —— 决策层：LLM 提示词模板 + 规则引擎兜底
    player      —— 玩家框架：感知→记忆→决策→行动 生命周期
    simulation  —— 世界时钟、tick 循环、事件日志、广播
"""
