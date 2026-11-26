"""从默认配置生成联调配置：启用真实短剧聚合源（小齐 API）。

用法：python -X utf8 scripts/make_live_config.py
产物：config/platforms.live.json（默认配置 + agg_drama 真实接入）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "config" / "platforms.json"
LIVE = ROOT / "config" / "platforms.live.json"

LIVE_AGG = {
    "id": "agg_drama",
    "type": "drama",
    "role": "aggregator",
    "stage": 4,
    "adapter": "icofun_shortdrama",
    "tier": "L1",
    "content_level": "L1",
    "timeout_ms": 4000,
    "priority": 60,
    "enabled": True,
    "base_url": "https://api.icofun.cn",
    "notes": "小齐API「短剧搜索与排名榜」：公开访问、无需密钥，仅取 L1 元数据",
}


def main() -> int:
    data = json.loads(DEFAULT.read_text(encoding="utf-8"))
    data["_comment"] = ("联调配置：默认演示源 + 真实短剧聚合源（小齐API，公开/无需密钥）。"
                        "启用方式：NDS_PLATFORMS=config/platforms.live.json")
    platforms = data["platforms"]
    for index, platform in enumerate(platforms):
        if platform["id"] == "agg_drama":
            platforms[index] = LIVE_AGG
            break
    else:
        platforms.append(LIVE_AGG)
    LIVE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {LIVE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
