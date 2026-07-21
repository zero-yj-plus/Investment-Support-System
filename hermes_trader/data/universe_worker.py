"""股票池阻塞源的可终止子进程 worker。"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "tdx_api":
        return 2
    # 延迟导入：全量 instruments_a 只在可硬终止的隔离进程里读取。
    from . import tdx_api_source  # noqa: PLC0415

    names = tdx_api_source.stock_universe() or {}
    sys.stdout.write(json.dumps(names, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
