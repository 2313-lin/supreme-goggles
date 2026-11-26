# Redis 多实例缓存验证报告

- 目标 Redis：`redis://127.0.0.1:6399/0`（真实 redis-server，非 mock）
- 结果：**9/9 项检查通过**
- 冷/热耗时：316.9ms → 3.3ms
- 上游 mock 平台总调用次数：1（两实例共享后应保持 1）
- 原始数据：[results.json](redischeck/results.json)

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 两实例后端均为 redis | `redis` | `redis` | ✅ |
| 实例 B 后端为 redis | `redis` | `redis` | ✅ |
| 实例 A 冷查询命中 mock | `2` | `2` | ✅ |
| mock 被调用 1 次 | `1` | `1` | ✅ |
| 实例 B 结果一致 | `2` | `2` | ✅ |
| 实例 B 未再调用 mock（命中共享缓存） | `1` | `1` | ✅ |
| 实例 B 显著更快 | `True` | `True` | ✅ |
| 实例 B 缓存命中 ≥1 | `True` | `True` | ✅ |
| Redis 中存在 1 个共享键 | `1` | `1` | ✅ |

复现：
```bash
redis-server --port 6399 --bind 127.0.0.1
cd novel-drama-agent && python -X utf8 scripts/redis_check.py
```
