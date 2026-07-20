from __future__ import annotations

from hermes_trader.jobs.scheduler import _archive_output_artifact, _report_output_artifact


def test_report_task_output_is_traceable_without_exposing_content_or_paths() -> None:
    artifact = _report_output_artifact({
        "rtype": "close",
        "date": "2026-07-13",
        "content_id": "report:close:2026-07-13",
        "content_hash": "a" * 64,
        "report_version": 1,
        "content_md": "sensitive body",
        "input_snapshot_file": "reports/2026-07-13/.inputs/close.json",
    })

    assert artifact == {
        "kind": "report",
        "content_id": "report:close:2026-07-13",
        "report_type": "close",
        "trade_day": "2026-07-13",
        "version": 1,
        "content_hash": "a" * 64,
    }
    assert "content_md" not in artifact
    assert "input_snapshot_file" not in artifact


def test_archive_task_output_lists_only_archive_identity_and_report_types() -> None:
    artifact = _archive_output_artifact({
        "date": "2026-07-13",
        "reports": {
            "close": {"content_md": "close body"},
            "evening": {"content_md": "evening body"},
        },
        "holdings": [{"code": "000001", "shares": 1000}],
    })

    assert artifact == {
        "kind": "archive",
        "content_id": "archive:2026-07-13",
        "trade_day": "2026-07-13",
        "report_types": ["close", "evening"],
    }
