"""发送一条固定的持仓仓位管理链路测试消息，不读取真实持仓或行情。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hermes_trader.analysis.holdings_risk import push_test_message
from hermes_trader.config import Config


async def _send(config_path: Path) -> bool:
    config = Config(config_path)
    if config.push_via == "gateway":
        from hermes_trader.notify import GatewaySender

        notifier = GatewaySender(config)
    else:
        from hermes_trader.feishu.push import FeishuPusher

        notifier = FeishuPusher(config)
    sender = getattr(notifier, "push_text", None)
    if not callable(sender):
        return False
    return bool(await sender(push_test_message()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    ok = asyncio.run(_send(Path(args.config).resolve()))
    print(json.dumps({
        "ok": ok,
        "kind": "holdings_risk_demo",
        "real_holdings_read": False,
    }, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
