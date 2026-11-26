# 架构说明

本文说明代码怎么实现设计文档（v1.1 + 可运行最小闭环 v1.0）里的每个机制，以及从哪扩展。

## 1. 分层与模块职责

```
┌──────────────────────────────────────────────────────────────────────┐
│ 表现层   web/（原生单页 UI）        cli.py（search/demo/stats/simulate）│
├──────────────────────────────────────────────────────────────────────┤
│ 接口层   api.py  线程化 HTTP、路由、JSON、静态文件、CORS、限流入口      │
├──────────────────────────────────────────────────────────────────────┤
│ 编排层   orchestrator.py  预算、并行召回、降级、熔断联动、结果组装      │
│          guardrails.py    查询审查 / 注入 / 限流 / PII / 管理员确认     │
│          memory.py        画像、会话、实体权重、程序记忆（SQLite）      │
│          audit.py         JSONL 审计（自动轮转）                       │
├──────────────────────────────────────────────────────────────────────┤
│ 检索层   understanding.py 意图 / 实体 / 改写 / 类型路由                 │
│          channels.py      句子·角色·主题·剧名·台词·演员 六类通道       │
│          ranking.py       分层、置顶提升、证据校验、同层个性化          │
│          corpus.py        语料索引（标题、别名、角色、主题、BM25）      │
│          text.py          归一化、bigram、BM25、相似度                 │
├──────────────────────────────────────────────────────────────────────┤
│ 平台层   registry.py      注册表 / 健康分 / 三态熔断 / 降级链           │
│          adapters.py      适配器协议 + 本地语料 + 通用 HTTP 骨架        │
│          adapters_icofun.py 真实短剧聚合源（小齐 API，L1 元数据）        │
│          adapters_dracin.py DracinAPI（Bearer 鉴权 + 分页）              │
│          cache.py         L1 缓存（TTL + LRU，只包 HTTP 适配器）         │
│          cache_redis.py   Redis 后端（RESP 客户端，多实例共享）           │
├──────────────────────────────────────────────────────────────────────┤
│ 数据     data/corpus.json（语料）  data/memory.db（记忆）  data/audit.jsonl │
└──────────────────────────────────────────────────────────────────────┘
```

线程模型：`ThreadingHTTPServer` 每连接一线程；一次搜索内部用短生命周期
`ThreadPoolExecutor` 并行调用同级的多个平台；记忆与审计写走进程级
`ThreadPoolExecutor(2)`，`close()` 时 `wait=True` 保证落库。

## 2. 请求生命周期（对应 `SearchAgent.search`）

1. `normalize_tab()` → `all | novel | short_drama`。
2. `guardrails.screen()`：清洗 → 违禁主题 → 用户屏蔽 → 注入标记 → 限流 → PII 脱敏。
   拒绝时直接返回 `allowed:false`，不进入检索、不写记忆。
3. `memory.get_profile(user_id)`：偏好主题/作者/角色/点击作品/屏蔽主题。
4. `understanding.understand(query, corpus, tab)`：
   - 引号内容 → 句子 / 标题实体（最高优先）；
   - 词典最长匹配（作品名、别名、角色名、主题名），重叠片段只取一次；
   - 同义词映射（先婚后爱 → 闪婚）、错别字纠正（总栽 → 总裁）；
   - 纯拼音/首字母（`cyj` → `cyjm` → 长夜将明）；
   - 长句启发式 → 附加句子实体（与主题/角色并存，走多路召回）；
   - 类型路由：显式 tab > 实体类型 > "短剧/台词" 等关键词 > 两者。
5. `registry.plan(kind)` 得到 `[[主源], [备用1], …]`；`stage` 逐级激活。
6. `_recall_stage()`：对 `(kind, platform)` 组合并行提交，`wait(timeout)` 收集；
   超时的调用记为 `timeout`、记一次熔断失败，但响应继续。
   HTTP 类适配器先过 L1 缓存（键含 `platform_id + base_url + kind + 归一化查询`），
   命中则跳过网络；`local_corpus` 不缓存。
7. 每个平台成功返回就 `report_success(latency)`；失败/超时 `report_failure(reason)`。
8. 停止条件（按类型独立判断）：
   - 出现确定性命中（句子逐字 / 剧名精确 / 主角名）；
   - 或满足 `min_results` 的强命中数量；
   - 或没有下一级可激活 / 预算耗尽。
9. `ranking.rank()` 每类型独立：合并同作品多通道证据 → 置顶规则 → 个性化 → 排序 → 截断。
10. 组装 `groups` + 平台报告 + `degraded/expanded` + `warnings/notes`，异步写记忆与审计。

## 3. 召回通道

| 通道 | 数据来源 | 过滤 | 证据 |
| --- | --- | --- | --- |
| `sentence_exact` | 小说句子（L3） | 去标点后逐字相等，或原文连续片段 ≥10 字 / 覆盖 ≥60% | 句子原文 + 章节段落 + verified |
| `sentence_fuzzy` | 小说句子 | 二元组覆盖率 ≥0.55 | 句子 + 相似分 |
| `character` | 角色表 | 名称/别名/拼音精确；角色仅出现在本平台作品 | 角色名 + 身份 + 描述 |
| `theme` | 主题与标签 | 规范化主题名相等 | 作品主题列表 |
| `title_exact` / `title_fuzzy` | 标题与别名 | 全等 / 包含或相似度 ≥0.5 | 命中位置 |
| `name_exact` / `name_alias` / `name_pinyin` / `name_fuzzy` | 短剧标题 | 全等 / 别名 / 拼音首字母 / 部分命中 | 命中位置 |
| `theme` / `theme_weak` | 短剧主题 | 标签相等 / 简介含主题词 | 标签或简介片段 |
| `line_exact` / `line_fuzzy` | 字幕台词（L3） | 去标点全等或覆盖率 ≥60% / 二元组 ≥0.5 | 台词 + 集数时间戳 |
| `cast` | 短剧角色/演员 | 名称/别名精确 | 角色 + 描述 |
| `summary_fuzzy` | 简介 | 二元组覆盖率 ≥0.4 | 简介片段 |

所有通道都在 `platform` 维度隔离：只有作品在该平台有 `SourceRef` 才会被该平台适配器返回，
因此"同一作品在七猫上也能找到"会体现在 `also_on`，而不会重复出卡。

## 4. 排序与置顶

排序键：`(layer, -score, 平台优先级, -热度, 标题)`。

- `layer`：通道表映射（`channels.NOVEL_LAYERS` / `DRAMA_LAYERS`），初始值。
- `_promote`：
  - 小说：`layer ≤ 3` 一个都没有，且有 `title_exact ≥ 0.95`（含拼音精确/前缀）→ 标题层升到 0；
  - 短剧：`layer ≤ 3` 一个都没有，且存在 `line_exact` → 台词层升到 0。
- 同作品多通道：取最小 layer（同层取最高分）作为主通道，其余进 `matched_channels`，
  证据去重后最多展示 4 条（verified 优先）。
- `confidence`：`min(0.99, 0.72×(0.95-0.11×layer) + 0.28×score)`，展示为 高/中/低。
- 个性化：`personalization_boost ≤ 0.05`，来源为主题命中 +0.012/个、作者 +0.015、
  角色 +0.008、历史点击 +0.01×min(3, 次数)、偏好类型 +0.004。排序键第一维仍是 layer。

## 5. 预算与降级伪代码

```python
stage, fallback_stage = 1, 1
while stage <= max_stages:
    todo = [(kind, pid) for kind in kinds
            for pid in plan[kind][stage-1]
            if not sufficient(kind) and platform_can_try(pid)]
    if not todo:
        if stage == max_stages: break
        stage += 1; continue
    if remaining_budget <= 50ms:
        degraded = True; break
    fallback_stage = stage
    for (kind, pid), (cands, latency, error) in parallel_recall(todo, min(remaining, max_platform_timeout + 100ms)):
        if error: breaker.failure(pid); degraded = True; continue
        breaker.success(pid); candidates[kind] += filter_blocked(cands)
    if all(sufficient(kind) for kind in kinds): break
    if any(definitive(kind) for kind in kinds): break
    stage += 1
```

`sufficient(kind)`：`definitive(kind)` 或 `count(works, layer ≤ 意图阈值) ≥ min_results`。
意图阈值：句子 1、角色 2、主题 3、标题 4、其它 2。

## 6. 熔断状态机

```
            fail ≥ threshold
closed ───────────────────────► open
  ▲                               │  cooldown (20s)
  │  success                      ▼
  └────────────────────────── half_open
                                  │ fail → open (立即，重新计时)
```

`PlatformRuntime.can_try()` 是唯一入口：`enabled=false`、`forced_down`、`open` 都返回 False，
因此这些平台既不消耗预算，也不计入 `used`。

**并发语义**：`can_try()` 是"申请名额"——冷却结束后第一个调用把状态切到 `half_open` **并占用试探位**，
其余并发请求在试探位释放前一律返回 False；`available()` 是只读探测，不占名额，仅用于展示与跳过标注。
试探位在 `record_success()` / `record_failure()` 时释放，并有 `trial_timeout_s`（默认 5s）兜底，
避免某次试探异常未回报导致熔断器永久卡在半开。E15 用 8 个并发请求验证了"只放行 1 个"。

阈值与冷却可按平台配置（`breaker_threshold` 默认 3、`breaker_cooldown_s` 默认 20s），
测试中可缩短冷却以观测 `closed → open → half_open → closed` 全流程（实验 E11/E12）。

鉴权与缓存：平台配置支持 `api_key` / `api_key_header` / `headers`（密钥可用
`NDS_API_KEY_<ID>` 注入，快照与错误信息不泄露）；HTTP 适配器的召回结果经缓存
（`memory` 或 `redis`，`NDS_CACHE_BACKEND` 切换；Redis 不可达自动回退内存）缓存，
换源或热加载时按 `base_url` 自然失效并清空。
缓存是 TTL-only：缓存有效期内上游故障会被旧结果掩盖，因此故障演练/实验前需显式清缓存
（E13 与 live_check 都这样做）。

分页：`http_json` 契约支持 `page` + `limit`，响应 `total` / `has_more` / `next_page`，
平台字段 `page_size` / `max_pages`；DracinAPI 走独立适配器（Bearer 鉴权 + 字段映射）。

## 7. 记忆写入与个性化

| 事件 | 写入 |
| --- | --- |
| 搜索 | `sessions.recent_queries`、`entities(theme)+0.1`、`entities(type)+0.05`、`procedures` |
| 点击 | `entities(work)+1.0`、`entities(theme)+0.4`、`entities(author)+0.3`、`entities(character)+0.2`、`sessions.clicked_works` |
| 改屏蔽 | `profiles.blocked_topics` |
| 删除 | 清理该 user_id 的画像/实体/会话 |

`_refresh_preferences` 每次按阈值把权重最高的实体投影到画像，个性化读取的是画像，
因此"点击 → 偏好 → 排序"形成闭环，但永远不改变分层。

## 8. 安全边界

- 检索是只读的；`adapters` 没有任何写平台数据的接口。
- 唯二的状态变更接口（模拟故障、热加载）都要求管理员令牌 + `confirm=true`。
- 审计只记脱敏后的查询、意图、平台、耗时、结果数、flags；不记正文与 PII。
- 记忆有删除接口与 7 天会话 TTL；`purge_expired_sessions` 可定时调用。

## 9. 与设计文档的对应关系

| 设计文档章节 | 实现 | 说明 |
| --- | --- | --- |
| §1.1 搜索能力 | `understanding.py`、`channels.py` | 主角 / 句子 / 主题 / 剧名 / 台词 / 演员 |
| §1.2 平台优先级 | `config/platforms.json`、`registry.plan` | 番茄、红果为主源，备用链可配 |
| §1.3 全局原则 1–8 | `ranking._promote`、`orchestrator`、`guardrails`、`memory` | 逐条有测试 |
| §2 总体架构 | 上文分层 | 记忆→理解→路由→召回→编排→排序→组装→异步写 |
| §3 数据模型 | `models.py`、`corpus.json` | Work/Character/Sentence/Theme/UserProfile/Session/Entity/Procedure |
| §4.1 通道能力分层 | `SourceRef.content_level`、`detail()` | L1 列表、L2 详情、L3 句子/台词按需 |
| §4.2 平台注册表 | `PlatformDef`、`PlatformRegistry.reload` | 热插拔、健康检查、超时预算 |
| §5 查询理解与路由 | `understanding.py` | 意图、实体、改写、类型路由 |
| §6 检索与排序 | `channels.py`、`ranking.py` | 多路召回 + 分层置顶 |
| §7 熔断 | `CircuitBreaker` | 三态、阈值、冷却、半开试探 |
| §8 安全护栏 | `guardrails.py` | 审查、注入、限流、PII、确认 |
| §9 长记忆 | `memory.py` | 画像/会话/实体/程序记忆 |
| §10 结果展示 | `web/` | 分组卡片、证据、置信、平台、降级提示 |
| §11 API | `api.py` | 见 README 表格 |
| §12 MVP 路径 | 本仓库 | 语料 → 通道 → 排序 → 编排 → 护栏 → 记忆 → UI |
| §13 风险与应对 | `degraded`/`warnings`/熔断/审计 | 超时必有结果、可见降级原因 |
| §14 最小闭环 | `run.py serve` + 测试 | 一条命令可跑 |

## 10. 扩展点

| 想做的事 | 改哪里 |
| --- | --- |
| 接一个新平台 | 加 `config/platforms.json` 条目；遵循 `http_json` 契约即可零代码，特殊签名则写 Adapter 并 `hub.register`（参考 `adapters_icofun.py`） |
| 接真实聚合源 | `scripts/make_live_config.py` 生成 `config/platforms.live.json`；或用 `NDS_PLATFORMS` 指向自定义注册表 |
| 加一路召回（如向量） | `channels.recall()` 增加函数 + 在 `NOVEL_LAYERS` / `DRAMA_LAYERS` 注册层级 |
| 换排序策略 | `ranking.rank()` + `personalization_boost()`；保持"layer 第一维"即可 |
| 接 LLM 重排 | `Settings.llm_rerank` 打开，在 `rank()` 后加一次带 `llm_timeout_ms` 的重排，超时降级 |
| 换存储 | `MemoryStore` 接口不变，换 Postgres/Redis 实现 |
| 多实例 | 熔断、限流、记忆迁到共享存储；`api` 保持一致 |
| 鉴权 | `guardrails.check_admin` 换成 OIDC/JWT 中间件 |
| 定时任务 | 调 `memory.purge_expired_sessions()`、平台健康巡检 |

## 11. 明确未实现（避免误读）

- 没有真实抓取逻辑：番茄/七猫/红果的签名、风控、MCP 客户端不在仓库内。
  已接入一个真实短剧聚合源（小齐 API，仅 L1 元数据）作为联调示例，见 `docs/LIVE_CHECK.md`。
  （`http_json` 适配器已实现通用契约，实验 E13 用本地假平台验证了真实 HTTP 路径。）
- 没有向量库、没有 embedding、没有 ASR 转写流水线。
- `llm_rerank` 只有开关与超时参数，没有接模型（默认关闭，不影响主链路）。
- 用户体系、配额计费、分布式限流、灰度发布均未实现。
