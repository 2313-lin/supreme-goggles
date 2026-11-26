# 交付说明 · 夜航（Novelette & Drama Search）

- 版本：**v1.6.0**
- 交付日期：2026-11-25
- 运行环境：Python 3.10+，零第三方依赖；前端为原生 HTML/CSS/JS，无需构建
- 状态：最小闭环可运行，133 个自动化测试 + 16 组 / 107 项实验检查 + 真实聚合源联调 18/18 + Dracin 联调 8/8 全部通过

## 1. 交付物一览

| 类别 | 内容 | 位置 |
| --- | --- | --- |
| 后端服务 | 检索编排、平台注册表、熔断、护栏、记忆、API | `nds/` |
| 配置 | 平台注册表（降级链/超时/熔断参数）、运行参数 | `config/` |
| 演示语料 | 20 部虚构作品（12 小说 + 8 短剧）、角色、原文句子、台词 | `data/corpus.json` |
| 前端 | 单页搜索 UI（分组结果、证据、平台健康、记忆、故障开关） | `web/` |
| 测试 | 133 个单元 / 端到端测试 | `tests/` |
| 实验 | E1–E16 共 107 项检查，自动生成报告 | `scripts/experiment.py`、`docs/EXPERIMENT.md` |
| 真实联调 | 小齐 API 短剧聚合源 + L1 缓存 + 鉴权，18 项检查 | `scripts/live_check.py`、`docs/LIVE_CHECK.md` |
| Dracin 联调 | DracinAPI（Bearer 鉴权 + 分页），真实/mock，8 项检查 | `scripts/live_check_dracin.py`、`docs/DRACIN_CHECK.md` |
| 文档 | 使用、架构、实验、交付 | `README.md`、`docs/` |
| 打包 | 源码压缩包（不含运行时数据） | `../novel-drama-agent-v1.6.0.zip` |

## 2. 5 分钟上手

```bash
cd novel-drama-agent

python run.py serve                  # UI + API：http://127.0.0.1:8765
python run.py serve --live           # 接入真实短剧聚合源（公开无需密钥）
python run.py search "她说她从来没有恨过我" --tab all
python run.py demo                   # 6 条示例查询
python -m unittest discover -s tests -t .   # 133 个测试
python -X utf8 scripts/experiment.py        # 16 组实验 / 107 项检查
```

可选的演示操作：

- UI 右侧「演示：可手动停用平台」→ 停用番茄，再搜「林昭」，观察自动降级到七猫；
- 命令行：`python run.py simulate fanqie --down` / `--up` / `--reset`；
- 直接访问 `http://127.0.0.1:8765/?q=闪婚&tab=drama` 或 `?work=d_shanhun`。

## 3. 验证证据

| 项目 | 结果 |
| --- | --- |
| 单元 / 端到端测试 | 133 passed（`unittest`，含真实 HTTP 起服务） |
| 实验 E1–E16 | 107/107 检查通过，约 6s，可重复运行 |
| 排序规则 | 句子命中置顶、剧名/主题优先于台词、台词在无强命中时提升 |
| 降级链 | 番茄停用 → 七猫返回同作品，`fallback_stage=2` |
| 熔断 | closed→open→half_open→closed 全流程；半开并发只放行 1 个试探 |
| 预算 | 150ms 预算下约 150–180ms 返回部分结果，不抛超时 |
| 安全护栏 | 违禁拒绝、注入只标记、401/428、PII 脱敏、限流 |
| 长记忆 | 点击后同层排序翻转，不跨层；支持导出/删除 |
| 并发 | 30 请求 / 10 线程全部成功；p95 约 90–165ms |
| 真实聚合联调 | 小齐 API：18/18；故障时 3 次熔断、第 4 次约 16ms 跳过；缓存冷 1395ms → 热 17ms |
| Redis 缓存 | RESP 协议后端，多实例共享（真实 redis-server 验证：冷 316.9ms → 热 3.3ms）；不可达自动回退内存 |
| DracinAPI | Bearer 鉴权 + 分页，mock 联调 8/8；真实需 `NDS_API_KEY_DRACIN` |

截图：`docs/preview.png`（句子命中）、`docs/preview-fallback.png`（主源降级）。

## 4. 目录索引

```
novel-drama-agent/
├─ run.py                    启动入口
├─ README.md                 使用文档
├─ CHANGELOG.md              版本与修复记录
├─ config/
│  ├─ platforms.json         平台注册表：类型/层级/超时/优先级/熔断参数
│  └─ settings.json          预算、结果数、限流、记忆 TTL
├─ data/corpus.json          演示语料（虚构）
├─ nds/
│  ├─ api.py                 HTTP 路由、静态 UI、管理接口
│  ├─ orchestrator.py        预算、并行召回、降级、熔断联动、结果组装
│  ├─ registry.py            注册表、健康分、三态熔断（单试探位）
│  ├─ adapters.py            适配器协议 + 本地语料 + http_json 契约
│  ├─ adapters_icofun.py     小齐短剧聚合源适配器（真实网络，仅 L1 元数据）
│  ├─ adapters_dracin.py     DracinAPI 适配器（Bearer 鉴权 + 分页）
│  ├─ cache.py               L1 缓存（TTL + LRU，换源按 base_url 失效）
│  ├─ cache_redis.py         Redis 后端（极简 RESP 客户端，多实例共享）
│  ├─ understanding.py       意图/实体/改写/类型路由
│  ├─ channels.py            六类召回通道
│  ├─ ranking.py             分层置顶、证据校验、同层个性化
│  ├─ corpus.py / text.py    语料索引、中文归一化/BM25/相似度
│  ├─ guardrails.py          违禁/注入/限流/PII/管理员确认
│  ├─ memory.py              SQLite 长记忆（画像/会话/实体/程序）
│  ├─ audit.py / models.py   审计、数据模型
│  └─ cli.py                 命令行
├─ web/                      单页 UI
├─ tests/                    113 个测试
├─ scripts/
│  ├─ experiment.py          离线实验（起真实 HTTP 服务）
│  ├─ make_live_config.py    生成联调配置
│  ├─ make_dracin_config.py  生成 DracinAPI 联调配置
│  └─ live_check.py / live_check_dracin.py  真实聚合源 / DracinAPI 联调
│  └─ redis_check.py         真实 Redis 多实例共享验证
└─ docs/
   ├─ ARCHITECTURE.md        架构、算法、设计文档逐章对照
   ├─ EXPERIMENT.md          16 组实验报告
   ├─ LIVE_CHECK.md          真实聚合源联调报告
   ├─ DRACIN_CHECK.md        DracinAPI 联调报告
   ├─ REDIS_CHECK.md        真实 Redis 多实例验证报告
   └─ preview*.png           界面截图
```

## 5. 关键设计决策

1. **分层层级是排序第一维**：`(layer, -score, 平台优先级, -热度, 标题)`。个性化加分上限 0.05，
   只改 score，因此永远无法跨层。
2. **确定性命中即停止扩源**：句子逐字命中、剧名精确、主角名命中时不再为“凑数量”消耗备用源预算。
3. **超时必有结果**：单级等待 `min(剩余预算, 1200ms)`，到点返回已召回内容并标记 `degraded`，
   而不是抛 5xx。
4. **熔断半开并发安全**：`can_try()` 申请名额（半开只放行一个）、`available()` 只读探测、
   `trial_timeout_s` 兜底；这是实验 E15 发现并修复的真实缺陷。
5. **平台只返回作品**：`http_json` 契约不把排序权交给平台，分层与个性化统一由本服务决定。
6. **写操作全部可拒**：只读检索无需确认；改平台状态需管理员令牌 + `confirm=true`；
   记忆可导出、可删除，审计只存脱敏字段。

## 6. 接入真实平台（三步）

1. **已经接好一个真实示例**：小齐 API 短剧聚合源（公开、无需密钥），见 `config/platforms.live.json`
   与 `nds/adapters_icofun.py`；联调命令：`python -X utf8 scripts/live_check.py`。
   自己接新平台时：若服务能按 `README` 的 `http_json` 契约返回 JSON，把 `adapter` 改为
   `http_json`、填 `base_url`，重启或调 `POST /api/admin/platforms/reload` 即可。
2. 若需要签名/风控/私有协议：实现 `Adapter` 三个方法
   （`search` / `health` / `detail`），用 `app.hub.register("your_adapter", ...)` 注册，
   配置里引用该名字（可参考 `adapters_icofun.py` 的字段映射与错误归一）。
3. 用 `python -X utf8 scripts/experiment.py`、`tests/`、`scripts/live_check.py` 分别做离线回归、
   单元测试与真实网络联调；真实网络耗时以联调报告为准。

**合规约束**：只读检索；遵守平台条款与 robots；不存储/分发完整正文与视频；不提供播放/下载地址；
不绕过验证码、签名与风控；L3 内容按需拉取并短 TTL 缓存；收到下架/投诉要能立即断开通道。

## 7. 已知限制（避免误读）

- 演示语料为虚构数据，默认离线运行；已提供一个真实短剧聚合源联调示例（第三方公开服务，
  稳定性与配额不受本项目控制），番茄/七猫/红果的签名与风控客户端不在仓库内。
- 召回是字符 bigram + BM25 + 规则实体，无向量召回；`llm_rerank` 仅有开关与超时参数。
- 限流、熔断、记忆为单进程内存/SQLite；多实例需换 Redis / 共享数据库。
- 鉴权只有开发用管理员令牌；无用户体系、配额计费、灰度发布。
- 短剧字幕/ASR 字段已建模，但未实现转写流水线。

## 8. 维护约定

| 想改什么 | 改哪里 |
| --- | --- |
| 调预算 / 结果数 / 限流 | `config/settings.json` 或环境变量 `NDS_*` |
| 调平台顺序 / 超时 / 熔断参数 | `config/platforms.json`（`stage`、`timeout_ms`、`breaker_*`） |
| 加一路召回 | `nds/channels.py` + `NOVEL_LAYERS` / `DRAMA_LAYERS` |
| 换排序 / 个性化 | `nds/ranking.py`（保持 layer 为第一维） |
| 换存储 | 实现 `MemoryStore` 同名接口 |
| 加实验 | `scripts/experiment.py` 增加 `eN_xxx()` 并加入 `run()` 顺序 |
| 调缓存 | `config/settings.json` 的 `cache_ttl_s` / `cache_max_entries`；或 `POST /api/cache/clear` |
| 换 Redis | `NDS_CACHE_BACKEND=redis NDS_REDIS_URL=redis://...`；不可达自动回退内存 |
| 接鉴权源 | 平台配置 `api_key_header` + 环境变量 `NDS_API_KEY_<ID>`（勿把密钥写进仓库） |

改完执行：`python -m unittest discover -s tests -t .` 与 `python -X utf8 scripts/experiment.py`。
