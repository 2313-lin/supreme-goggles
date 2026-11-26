"""启动脚本：python run.py [serve|search|stats|platforms|simulate|demo]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nds.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
