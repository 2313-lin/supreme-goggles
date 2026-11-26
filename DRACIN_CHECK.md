# DracinAPI 联调报告

- 时间：2026-11-26T00:10:37+08:00（Asia/Shanghai）
- 模式：**本地 mock（与 DracinAPI 相同的 Bearer 鉴权 + 字段契约）；真实 key 就绪后同脚本一键切换**
- 结果：**8/8 项检查通过**
- 原始数据：[results.json](dracin/results.json)

## 检查项

| 检查 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- |
| 端到端命中 dracin | `True` | `True` | ✅ |
| 映射出集数 | `True` | `True` | ✅ |
| 通道为剧名命中 | `True` | `True` | ✅ |
| 首次耗时在预算内 | `True` | `True` | ✅ |
| 缓存后端可用 | `True` | `True` | ✅ |
| 二次命中更快 | `True` | `True` | ✅ |
| 鉴权失败触发熔断 | `circuit_open` | `circuit_open` | ✅ |
| mock 收到 Bearer 头 | `Bearer test-key` | `Bearer test-key` | ✅ |

## 复现

```bash
cd novel-drama-agent
python -X utf8 scripts/make_dracin_config.py
# 真实：把 dracinapi.com/register 注册得到的 key 注入后重跑本脚本
NDS_API_KEY_DRACIN=你的key python -X utf8 scripts/live_check_dracin.py
# 无 key 的 mock 模式：
python -X utf8 scripts/live_check_dracin.py
```

鉴权：`Authorization: Bearer <key>`；搜索：`/{provider}/api/v1/search?keyword=<kw>`；
只取 L1 元数据（剧名/ID/封面/集数/简介/标签），不请求 `play` 播放接口。
