from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from hermes_trader.analysis.holding_risk import evaluate_holding, load_risk_states, save_risk_states


def rising_bars(count: int = 100) -> list[dict]:
    start = datetime(2026, 1, 1)
    rows = []
    price = 10.0
    for index in range(count):
        price += 0.05
        rows.append({
            "d": (start + timedelta(days=index)).date().isoformat(),
            "o": price - .03,
            "h": price + .12,
            "l": price - .12,
            "c": price,
            "v": 1000 + index * 3,
        })
    return rows


class HoldingRiskTest(unittest.TestCase):
    def test_manual_stop_has_highest_priority(self) -> None:
        rows = rising_bars()
        last = rows[-1]["c"]
        card = evaluate_holding(
            {"code": "600000", "cost": 12, "stop_loss_price": last + .1, "horizon": "short"},
            {"price": last, "change_pct": -1, "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        self.assertEqual(card["state"], "red")
        self.assertIn("客户预设风险线", card["summary"])

    def test_ma5_break_alone_never_forces_red(self) -> None:
        rows = rising_bars()
        rows[-1]["c"] -= .4
        rows[-1]["l"] = min(rows[-1]["l"], rows[-1]["c"] - .1)
        card = evaluate_holding(
            {"code": "600000", "cost": 11, "horizon": "swing"},
            {"price": rows[-1]["c"], "change_pct": -1, "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        self.assertNotEqual(card["state"], "red")

    def test_missing_history_is_gray(self) -> None:
        rows = rising_bars(20)
        card = evaluate_holding(
            {"code": "600000", "cost": 10},
            {"price": 11, "stale": False},
            rows,
            clock=datetime(2026, 2, 1, 16),
        )
        self.assertEqual(card["state"], "gray")
        self.assertIn("至少60根前复权日K", card["missing"])

    def test_shadow_card_never_enables_push(self) -> None:
        rows = rising_bars()
        card = evaluate_holding(
            {"code": "600000", "cost": 11, "horizon": "trend", "entry_date": rows[-40]["d"]},
            {"price": rows[-1]["c"], "change_pct": 1, "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        self.assertTrue(card["shadow_mode"])
        self.assertFalse(card["push_eligible"])

    def test_algorithm_line_never_moves_down(self) -> None:
        rows = rising_bars()
        holding = {
            "code": "600000", "cost": 11, "horizon": "swing",
            "entry_type": "general", "entry_date": rows[20]["d"],
        }
        first = evaluate_holding(
            holding,
            {"price": rows[-1]["c"], "change_pct": 1, "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        previous_line = first["levels"]["algorithm_stop"]
        runtime = first["runtime_state"]
        weakened = [dict(row) for row in rows]
        weakened[-1]["c"] -= 1.2
        weakened[-1]["l"] = weakened[-1]["c"] - .5
        weakened[-1]["h"] = weakened[-1]["c"] + .5
        second = evaluate_holding(
            holding,
            {"price": weakened[-1]["c"], "change_pct": -5, "stale": False},
            weakened,
            previous_runtime=runtime,
            clock=datetime(2026, 5, 2, 16),
        )
        self.assertGreaterEqual(second["levels"]["algorithm_stop"], previous_line)

    def test_entry_logic_changes_initial_invalidation_basis(self) -> None:
        rows = rising_bars()
        cost = rows[-1]["c"] + .2
        breakout = evaluate_holding(
            {"code": "600000", "cost": cost, "horizon": "short", "entry_type": "breakout"},
            {"price": rows[-1]["c"], "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        general = evaluate_holding(
            {"code": "600001", "cost": cost, "horizon": "short", "entry_type": "general"},
            {"price": rows[-1]["c"], "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        self.assertEqual(breakout["levels"]["initial_stop_basis"], "突破关键位下方")
        self.assertNotEqual(
            breakout["levels"]["initial_stop_basis"],
            general["levels"]["initial_stop_basis"],
        )

    def test_profit_action_is_not_labeled_as_risk_reduction(self) -> None:
        rows = rising_bars()
        card = evaluate_holding(
            {"code": "600000", "cost": 12, "take_profit_price": 14, "horizon": "short"},
            {"price": rows[-1]["c"], "stale": False},
            rows,
            clock=datetime(2026, 5, 1, 16),
        )
        self.assertEqual(card["signal_type"], "profit_take")
        self.assertEqual(card["action"], "止盈兑现1/3")

    def test_runtime_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = {"600000": {"algorithm_stop": 10.5, "fingerprint": "x"}}
            save_risk_states(Path(directory), state)
            self.assertEqual(load_risk_states(Path(directory)), state)


if __name__ == "__main__":
    unittest.main()
