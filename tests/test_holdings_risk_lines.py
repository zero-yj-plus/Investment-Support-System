from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hermes_trader.knowledge.store import Store


class HoldingsRiskLinesTest(unittest.TestCase):
    def test_risk_lines_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_holdings([
                {
                    "code": "000001",
                    "name": "平安银行",
                    "cost": 10,
                    "shares": 100,
                    "stop_loss_price": 9.2,
                    "take_profit_price": 12.5,
                    "horizon": "swing",
                    "entry_type": "breakout",
                    "entry_date": "2026-08-01",
                }
            ])

            row = store.holdings()[0]
            self.assertEqual(row["stop_loss_price"], 9.2)
            self.assertEqual(row["take_profit_price"], 12.5)
            self.assertEqual(row["horizon"], "swing")
            self.assertEqual(row["entry_type"], "breakout")
            self.assertEqual(row["entry_date"], "2026-08-01")

    def test_risk_lines_are_optional(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_holdings([
                {"code": "000001", "cost": 10, "shares": 100}
            ])

            row = store.holdings()[0]
            self.assertIsNone(row["stop_loss_price"])
            self.assertIsNone(row["take_profit_price"])
            self.assertEqual(row["horizon"], "general")
            self.assertEqual(row["entry_type"], "general")
            self.assertIsNone(row["entry_date"])

    def test_stop_must_be_below_target(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            with self.assertRaisesRegex(ValueError, "止损价必须低于止盈价"):
                store.save_holdings([
                    {
                        "code": "000001",
                        "cost": 10,
                        "shares": 100,
                        "stop_loss_price": 12,
                        "take_profit_price": 11,
                    }
                ])

    def test_risk_line_must_be_positive(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            with self.assertRaisesRegex(ValueError, "止损价必须大于 0"):
                store.save_holdings([
                    {
                        "code": "000001",
                        "cost": 10,
                        "shares": 100,
                        "stop_loss_price": -1,
                    }
                ])


if __name__ == "__main__":
    unittest.main()
