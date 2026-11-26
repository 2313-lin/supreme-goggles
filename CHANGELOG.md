# 变更记录

## v1.6.0（2026-11-26）

### 新增
- **`--live` 启动开关**：`python run.py serve --live` 一条命令启用真实短剧聚合源
  （自动生成 `config/platforms.live.json` 并把预算放宽到 6000ms）；`ensure_live_config()` 收敛在 `nds/config.py`。
- **真实 Redis 多实例验证脚本**（`scripts/redis_check.py` + `docs/REDIS_CHECK.md`）：
  两个独立 NdsApp 实例共享同一 Redis，实例 A 冷查询 316.9ms → 实例 B 命中共享缓存 3.3ms，
  上游只被调用 1 次。

### 验证
- 单元测试 133 个全绿；离线实验 107/107；真实短剧源联调 18/18；真实 Redis 多实例 9/9；
  `serve --live` 端到端返回真实短剧（剧名 + 集数）。

## v1.5.0（2026-11-26）

### 新增
- **Redis 缓存后端**（`nds/cache_redis.py`）：零依赖实现 RESP2 客户端
  （PING/AUTH/SELECT/GET/SET/SETEX/DEL/SCAN/DBSIZE），与内存缓存同接口，多实例共享；
  `build_cache()` 自动回退：Redis 不可达时降级到内存并记录 `fallback_reason`。
  设置项 `cache_backend` / `redis_url` / `redis_prefix`（环境变量 `NDS_CACHE_BACKEND` /
  `NDS_REDIS_URL` / `NDS_REDIS_PREFIX`）。
- **http_json 契约分页**：请求 `page` + `limit`，响应 `total` / `has_more` / `next_page`，
  平台字段 `page_size`（默认 20）/ `max_pages`（默认 1），兼容 `data` / `results` 数组名。
- **DracinAPI 适配器**（`nds/adapters_dracin.py`）：`Authorization: Bearer <key>`、
  `/{provider}/api/v1/search`、字段映射与分页；`scripts/make_dracin_config.py`、
  `scripts/live_check_dracin.py`（有 key 走真实，无 key 走本地 mock，同一套契约）。

### 验证
- 单元测试 133 个全绿（新增 9 个 Redis 后端、9 个 Dracin、2 个分页等）。
- 离线实验 107/107；Dracin mock 联调 8/8。
- **真实 redis-server 端到端**（`scripts/redis_check.py`）：两个独立实例共享同一 Redis，
  实例 A 冷查询 316.9ms → 实例 B 热命中 3.3ms，上游只被调用 1 次（[REDIS_CHECK.md](docs/REDIS_CHECK.md)）。

## v1.4.0（2026-11-25）

### 新增
- **L1 召回缓存**（`nds/cache.py`）：TTL + LRU + 线程安全，只包 HTTP 类适配器；
  缓存键含 `platform_id + base_url + kind + 归一化查询`，换源不会误命中旧缓存。
  实测冷查询 1395ms → 二次命中 17ms（约 81x）。
- **鉴权支持**：平台配置新增 `api_key` / `api_key_header` / `headers`；
  支持 `NDS_API_KEY_<PLATFORM_ID>` 环境变量注入；密钥不出现在 `/api/platforms` 快照，
  错误信息中替换为 `***`。`http_json` 与 `icofun_shortdrama` 均支持。
- 新增接口：`POST /api/cache/clear`（管理员 + confirm）、`/api/stats` 返回缓存统计。
- 设置项：`cache_ttl_s`（默认 30）、`cache_max_entries`（默认 256）；
  环境变量 `NDS_CACHE_TTL`、`NDS_CACHE_MAX`。

### 修复
- 缓存开启后发现的错误：`TTLCache.__len__` 使空缓存为假值，导致 `if self.cache:` 判断
  把"缓存已启用但为空"当成关闭（`/api/stats` 显示 `enabled:false`、清缓存无效）；
  改为显式 `is not None`。
- 热加载/换源后旧源缓存未失效：缓存键纳入 `base_url`，reload 时同时清空缓存。

### 验证
- 单元测试 113 个全绿（新增 10 个缓存测试、4 个鉴权测试等）。
- 真实联调 18/18：缓存冷/热对比、故障熔断、恢复、句子查询不污染。

## v1.3.0（2026-11-25）

### 新增
- **真实短剧聚合源接入**：小齐 API「短剧搜索与排名榜」（公开访问、无需密钥），
  新增 `nds/adapters_icofun.py`（L1 元数据映射：剧名/ID/封面/集数）与 `config/platforms.live.json`。
- 联调脚本 `scripts/live_check.py`（直连 + 端到端 + 故障注入）与报告 `docs/LIVE_CHECK.md`；
  配置生成器 `scripts/make_live_config.py`。
- 设置项 `platforms_config` / 环境变量 `NDS_PLATFORMS`，支持切换注册表文件。
- `Work.episodes` 字段与前端「N 集」展示。

### 变更
- 单级召回等待上限从硬编码 1200ms 改为**跟随参与平台的最长 timeout**（+100ms 余量），
  仍受全局剩余预算约束——否则 2s 级的真实聚合源会被误切。
- 测试 95 个；默认 `config/platforms.json` 保持离线，真实源仅在 live 配置启用。

### 验证
- 真实联调 14/14 通过：直连 p50≈2.0s、端到端每查询 6 条（1 本地 + 5 真实）、
  聚合源连续 500 → 3 次熔断 → 第 4 次 18ms 跳过并回落本地。
- 离线实验 107/107；单元测试 95 个全绿。

## v1.2.0（2026-11-25）

### 新增
- 实验 E15：半开并发冲击（8 并发，验证只放行 1 个试探）。
- 实验 E16：恢复后并发（closed 状态下 6 并发全部放行）。
- 单元测试：半开只放行一个并发试探、过期试探名额自动释放。

### 修复
- **熔断器半开状态并发漏洞**：原实现 `can_try()` 在 half_open 对每个调用者都返回 True，
  冷却结束瞬间 N 个并发请求会同时打向刚出故障的主源。现引入“单试探位”：
  `can_try()` 申请名额、`available()` 只读探测、`trial_timeout_s`（默认 5s）兜底，
  编排层同步改为“调度时申请一次”，跳过原因区分 `open` / `half_open`。

### 变更
- 实验总数 16 组 / 107 项检查；自动化测试 87 个。
- README 补充半开并发语义与实验索引；ARCHITECTURE 更新熔断并发说明。

## v1.1.0（2026-11-25）

### 新增
- `http_json` 适配器实现通用 JSON 契约（`GET {base_url}/search?q=&type=&limit=`），
  零代码接入符合契约的平台/聚合服务；新增 6 个适配器测试。
- 熔断阈值与冷却可按平台配置：`breaker_threshold`、`breaker_cooldown_s`。
- 实验 E13：本地假平台经真实 socket 联调（含 500 回落）；E14：热加载保留熔断状态。
- 实验 E11：冷却 → 半开 → 恢复；E12：半开失败立即重开。

## v1.0.0（2026-11-25）

### 新增
- 多路召回：原文句子 / 角色 / 主题 / 剧名 / 台词 / 演员。
- 分层置顶排序、证据校验、同层个性化（上限 0.05）。
- 平台注册表、降级链、健康分、三态熔断、全局预算。
- 安全护栏：违禁主题、提示词注入标记、限流、PII 脱敏、管理员令牌 + 人工确认。
- SQLite 长记忆：画像 / 会话 / 实体权重 / 程序记忆，支持导出与删除。
- HTTP API（13 个接口）+ 单页 UI（分组、证据、平台状态、演示故障开关）。
- 演示语料 20 部作品；实验 E1–E10 与 77 个测试。
