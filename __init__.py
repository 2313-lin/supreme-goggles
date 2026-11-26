"""短篇小说 / 短剧搜索 AI Agent（最小闭环 v1.0）。

模块地图：
    config      路径与运行时参数
    models      领域数据模型（Work / Character / Sentence / Candidate ...）
    text        中文归一化、分词、BM25、相似度
    corpus      演示语料加载、合并、索引
    registry    平台注册表、健康分、熔断器、降级链
    adapters    平台适配器（本地语料 / http_json 契约 / 小齐短剧 / DracinAPI）
    cache       L1 缓存（内存 / Redis，多实例共享）
    cache_redis 极简 RESP 客户端 + Redis 缓存后端
    understanding  Query 理解：意图、实体、改写
    channels    多路召回通道
    ranking     分层融合排序 + 证据校验
    guardrails  安全护栏
    memory      SQLite 长记忆
    audit       审计日志
    orchestrator 编排：预算、并行召回、降级、熔断联动
    api         HTTP API + 静态 UI
    cli         命令行
"""

__version__ = "1.6.0"
