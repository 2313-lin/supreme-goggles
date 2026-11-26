# 实验运行报告

- 运行时间：2026-11-26T00:10:58+08:00（Asia/Shanghai）
- 环境：Python 3.10.2，真实 HTTP 服务 `http://127.0.0.1:5314`（进程内起服务，随机端口）
- 规模：16 组实验，107 项检查，**107 通过 / 0 失败**，总耗时 5.22s
- 原始数据：[results.json](experiment/results.json)

## 总览

| # | 实验 | 检查项 | 结果 | 关键指标 |
| --- | --- | --- | --- | --- |
| E1 | 冷启动、语料与配置 | 10 | ✅ 通过 | platforms_healthy=8 |
| E2 | 意图识别与分层排序矩阵 | 24 | ✅ 通过 | latency_p50_ms=12.4 |
| E3 | 主源（番茄）不可用 → 自动降级到七猫 | 5 | ✅ 通过 | latency_ms=14.4；fallback_stage=2 |
| E4 | 超时 → 熔断打开 → 跳过主源（预算保护） | 4 | ✅ 通过 | threshold_expected=3 |
| E5 | 小说主源 + 第一备用全部不可用 | 4 | ✅ 通过 | latency_ms=3.2 |
| E6 | 慢平台 + 150ms 预算：超时必有结果 | 3 | ✅ 通过 | elapsed_ms=185.1；budget_ms=150 |
| E7 | 安全护栏：违禁、注入、鉴权、PII、限流 | 11 | ✅ 通过 | blocked_message=该查询涉及毒品制作或交易，无法提供检索。；audit_events=56 |
| E8 | 长记忆：点击 → 同层排序翻转（不跨层） | 5 | ✅ 通过 |  |
| E9 | 并发压测：30 请求 / 10 线程 | 4 | ✅ 通过 | wall_ms=184.5；p50_ms=45.1 |
| E10 | 审计完整性 | 6 | ✅ 通过 | total_events=149；search_events=80 |
| E11 | 熔断冷却 → 半开试探 → 恢复（closed） | 5 | ✅ 通过 | cooldown_s=0.4；opened_state=open |
| E12 | 半开试探失败 → 立即重开（不计入成功） | 4 | ✅ 通过 | half_open_latency_ms=87.1；failures_after=4 |
| E13 | 真实 HTTP 适配器联调（本地假平台 + 故障回落） | 9 | ✅ 通过 | adapter=http_json；mock_base_url=http://127.0.0.1:53799 |
| E14 | 平台注册表热加载（保留熔断运行时状态） | 4 | ✅ 通过 | platforms=10；breaker_before=open |
| E15 | 半开并发冲击：只放行一个试探 | 5 | ✅ 通过 | cooldown_s=0.4；concurrent_requests=8 |
| E16 | 恢复后并发放行（closed 状态） | 4 | ✅ 通过 | concurrent_requests=6；admitted=6 |

## E1 · 冷启动、语料与配置

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 健康接口 200 | `200` | `200` | ✅ |
| 服务状态 ok | `ok` | `ok` | ✅ |
| 作品数 20 | `20` | `20` | ✅ |
| 小说 12 部 | `12` | `12` | ✅ |
| 短剧 8 部 | `8` | `8` | ✅ |
| 句子/台词 43 条 | `43` | `43` | ✅ |
| 其中短剧台词 16 条 | `16` | `16` | ✅ |
| 平台注册 10 个 | `10` | `10` | ✅ |
| 统计接口可用 | `200` | `200` | ✅ |
| 配置预算 1500ms | `1500` | `1500` | ✅ |

**指标 / 观测**：

```json
{
  "corpus": {
    "works": 20,
    "novels": 12,
    "dramas": 8,
    "sentences": 43,
    "lines": 16,
    "characters": 31,
    "themes": 24,
    "platforms": 9
  },
  "platforms_healthy": 8
}
```

## E2 · 意图识别与分层排序矩阵

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 她说她从来没有恨过我，只是不想再见到我 → 首条作品 | `长夜将明` | `长夜将明` | ✅ |
| 她说她从来没有恨过我，只是不想再见到我 → 层级 | `0` | `0` | ✅ |
| 她说她从来没有恨过我，只是不想再见到我 → 来源标签 | `原文句子命中` | `原文句子命中` | ✅ |
| 林昭 → 首条作品 | `长夜将明` | `长夜将明` | ✅ |
| 林昭 → 层级 | `2` | `2` | ✅ |
| 林昭 → 来源标签 | `主角/角色命中` | `主角/角色命中` | ✅ |
| 侯府嫡女 → 首条作品 | `侯府嫡女不好惹` | `侯府嫡女不好惹` | ✅ |
| 侯府嫡女 → 层级 | `0` | `0` | ✅ |
| 侯府嫡女 → 来源标签 | `标题精确命中` | `标题精确命中` | ✅ |
| cyj → 首条作品 | `长夜将明` | `长夜将明` | ✅ |
| cyj → 层级 | `0` | `0` | ✅ |
| cyj → 来源标签 | `标题精确命中` | `标题精确命中` | ✅ |
| 闪婚 → 首条作品 | `闪婚后，傅先生每天都在吃醋` | `闪婚后，傅先生每天都在吃醋` | ✅ |
| 闪婚 → 层级 | `1` | `1` | ✅ |
| 闪婚 → 来源标签 | `剧名近似` | `剧名近似` | ✅ |
| 念念，你跑什么？我又不会吃了你 → 首条作品 | `闪婚后，傅先生每天都在吃醋` | `闪婚后，傅先生每天都在吃醋` | ✅ |
| 念念，你跑什么？我又不会吃了你 → 层级 | `0` | `0` | ✅ |
| 念念，你跑什么？我又不会吃了你 → 来源标签 | `台词精确命中` | `台词精确命中` | ✅ |
| 傅宴 → 首条作品 | `闪婚后，傅先生每天都在吃醋` | `闪婚后，傅先生每天都在吃醋` | ✅ |
| 傅宴 → 层级 | `6` | `6` | ✅ |
| 傅宴 → 来源标签 | `角色/演员命中` | `角色/演员命中` | ✅ |
| 主题查询结果数 ≥3 | `True` | `True` | ✅ |
| 主题查询全部 layer=3 | `True` | `True` | ✅ |
| 主题查询触发备用源 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "cases": [
    {
      "query": "她说她从来没有恨过我，只是不想再见到我",
      "tab": "novel",
      "title": "长夜将明",
      "layer": 0,
      "label": "原文句子命中",
      "latency_ms": 8.8,
      "verified": true
    },
    {
      "query": "林昭",
      "tab": "novel",
      "title": "长夜将明",
      "layer": 2,
      "label": "主角/角色命中",
      "latency_ms": 7.0,
      "verified": true
    },
    {
      "query": "侯府嫡女",
      "tab": "novel",
      "title": "侯府嫡女不好惹",
      "layer": 0,
      "label": "标题精确命中",
      "latency_ms": 13.9,
      "verified": true
    },
    {
      "query": "cyj",
      "tab": "novel",
      "title": "长夜将明",
      "layer": 0,
      "label": "标题精确命中",
      "latency_ms": 13.2,
      "verified": true
    },
    {
      "query": "闪婚",
      "tab": "drama",
      "title": "闪婚后，傅先生每天都在吃醋",
      "layer": 1,
      "label": "剧名近似",
      "latency_ms": 12.5,
      "verified": true
    },
    {
      "query": "念念，你跑什么？我又不会吃了你",
      "tab": "drama",
      "title": "闪婚后，傅先生每天都在吃醋",
      "layer": 0,
      "label": "台词精确命中",
      "latency_ms": 10.6,
      "verified": true
    },
    {
      "query": "傅宴",
      "tab": "drama",
      "title": "闪婚后，傅先生每天都在吃醋",
      "layer": 6,
      "label": "角色/演员命中",
      "latency_ms": 12.4,
      "verified": true
    },
    {
      "query": "重生 复仇",
      "tab": "novel",
      "titles": [
        "长夜将明",
        "退婚三次后我成了首富",
        "侯府嫡女不好惹"
      ],
      "expanded": true,
      "fallback_stage": 2,
      "latency_ms": 22.5
    }
  ],
  "latency_p50_ms": 12.4,
  "latency_max_ms": 22.5
}
```

## E3 · 主源（番茄）不可用 → 自动降级到七猫

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 仍命中长夜将明 | `True` | `True` | ✅ |
| 来源切到七猫 | `qimao` | `qimao` | ✅ |
| 标记降级 | `True` | `True` | ✅ |
| 标记拓展 | `True` | `True` | ✅ |
| 降级原因含 fanqie | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "latency_ms": 14.4,
  "fallback_stage": 2,
  "degraded_reasons": [
    "fanqie 不可用（演示中平台被停用）"
  ],
  "platforms": [
    {
      "id": "agg_novel",
      "state": "disabled",
      "used": false
    },
    {
      "id": "fanqie",
      "state": "unavailable",
      "used": false
    },
    {
      "id": "qimao",
      "state": "healthy",
      "used": true
    }
  ]
}
```

## E4 · 超时 → 熔断打开 → 跳过主源（预算保护）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 连续超时后熔断打开 | `open` | `open` | ✅ |
| 第 4 次查询不再耗时等主源 | `True` | `True` | ✅ |
| 熔断后仍返回结果 | `True` | `True` | ✅ |
| 熔断后查询标记降级 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "rounds": [
    {
      "state": "degraded",
      "breaker": "closed",
      "failures": 1,
      "elapsed_ms": 123.4,
      "degraded": true
    },
    {
      "state": "degraded",
      "breaker": "closed",
      "failures": 2,
      "elapsed_ms": 136.4,
      "degraded": true
    },
    {
      "state": "circuit_open",
      "breaker": "open",
      "failures": 3,
      "elapsed_ms": 139.3,
      "degraded": true
    },
    {
      "state": "circuit_open",
      "breaker": "open",
      "failures": 3,
      "elapsed_ms": 10.4,
      "degraded": true
    }
  ],
  "threshold_expected": 3,
  "final_latency_ms": 10.4
}
```

## E5 · 小说主源 + 第一备用全部不可用

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 无结果 | `0` | `0` | ✅ |
| 标记降级 | `True` | `True` | ✅ |
| 返回原因 | `True` | `True` | ✅ |
| 接口仍 200 正常返回 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "latency_ms": 3.2,
  "degraded_reasons": [
    "fanqie 不可用（演示中平台被停用）",
    "qimao 不可用（演示中平台被停用）",
    "结果较少（0 < 3），已尝试全部可用平台",
    "主源结果不足，已按降级链拓展到第 4 级备用源"
  ]
}
```

## E6 · 慢平台 + 150ms 预算：超时必有结果

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 在预算附近返回（<500ms） | `True` | `True` | ✅ |
| 标记降级 | `True` | `True` | ✅ |
| 响应含预算/超时说明 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "elapsed_ms": 185.1,
  "budget_ms": 150,
  "degraded_reasons": [
    "fanqie 不可用（演示中平台被停用）",
    "qimao 不可用（timeout: 超过单级预算）",
    "预算耗尽，返回已召回的部分结果",
    "结果较少（0 < 3），已尝试全部可用平台",
    "主源结果不足，已按降级链拓展到第 2 级备用源"
  ]
}
```

## E7 · 安全护栏：违禁、注入、鉴权、PII、限流

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 违禁主题拒绝 | `False` | `False` | ✅ |
| 拒绝码 blocked_topic | `blocked_topic` | `blocked_topic` | ✅ |
| 无凭据改平台 → 401 | `401` | `401` | ✅ |
| 无 confirm → 428 | `428` | `428` | ✅ |
| 428 错误码 need_confirmation | `need_confirmation` | `need_confirmation` | ✅ |
| 注入只标记不执行 | `True` | `True` | ✅ |
| 注入查询仍可检索 | `True` | `True` | ✅ |
| 审计中 PII 已脱敏 | `True` | `True` | ✅ |
| 审计不含明文手机号 | `False` | `False` | ✅ |
| 限流前两次放行 | `[True, True]` | `[True, True]` | ✅ |
| 第三次限流 | `rate_limited` | `rate_limited` | ✅ |

**指标 / 观测**：

```json
{
  "blocked_message": "该查询涉及毒品制作或交易，无法提供检索。",
  "audit_events": 56,
  "last_audit": {
    "query": "[手机号] 林昭",
    "allowed": true,
    "results": 1,
    "elapsed_ms": 2
  }
}
```

## E8 · 长记忆：点击 → 同层排序翻转（不跨层）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 点击前首条为退婚三次 | `退婚三次后我成了首富` | `退婚三次后我成了首富` | ✅ |
| 点击后首条为侯府嫡女 | `侯府嫡女不好惹` | `侯府嫡女不好惹` | ✅ |
| 同层内重排（layer=3） | `3` | `3` | ✅ |
| 偏好主题含重生 | `True` | `True` | ✅ |
| 点击作品已记录 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "baseline_order": [
    "退婚三次后我成了首富",
    "侯府嫡女不好惹"
  ],
  "after_order": [
    "侯府嫡女不好惹",
    "退婚三次后我成了首富"
  ],
  "preferred_themes": [
    "重生",
    "古装",
    "宅斗"
  ]
}
```

## E9 · 并发压测：30 请求 / 10 线程

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 30 次并发全部成功 | `30` | `30` | ✅ |
| 每个请求都有结果或合法空结果 | `True` | `True` | ✅ |
| p95 < 500ms | `True` | `True` | ✅ |
| 服务未崩溃（健康检查 200） | `200` | `200` | ✅ |

**指标 / 观测**：

```json
{
  "wall_ms": 184.5,
  "p50_ms": 45.1,
  "p95_ms": 93.5,
  "max_ms": 117.6,
  "throughput_rps": 162.6
}
```

## E10 · 审计完整性

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 搜索请求数 | `80` | `80` | ✅ |
| 审计搜索事件数与请求数一致 | `80` | `80` | ✅ |
| 每条搜索都有 trace_id | `True` | `True` | ✅ |
| 含耗时字段 | `True` | `True` | ✅ |
| 审计接口可用 | `5` | `5` | ✅ |
| 无明文手机号 | `False` | `False` | ✅ |

**指标 / 观测**：

```json
{
  "total_events": 149,
  "search_events": 80,
  "expected_searches": 80,
  "flush_wait_ms": 0.0,
  "degraded_events": 28,
  "last": {
    "trace_id": "0c400c5d3ecd",
    "query": "林昭",
    "results": 1,
    "elapsed_ms": 9
  }
}
```

## E11 · 熔断冷却 → 半开试探 → 恢复（closed）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 3 次超时后熔断打开 | `open` | `open` | ✅ |
| 冷却后半开试探成功 | `closed` | `closed` | ✅ |
| 成功后半开计数清零 | `0` | `0` | ✅ |
| 恢复后主源重新参与 | `fanqie` | `fanqie` | ✅ |
| 恢复后不再标记降级 | `False` | `False` | ✅ |

**指标 / 观测**：

```json
{
  "cooldown_s": 0.4,
  "opened_state": "open",
  "recovered_state": "closed",
  "recovery_latency_ms": 19.7,
  "platform": {
    "state": "healthy",
    "failures": 0
  }
}
```

## E12 · 半开试探失败 → 立即重开（不计入成功）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 再次打开熔断 | `open` | `open` | ✅ |
| 半开试探失败后立即重开 | `open` | `open` | ✅ |
| 失败计数累计到 4 | `4` | `4` | ✅ |
| 试探失败仍正常响应 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "half_open_latency_ms": 87.1,
  "failures_after": 4,
  "platform_state": "circuit_open"
}
```

## E13 · 真实 HTTP 适配器联调（本地假平台 + 故障回落）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 真实 HTTP 召回成功 | `长夜将明（镜像）` | `长夜将明（镜像）` | ✅ |
| 来源标记为接入平台 | `fanqie` | `fanqie` | ✅ |
| 通道映射生效 | `True` | `True` | ✅ |
| 中文查询正确编码 | `True` | `True` | ✅ |
| 正常路径不降级 | `False` | `False` | ✅ |
| HTTP 500 触发熔断 | `circuit_open` | `circuit_open` | ✅ |
| 故障时回落到七猫 | `qimao` | `qimao` | ✅ |
| 故障时返回本地作品 | `长夜将明` | `长夜将明` | ✅ |
| 故障时标记降级 | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "adapter": "http_json",
  "mock_base_url": "http://127.0.0.1:53799",
  "first_call_latency_ms": 15.7,
  "rounds": [
    {
      "state": "degraded",
      "breaker": "closed",
      "failures": 1
    },
    {
      "state": "degraded",
      "breaker": "closed",
      "failures": 2
    },
    {
      "state": "circuit_open",
      "breaker": "open",
      "failures": 3
    },
    {
      "state": "circuit_open",
      "breaker": "open",
      "failures": 3
    }
  ],
  "fallback_platform": "qimao"
}
```

## E14 · 平台注册表热加载（保留熔断运行时状态）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 热加载返回 200 | `200` | `200` | ✅ |
| 平台数量不变 | `10` | `10` | ✅ |
| 熔断状态被保留 | `open` | `open` | ✅ |
| 失败计数被保留 | `4` | `4` | ✅ |

**指标 / 观测**：

```json
{
  "platforms": 10,
  "breaker_before": "open",
  "breaker_after": "open",
  "failures": 4
}
```

## E15 · 半开并发冲击：只放行一个试探

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 8 个并发只有 1 个进入半开试探 | `1` | `1` | ✅ |
| 其余 7 个请求跳过主源 | `7` | `7` | ✅ |
| 跳过原因标注半开试探 | `True` | `True` | ✅ |
| 试探成功后熔断关闭 | `closed` | `closed` | ✅ |
| 主源实际只被调用 1 次 | `1` | `1` | ✅ |

**指标 / 观测**：

```json
{
  "cooldown_s": 0.4,
  "concurrent_requests": 8,
  "admitted": 1,
  "fanqie_calls_delta": 1,
  "opened_state": "open",
  "final_state": "closed",
  "skip_reasons": [
    "半开试探进行中，跳过"
  ]
}
```

## E16 · 恢复后并发放行（closed 状态）

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 恢复后 6 个并发全部放行 | `6` | `6` | ✅ |
| 全部正常响应 | `6` | `6` | ✅ |
| 状态保持 closed | `closed` | `closed` | ✅ |
| p95 < 300ms | `True` | `True` | ✅ |

**指标 / 观测**：

```json
{
  "concurrent_requests": 6,
  "admitted": 6,
  "p50_ms": 19.0,
  "p95_ms": 22.5,
  "final_state": "closed"
}
```

## 复现方式

```bash
cd novel-drama-agent
python -X utf8 scripts/experiment.py
```

脚本会在进程内启动真实 HTTP 服务（随机端口），使用独立的 `docs/experiment/` 记忆库与审计文件，
不会污染 `data/memory.db`；结束后自动关闭服务并写出本报告。
