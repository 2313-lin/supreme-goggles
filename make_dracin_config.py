"""生成 DracinAPI 联调配置：默认演示源 + dracin（需 NDS_API_KEY_DRACIN）。

用法：python -X utf8 scripts/make_dracin_config.py
产物：config/platforms.dracin.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "config" / "platforms.json"
OUT = ROOT / "config" / "platforms.dracin.json"

DRACIN = {
    "id": "dracin",
    "type": "drama",
    "role": "aggregator",
    "stage": 4,
    "adapter": "dracin",
    "tier": "L1",
    "content_level": "L1",
    "timeout_ms": 8000,
    "priority": 60,
    "enabled": True,
    "base_url": "https://dracinapi.com",
    "provider": "dramabox",
    "api_key": "",
    "api_key_header": "Authorization",
    "page_size": 20,
    "max_pages": 1,
    "notes": "DracinAPI（42 平台短剧聚合；需 NDS_API_KEY_DRACIN）",
}


def main() -> int:
    data = json.loads(DEFAULT.read_text(encoding="utf-8"))
    data["_comment"] = ("DracinAPI 联调配置：默认演示源 + dracin 聚合源。"
                        "启用：NDS_API_KEY_DRACIN=... NDS_PLATFORMS=config/platforms.dracin.json")
    platforms = data["platforms"]
    for index, platform in enumerate(platforms):
        if platform["id"] == "agg_drama":
            platforms[index] = DRACIN
            break
    else:
        platforms.append(DRACIN)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
