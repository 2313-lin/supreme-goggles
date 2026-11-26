# 夜航 · 短篇小说 / 短剧搜索 AI Agent

按**主角名称、原文句子、主题**找短篇小说；按**剧名、主题、台词、演员**找短剧。
零第三方依赖（纯 Python 标准库 + 原生前端），`python run.py serve` 即可跑通完整闭环。

> 文档索引：[交付说明](docs/HANDOVER.md) · [架构设计](docs/ARCHITECTURE.md) ·
> [实验报告](docs/EXPERIMENT.md) · [真实联调报告](docs/LIVE_CHECK.md) ·
> [DracinAPI 联调](docs/DRACIN_CHECK.md) · [Redis 验证](docs/REDIS_CHECK.md) · [变更记录](CHANGELOG.md)

![预览](docs/preview.png)

```
小说：原文句子 > 主角 > 主题 > 标题        短剧：剧名/主题 > 台词 > 角色/演员
主源：番茄小说 / 红果短剧                降级链：七猫 → 书旗 → 米读/连尚（小说）
                                              河马 → 爱看 → 聚合层（短剧）
```

## 快速开始

```bash
cd novel-drama-agent

python run.py serve                 # 启动 API + UI：http://127.0.0.1:8765
python run.py serve --live          # 接入真实短剧聚合源（公开无需密钥）
python run.py search "她说她从来没有恨过我" --tab all
python run.py demo                  # 跑 6 条示例查询，直接看排序结果
python run.py platforms             # 查看平台健康分 / 熔断状态
python -m unittest discover -s tests -t .    # 133 个测试
```

> 环境：Python 3.10+（开发验证于 3.10.2 / Windows）。不需要 `pip install` 任何东西。
> 演示语料是**虚构的** 20 部作品（12 部小说 + 8 部短剧），用于把召回、排序、降级、熔断、
> 记忆、护栏全部跑通；真实平台接入见下文「接入真实平台」。

## 已实现的设计要点

| 设计文档要求 | 实现位置 | 可验证行为 |
| --- | --- | --- |
| 原文句子最高优先级，可信命中直接置顶 | `channels.py` / `ranking.py` | 句子逐字命中 layer 0，排在所有角色/主题命中之前 |
| 角色、主题只做补充排序 | `channels.py` | layer 2 / 3，永远在句子命中之后 |
| 短剧剧名、主题强命中优先 | `channels.py` / `ranking.py` | `name_exact/alias/pinyin` layer 0，主题 layer 2 |
| 台词精确命中仅在没有剧名/主题强命中时前置 | `ranking._promote()` | 有剧名/主题命中时台词留在 layer 4；否则提升到 layer 0 |
| 「全部」分组展示、各自排序、不混排 | `ranking.py` / `api` | `groups.novel` 与 `groups.short_drama` 分别排序 |
| 免费平台优先 + 主源降级后按链拓展 | `registry.plan()` / `orchestrator` | 番茄不可用时自动落到七猫、书旗…… |
| 拓展不是无限扩源，共享全局预算 | `orchestrator._recall_stage` | 单级超时 = min(剩余预算, 1200ms)，到点返回部分结果 |
| 超时必有结果，标记 `degraded` | `orchestrator` | 平台故障 / 预算耗尽 → `degraded=true` + 原因列表 |
| 熔断器 | `registry.CircuitBreaker` | 连续 3 次失败打开，20s 冷却后进入半开，**同一时刻只放行一个试探**（并发安全，E15 验证） |
| 只读检索 + 高危操作人工确认 | `guardrails.check_admin` | 改平台可用状态需 token + `confirm=true` |
| 长记忆只作用于同层内排序 | `ranking.personalization_boost` | 加分上限 0.05，排序键第一维仍是 layer |
| 平台注册表热插拔 | `config/platforms.json` + `registry` | 新平台只改配置；`/api/admin/platforms/reload` 热加载 |
| L1 全量、L3 按需 | `models.SourceRef` / `adapters` | 详情接口才返回句子/台词；列表只带摘要与证据 |
| 真实源省流 | `cache.py` + `AdapterHub` | 同平台/同查询 TTL 内命中缓存，冷 1.4s → 热 17ms |

## 目录结构

```
novel-drama-agent/
├─ run.py                     启动入口
├─ config/
│  ├─ platforms.json           平台注册表：类型、降级层级、超时、优先级、适配器
│  └─ settings.json            预算、结果数、限流、记忆 TTL
├─ data/
│  ├─ corpus.json              演示语料（虚构作品 / 角色 / 原文句子 / 台词）
│  ├─ memory.db                SQLite 长记忆（运行时生成）
│  └─ audit.jsonl              审计日志（运行时生成，自动轮转）
├─ nds/
│  ├─ config.py                配置加载 + 环境变量覆盖
│  ├─ models.py                Work / Character / Sentence / Evidence / Candidate
│  ├─ text.py                  中文归一化、二元分词、BM25、相似度
│  ├─ corpus.py                语料加载与索引（标题/别名/角色/主题/句子/台词）
│  ├─ understanding.py         Query 理解：意图、实体、改写、类型路由
│  ├─ channels.py              多路召回：句子 / 角色 / 主题 / 剧名 / 台词 / 角色演员
│  ├─ ranking.py               分层融合、置顶提升、证据校验、同层个性化
│  ├─ registry.py              平台注册表、健康分、三态熔断器、降级链
│  ├─ adapters.py              适配器协议 + 本地语料 + 通用 HTTP 适配器
│  ├─ adapters_icofun.py       真实短剧聚合源适配器（小齐 API，L1 元数据）
│  ├─ adapters_dracin.py       DracinAPI 适配器（Bearer 鉴权 + 分页）
│  ├─ cache.py                 L1 缓存（TTL + LRU，线程安全）
│  ├─ cache_redis.py           Redis 后端（极简 RESP 客户端，多实例共享）
│  ├─ orchestrator.py          预算、并行召回、降级、熔断联动、结果组装
│  ├─ guardrails.py            违禁主题、注入、限流、PII、管理员确认
│  ├─ memory.py                SQLite 长记忆（画像/会话/实体/程序记忆）
│  ├─ audit.py                 JSONL 审计
│  ├─ api.py                   HTTP API + 静态 UI
│  └─ cli.py                   命令行
├─ web/                        单页 UI（原生 HTML/CSS/JS，无构建、无 CDN）
├─ tests/                      133 个单元 / 端到端测试
├─ scripts/
│  ├─ probe.py                 开发用检索探针
│  ├─ experiment.py            离线实验（E1–E16）
│  ├─ make_live_config.py      生成联调配置（启用真实聚合源）
│  ├─ live_check.py            真实网络联调（直连 + 端到端 + 故障注入）
│  ├─ make_dracin_config.py    生成 DracinAPI 联调配置
│  ├─ live_check_dracin.py     DracinAPI 联调（真实 / mock）
│  └─ redis_check.py           真实 Redis 多实例共享验证
└─ docs/ARCHITECTURE.md        架构、算法与设计文档对照
```

## 一次搜索发生了什么

```
POST /api/search {query, tab, user_id, session_id}
  │
  ├─1 安全护栏    守卫：长度/HTML/控制字符 → 违禁主题 → 用户屏蔽词 → 注入标记 → 限流 → PII 脱敏
  ├─2 记忆加载    用户画像（偏好主题/作者/角色/点击作品）、会话最近查询
  ├─3 Query 理解  引号句子、作品名《》、角色词典、主题词典、同义词、错别字、拼音扩展
  │                → intent + entities + rewrites + 类型路由（novel / short_drama / 两者）
  ├─4 平台编排    按 stage 逐级激活：主源并行召回 → 判定是否"结果足够/确定性命中"
  │                → 不够则下一级备用源；单级超时 = min(剩余预算, 1200ms)
  ├─5 分层排序    同一作品多通道命中合并 → layer 置顶规则 → 同层个性化加分 → 平台优先级兜底
  ├─6 结果组装    作品卡片 + 命中证据（句子/台词/角色/主题 + 出处）+ 平台报告 + 降级原因
  └─7 异步写入    长记忆（查询/偏好/程序记忆）+ 审计日志（脱敏后）
```

## 排序规则

分层（数字越小越靠前）与基础分：

| 类型 | layer | 通道 | 基础分 |
| --- | --- | --- | --- |
| 小说 | 0 | `sentence_exact` 句子逐字（去标点）命中，或原文连续片段 ≥10 字 / 覆盖 ≥60% | 0.90–1.0（verified） |
| 小说 | 1 | `sentence_fuzzy` 片段覆盖率 ≥ 0.55 | `0.35 + 0.55×(0.6×覆盖率 + 0.4×相似度)` |
| 小说 | 2 | `character` 主角/角色/别名/拼音 | `0.65 + 0.3×重要度` |
| 小说 | 3 | `theme` 主题/标签 | `0.55 + 0.25×热度` |
| 小说 | 4 | `title_exact` / `title_fuzzy` / 拼音 | 1.0 / 0.95 / `0.55×相似度` |
| 小说 | 5 | `summary_fuzzy` 简介近似 | `0.3 + 0.25×覆盖率` |
| 短剧 | 0 | `name_exact` / `name_alias` / `name_pinyin` | 1.0 / 0.96 / 0.92 |
| 短剧 | 1 | `name_fuzzy` 剧名部分命中 | `0.72 + 0.2×覆盖率` |
| 短剧 | 2 | `theme` 主题强命中 | `0.72 + 0.22×热度` |
| 短剧 | 3 | `theme_weak` 简介含主题词 | 0.45 |
| 短剧 | 4 | `line_exact` 台词精确（可被提升到 layer 0） | 1.0（verified） |
| 短剧 | 5 | `line_fuzzy` 台词近似 | `0.45 + 0.4×(…)` |
| 短剧 | 6 | `cast` 角色/演员 | `0.5 + 0.3×重要度` |

两条置顶规则（`ranking._promote`）：

1. **小说**：整串查询与完整标题/别名一致（或拼音精确/前缀命中，score ≥ 0.95）且没有任何句子/角色/主题命中 → 标题提升到 layer 0。
2. **短剧**：完全没有剧名/主题强命中（layer ≤ 3）时，台词精确命中提升到 layer 0。

同层排序键：`(layer, -score, 平台优先级, -热度, 标题)`。
个性化加分上限 **0.05**，只加在 score 上——因为排序键第一维是 layer，**永远无法跨层**。

## 降级链、熔断与预算

`config/platforms.json`（`stage` 越小越先激活）：

| 类型 | stage 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| 小说 | 番茄 `fanqie` | 七猫 `qimao` | 书旗 `shuqi` | 米读 `midu` / 连尚 `lianshang` | 聚合 `agg_novel`（默认未接入） |
| 短剧 | 红果 `hongguo` | 河马 `hema` | 爱看 `aikan` | 聚合 `agg_drama`（默认未接入） | — |

**什么时候继续拓展？** 当前级返回后，只要还有请求类型不满足下列任一条件：

- 出现「确定性命中」：句子逐字命中 / 剧名精确 / 主角名命中；
- 或该类型 `layer ≤ 阈值` 的不同作品数 ≥ `min_results`（默认 3），阈值按意图：句子 1、角色 2、主题 3、标题 4。

**预算**：`budget_ms` 默认 1500。每级等待 `min(剩余预算, 1200ms)`；到点未返回的调用记为超时、触发熔断计数、继续用已到结果组装响应，绝不让用户等到超时错误。

**熔断**（每平台独立）：

```
closed --连续失败≥3--> open --冷却20s--> half_open --成功--> closed
                                            └──失败--> open
```

打开后该平台不再消耗预算，响应里标记 `circuit_open` 并跳过；冷却结束后放行一次试探。

**`degraded` 与 `expanded` 的区别**：平台不可用/超时/预算耗尽 → `degraded=true`；仅是"主源结果不足、拓展了备用源"→ `expanded=true`（不算降级），两者都会在 `degraded_reasons` / `warnings` 里说明。

主源故障时的实际效果（演示开关：UI 右侧「演示：可手动停用平台」，或 `python run.py simulate fanqie --down`）：

![主源降级](docs/preview-fallback.png)

## 安全护栏

- **违禁主题**：未成年性内容、毒品制作/交易、赌博平台推广、诈骗教程、自伤方法、危险物品制作 —— 直接拒绝并给替代建议。
- **提示词注入**：命中「忽略以上指令 / 输出系统提示词 / jailbreak」等只做标记（`guardrail_flags`），查询当普通文本检索，绝不执行。
- **限流**：默认 60 次/分钟/用户，滑动窗口；超限返回 `rate_limited` 与重试秒数。
- **PII 脱敏**：手机号、身份证、邮箱、银行卡在进审计前替换为占位符。
- **输入卫生**：去 HTML 标签、控制字符，长度截断 200 字。
- **高危操作**：`/api/admin/*` 需要 `X-Admin-Token` + `confirm=true`（428 Need Confirmation），只读搜索永不触发确认。
- **屏蔽主题**：用户在 UI 里设置后，命中该主题的结果会被过滤，并在 `warnings` 里报告过滤条数。

## 长记忆

SQLite（`data/memory.db`）四张表：

| 表 | 内容 | 写入时机 |
| --- | --- | --- |
| `profiles` | 偏好类型/主题/作者/角色、点击作品、屏蔽主题 | 每次搜索/点击后刷新 |
| `sessions` | 最近查询（≤10）、点击作品、摘要，默认 7 天 TTL | 搜索 / 点击 |
| `entities` | 实体权重（作品、主题、作者、角色、类型），上限 10 | 搜索（主题 +0.1）、点击（作品 +1.0 等） |
| `procedures` | 查询模式 → 实际使用的降级层级、改写，命中次数 | 每次搜索 |

偏好阈值：主题 ≥ 0.5、作者 ≥ 0.3、角色 ≥ 0.2、作品 ≥ 0.5。所有个性化只影响同层排序，上限 0.05。
`GET /api/memory` 可导出，`DELETE /api/memory?user_id=` 可清除（数据可删除）。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康、版本、语料规模、平台正常数 |
| GET | `/api/platforms` | 平台快照：状态、健康分、延迟、熔断计数 |
| GET | `/api/stats` | 语料、平台、程序记忆、运行参数 |
| GET | `/api/examples` | UI 示例查询 |
| GET | `/api/works/{id}` | 作品详情（角色、句子/台词、来源） |
| GET | `/api/memory?user_id=&session_id=` | 画像、实体权重、会话 |
| GET | `/api/audit?limit=20` | 审计尾部 |
| POST | `/api/search` | **搜索主接口** |
| POST | `/api/feedback/click` | 点击反馈（写入实体记忆） |
| POST | `/api/memory/blocked` | 设置屏蔽主题 |
| POST | `/api/cache/clear` | 清空 L1 缓存（需管理员令牌 + confirm） |
| DELETE | `/api/memory?user_id=` | 清除某用户记忆 |
| POST | `/api/admin/platforms/{id}/simulate` | 故障注入：`{down, latency_ms, reset_breaker, confirm}` |
| POST | `/api/admin/platforms/reload` | 热加载 `platforms.json` |

搜索请求：

```json
{"query": "她说她从来没有恨过我", "tab": "all", "user_id": "u_demo",
 "session_id": "s_123", "limit": 8, "budget_ms": 1500}
```

搜索响应（节选）：

```json
{
  "trace_id": "9f2c1a7b0e11",
  "query": "她说她从来没有恨过我",
  "tab": "all",
  "allowed": true,
  "understanding": {
    "intent": "sentence",
    "types": ["novel", "short_drama"],
    "entities": [{"kind": "sentence", "value": "她说她从来没有恨过我", "canonical": "…"}],
    "rewrites": ["她说她从来没有恨过我", "她说她从来没有恨过我，只是不想再见到我"]
  },
  "groups": {
    "novel": {"label": "短篇小说", "total": 1, "results": [{
      "work": {"id": "n_changye", "title": "长夜将明", "author": "沈砚", "platforms": ["fanqie", "qimao"]},
      "layer": 0, "layer_label": "原文句子命中", "score": 0.96,
      "confidence": 0.96, "confidence_label": "高", "verified": true,
      "primary_channel": "sentence_exact", "platform_primary": "fanqie",
      "also_on": ["qimao"],
      "evidence": [{"kind": "sentence", "text": "她说她从来没有恨过我，只是不想再见到我。",
                    "position": "第3章 第2段", "verified": true, "score": 1.0}]
    }]},
    "short_drama": {"label": "短剧", "total": 0, "results": []}
  },
  "degraded": false, "expanded": false, "fallback_stage": 1,
  "elapsed_ms": 6, "budget_ms": 1500,
  "platforms": [{"platform": "fanqie", "state": "healthy", "used": true, "latency_ms": 1, "candidates": 3}],
  "warnings": [], "degraded_reasons": [], "notes": [],
  "guardrail_flags": [], "personalization": {"personalized": false, "themes": [], "clicked": 0}
}
```

错误码：`empty_query` `blocked_topic` `user_blocked_topic` `rate_limited`（以上 200 + `allowed:false`）、
`bad_json`(400) `work_not_found`(404) `not_found`(404) `unauthorized`(401) `need_confirmation`(428)
`payload_too_large`(413) `internal_error`(500)。

## 配置

`config/settings.json`：

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `budget_ms` | 1500 | 单次搜索总预算 |
| `min_results` | 3 | 判定"结果不足"的作品数 |
| `max_results_per_group` | 10 | 单类型上限 |
| `max_results_all` | 8 | 「全部」时每类上限 |
| `max_query_chars` | 200 | 查询长度上限 |
| `rate_limit_per_min` | 60 | 每用户每分钟请求数 |
| `session_ttl_days` | 7 | 会话记忆保留天数 |
| `cache_ttl_s` | 30 | L1 召回缓存 TTL（0 关闭） |
| `cache_max_entries` | 256 | 内存缓存条目上限（LRU） |
| `cache_backend` | memory | `memory` 或 `redis`（多实例共享） |
| `redis_url` | redis://127.0.0.1:6379/0 | Redis 连接串（含可选密码与 db） |
| `redis_prefix` | nds: | Redis 键前缀（命名空间） |
| `llm_rerank` / `llm_timeout_ms` | false / 600 | 预留的 LLM 重排开关（默认跳过，不阻塞主链路） |

环境变量覆盖：`NDS_HOST` `NDS_PORT` `NDS_BUDGET_MS` `NDS_RATE_LIMIT` `NDS_ADMIN_TOKEN`
`NDS_MEMORY_DB` `NDS_AUDIT_LOG` `NDS_LLM_RERANK` `NDS_PLATFORMS`（注册表文件路径，联调时指向
`config/platforms.live.json`）、`NDS_CACHE_TTL`、`NDS_CACHE_MAX`、`NDS_CACHE_BACKEND`、
`NDS_REDIS_URL`、`NDS_REDIS_PREFIX`、
`NDS_API_KEY_<PLATFORM_ID>`（按平台注入密钥，避免明文写进配置文件）。

`config/platforms.json` 每个平台：`id`、`type`（novel/drama）、`role`、`stage`（降级层级）、
`adapter`、`tier`、`content_level`、`timeout_ms`、`priority`、`enabled`、`base_url`、`notes`、
`breaker_threshold`（默认 3）、`breaker_cooldown_s`（默认 20）、
`api_key` / `api_key_header` / `headers`（鉴权与自定义请求头，`api_key_header` 为空时密钥作为
`key=` query 参数发送；密钥不会出现在 `/api/platforms` 快照里）。
新增平台 = 加一段配置（+ 对应适配器名），重启或调 reload 即可。

## Redis 缓存（多实例共享）

`cache_backend=redis` 时，召回结果写入 Redis（零依赖 RESP 客户端），多个进程共享命中。
Redis 不可达会自动回退到内存并记录 `fallback_reason`。

```powershell
# 起一个真实 redis-server（任意 5.0+ 即可）
redis-server --port 6399 --bind 127.0.0.1

$env:NDS_CACHE_BACKEND='redis'
$env:NDS_REDIS_URL='redis://127.0.0.1:6399/0'
python run.py serve

# 验证：两实例共享、冷热对比、上游只调用一次
python -X utf8 scripts/redis_check.py
```

实测（真实 redis-server，tporadowski 5.0.14 Windows 构建）：实例 A 冷查询 **316.9ms** →
实例 B 命中共享缓存 **3.3ms**，上游 mock 平台仅被调用 1 次（[REDIS_CHECK.md](docs/REDIS_CHECK.md)）。

## 接入真实平台

适配器协议只有三个方法（`nds/adapters.py`）：

```python
class Adapter(Protocol):
    name: str
    def search(self, uq, kind) -> list[Candidate]: ...
    def health(self) -> bool: ...
    def detail(self, work_id) -> dict | None: ...
```

以番茄为例（示意，需自行处理签名/风控/授权）：

```python
# nds/adapters_fanqie.py
from nds.models import Candidate, Evidence, SourceRef, Work, Sentence

class FanqieAdapter:
    name = "mcp_fanqie"

    def __init__(self, client):          # MCP / 官方开放接口客户端
        self.client = client

    def search(self, uq, kind):
        rows = self.client.search_books(keyword=uq.raw, limit=20)   # L1 搜索层
        out = []
        for row in rows:
            work = Work(id=row["book_id"], type="novel", title=row["title"],
                        author=row.get("author", ""), summary=row.get("abstract", ""),
                        themes=row.get("tags", []),
                        sources=[SourceRef("fanqie", tier="primary", content_level="L3")])
            out.append(Candidate(work=work, channel="title_fuzzy", score=0.6,
                                 evidence=[Evidence(kind="title", text=work.title, verified=False)],
                                 platforms={"fanqie"}))
        return out

    def health(self):
        return self.client.ping()

    def detail(self, work_id):
        return self.client.book_info(work_id)
```

注册与切换：

```python
app = NdsApp(settings)
app.hub.register("mcp_fanqie", lambda pid, defn: FanqieAdapter(build_client(defn)))
# config/platforms.json: {"id": "fanqie", "adapter": "mcp_fanqie", ...}
```

### 零改代码接入：内置 `http_json` 契约

如果平台或聚合服务能按下面的契约返回 JSON，**不用写适配器**：把
`config/platforms.json` 里的 `adapter` 改成 `"http_json"`、填 `base_url` 即可。

```http
GET {base_url}/search?q=<原始查询>&type=novel|drama&limit=20
```

```json
{
  "works": [{
    "id": "book-1",
    "title": "长夜将明",
    "author": "沈砚",
    "summary": "…",
    "themes": ["悬疑", "复仇"],
    "tags": ["双强"],
    "url": "https://example.com/book-1",
    "tier": "primary",
    "content_level": "L2",
    "channel": "character",
    "score": 0.8,
    "sentences": [{"text": "她说她从来没有恨过我。", "type": "dialogue", "position": "第3章"}]
  }]
}
```

- `channel` 可选，取值必须是该类型的合法通道（小说：`sentence_exact` / `character` / `theme` / `title_fuzzy`…；
  短剧：`name_exact` / `theme` / `line_exact` / `cast`…），非法值自动退回 `title_fuzzy` / `name_fuzzy`。
- `sentences` 可选，用于证据展示；有则生成句子/台词证据，无则用标题证据。
- 分页：请求追加 `page`（1 起）与 `limit`（=平台 `page_size`）；响应可含 `total` / `has_more` /
  `next_page`，`max_pages`（默认 1）限制最多拉取页数；也兼容 `data` / `results` 作为数组字段名。
- 平台只负责返回作品，**分层、排序、个性化仍由本服务统一决定**，避免各平台各自排序破坏全局优先级。
- `/health` 用于健康检查，`/works/{id}` 用于详情（可选）。
- 实验 E13 用一个本地假平台真实验证了这条链路（含 HTTP 500 → 熔断 → 回落）。

**接入时必须遵守**：只读检索；遵守平台服务条款与 robots；不存储/分发完整正文与视频；
不提供播放/下载地址；不绕过验证码、签名与风控；L3 正文/字幕按需触发并缓存短 TTL；
有下架、投诉、删除请求的通道要能立刻断开。聚合层（`agg_novel` / `agg_drama`）默认
`enabled:false`，填好 `base_url` 后再打开。

## 真实聚合源联调（已接入并验证）

`agg_drama` 已接上 **小齐 API「短剧搜索与排名榜」**（公开访问、无需密钥）：

```
GET https://api.icofun.cn/API/djpxb.php?msg=<关键词>&num=<条数>
```

只取 L1 元数据（剧名 / ID / 封面 / 集数），**不请求 `n` 参数（视频链接）**，不存储、不分发内容。
适配器：`nds/adapters_icofun.py`；联调配置：`config/platforms.live.json`。

```bash
python -X utf8 scripts/make_live_config.py      # 由默认配置生成联调配置

# PowerShell
$env:NDS_PLATFORMS='config/platforms.live.json'
$env:NDS_BUDGET_MS='6000'                        # 真实源 p50 约 2s，预算需放宽
python run.py serve

python -X utf8 scripts/live_check.py             # 直连 + 端到端 + 故障注入
```

最近一次真实联调（[完整报告](docs/LIVE_CHECK.md)，18/18 通过）：

| 观测 | 结果 |
| --- | --- |
| 真实接口延迟 | 1.4–2.5s（4 次直连，波动较大） |
| 端到端结果 | 每查询 6 条：1 条本地《闪婚…》+ 5 条真实聚合短剧 |
| 聚合结果 | 带回剧名、ID、封面、集数（如「闪婚 急诊」103 集） |
| 降级路径 | 本地源 → 聚合源（stage 4），`degraded=false` |
| 句子查询 | 不向 L1 元数据源发请求，聚合污染 0 条 |
| 故障注入 | 聚合源连续 500 → 3 次后熔断；第 4 次 16ms 跳过并回落本地结果 |
| 恢复 | 重载配置 + 重置熔断后重新命中聚合源 |
| L1 缓存 | 冷查询 1395ms → 二次命中 17ms（**81x**），TTL 30s |

> 缓存是 TTL-only：上游故障在缓存有效期内可能被旧结果掩盖。做故障演练或排查时先
> `POST /api/cache/clear`（联调/实验脚本已内置清缓存）。

> 默认 `config/platforms.json` 仍保持离线（测试与演示可重复）；真实源只在 live 配置启用。
> 该接口为第三方公开服务，稳定性与配额不受本项目控制，生产接入前请自行确认其条款与合规性。

### 接入需要鉴权的聚合源（DracinAPI）

[DracinAPI](https://dracinapi.com) 是 42 平台短剧聚合 API，鉴权为 `Authorization: Bearer <key>`，
搜索路径 `/{provider}/api/v1/search?keyword=`。已内置 `nds/adapters_dracin.py`。

```bash
python -X utf8 scripts/make_dracin_config.py
NDS_API_KEY_DRACIN=你的key NDS_PLATFORMS=config/platforms.dracin.json python run.py serve

# 联调（有 key 走真实，无 key 走本地 mock，同一套鉴权/字段/分页契约）
NDS_API_KEY_DRACIN=你的key python -X utf8 scripts/live_check_dracin.py
python -X utf8 scripts/live_check_dracin.py    # mock 模式
```

平台配置字段：`provider`（如 `dramabox`）、`api_key`（或用 `NDS_API_KEY_DRACIN` 注入）、
`page_size` / `max_pages`。只取 L1 元数据（剧名/ID/封面/集数/简介/标签），不请求播放接口。
> 真实 key 需在 dracinapi.com 注册后获取；本仓库未内置任何密钥。

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

覆盖：文本归一化/BM25/引号抽取；意图与实体抽取（句子、标题、角色、主题、同义、错别字、拼音）；
分层置顶与"个性化不跨层"；平台注册表、降级链、熔断三态；违禁词/注入/限流/PII/管理员确认；
记忆读写、偏好阈值、TTL、删除；真实 HTTP 端到端（起服务、搜索、详情、点击、管理接口、路径穿越）。

## 实验运行

```bash
python -X utf8 scripts/experiment.py
```

脚本会在进程内启动真实 HTTP 服务（随机端口、独立记忆库与审计文件），跑 E1–E16 共 107 项检查，
输出 [实验报告](docs/EXPERIMENT.md) 与 [results.json](docs/experiment/results.json)。

最近一次（Python 3.10.2）：**16 组实验 / 107 项检查全部通过，总耗时约 6–7s**（多次运行稳定全绿）。

| 实验 | 观测结果 |
| --- | --- |
| E2 排序矩阵（24 项） | 7 类查询的层级与来源标签全部符合预期，p50 约 10–20ms |
| E3 主源降级 | 停用番茄 → 七猫返回同作品，`degraded=true`、`fallback_stage=2` |
| E4 熔断 | 连续 3 次超时后 `open`，第 4 次查询个位数毫秒返回（不再等主源） |
| E6 预算保护 | 150ms 预算下约 155–180ms 返回部分结果，不抛超时错误 |
| E7 护栏（11 项） | 违禁拒绝、注入只标记、401/428、PII 脱敏、限流全部生效 |
| E8 长记忆 | 点击 2 次后同层排序翻转，层级仍为 3（不跨层） |
| E9 并发 | 30 请求 / 10 线程，p95 约 90–165ms，全部成功 |
| E11 熔断恢复 | 冷却后半开试探成功 → `closed`，失败计数清零 |
| E12 半开失败 | 半开试探超时 → 立即重新 `open`，失败计数累计到 4 |
| E13 HTTP 适配器联调 | 本地假平台经真实 socket 召回；500 时熔断并回落七猫 |
| E14 热加载 | 重载 `platforms.json` 后熔断状态与失败计数保留 |
| E15 半开并发冲击 | 8 个并发请求只有 **1** 个拿到试探名额（主源只被调用 1 次），其余 7 个跳过并回落 |
| E16 恢复后并发 | `closed` 下 6 个并发全部放行，p95 约 50ms |
| E10 审计 | 80 次搜索对应 80 条审计事件，无明文手机号 |

> 说明：这些实验验证的是编排、排序、熔断、预算、护栏、记忆与并发逻辑；语料是本地虚构数据，
> 所以延迟反映的是本机 CPU 与线程调度，**不代表真实平台网络耗时**。

## 已知限制与 Roadmap

- 演示语料为虚构数据，默认离线运行；已内置一个真实短剧聚合源联调（`config/platforms.live.json`），
  但番茄/七猫/红果各自的签名、风控与字段映射仍需自行编写。
- 召回是字符 bigram + BM25 + 规则实体，没有向量召回；`llm_rerank` 是预留开关未接模型。
- 限流、熔断、记忆都是单进程内存/SQLite 实现；多实例需换 Redis / 共享 DB。
- 鉴权只有开发用管理员令牌，无用户登录体系；生产需接 OIDC。
- 短剧台词来自字幕/ASR 的字段已建模（`scene_id`、`start_time`），但未实现转写流水线。
- 下一步：向量/语义召回、LLM Query 理解与重排、点击日志训练 LTR、ASR 台词入库、平台级限速与代理池。
