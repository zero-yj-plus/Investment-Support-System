from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_trader.research_runs import (
    ActiveResearchRunDeleteError,
    ActiveResearchRunError,
    ResearchRunManager,
)
from hermes_trader.api import routes


ROOT = Path(__file__).resolve().parents[1]
CHAIN_REPORT = "# 完整研究\n\n" + "\n".join(
    f"### {index}. 第{index}章\n完整分析" for index in range(1, 9)
)
COMPANY_REPORT = "# 单公司研究\n\n" + "\n".join(
    f"## {numeral}、研究章节\n完整分析" for numeral in "一二三四五六七八九"
)


async def _completed_events():
    yield {"stage": "模型生成中"}
    yield {"delta": CHAIN_REPORT + "\n\n结构化附录"}
    yield {"done": True, "truncated": False}


async def _company_events():
    yield {"stage": "模型生成中"}
    yield {"delta": COMPANY_REPORT + "\n\n结构化附录"}
    yield {"done": True, "truncated": False}


@pytest.mark.asyncio
async def test_background_run_completes_and_survives_manager_restart(tmp_path) -> None:
    manager = ResearchRunManager(tmp_path)
    row, reused = await manager.start(
        kind="chain",
        subject="电力",
        focus="利润池",
        generator_factory=_completed_events,
    )
    await manager._tasks[row["run_id"]]

    completed = manager.get(row["run_id"])
    latest = manager.latest("chain")
    restarted = ResearchRunManager(tmp_path)

    assert reused is False
    assert completed["status"] == "completed"
    assert completed["content"].startswith("# 完整研究")
    assert latest["latest_completed"]["run_id"] == row["run_id"]
    assert restarted.latest("chain")["latest_completed"]["content"].endswith("结构化附录")


@pytest.mark.asyncio
async def test_page_disconnect_does_not_cancel_background_run(tmp_path) -> None:
    release = asyncio.Event()

    async def delayed_events():
        yield {"stage": "后台运行中"}
        await release.wait()
        yield {"delta": COMPANY_REPORT}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="company",
        subject="000539",
        generator_factory=delayed_events,
    )
    await asyncio.sleep(0)

    assert manager.get(row["run_id"])["status"] == "running"
    release.set()
    await manager._tasks[row["run_id"]]
    assert manager.get(row["run_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_run_does_not_replace_last_completed_result(tmp_path) -> None:
    manager = ResearchRunManager(tmp_path)
    first, _ = await manager.start(
        kind="chain",
        subject="电力",
        generator_factory=_completed_events,
    )
    await manager._tasks[first["run_id"]]

    async def failed_events():
        yield {"error": "上游失败"}

    failed, _ = await manager.start(
        kind="chain",
        subject="机器人",
        generator_factory=failed_events,
    )
    await manager._tasks[failed["run_id"]]
    latest = manager.latest("chain")

    assert latest["latest"]["status"] == "failed"
    assert latest["latest_completed"]["run_id"] == first["run_id"]
    assert latest["latest_completed"]["content"].startswith("# 完整研究")


@pytest.mark.asyncio
async def test_same_request_reuses_active_run_and_other_subject_conflicts(tmp_path) -> None:
    release = asyncio.Event()

    async def delayed_events():
        await release.wait()
        yield {"delta": COMPANY_REPORT}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    first, _ = await manager.start(
        kind="company", subject="000539", generator_factory=delayed_events,
    )
    reused, was_reused = await manager.start(
        kind="company", subject="000539", generator_factory=delayed_events,
    )

    assert was_reused is True
    assert reused["run_id"] == first["run_id"]
    with pytest.raises(ActiveResearchRunError):
        await manager.start(
            kind="company", subject="000155", generator_factory=delayed_events,
        )
    release.set()
    await manager._tasks[first["run_id"]]


@pytest.mark.asyncio
async def test_cancel_marks_run_without_removing_previous_success(tmp_path) -> None:
    release = asyncio.Event()

    async def delayed_events():
        await release.wait()
        yield {"delta": "不应到达"}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="chain", subject="储能", generator_factory=delayed_events,
    )
    await asyncio.sleep(0)
    canceled = await manager.cancel(row["run_id"])

    assert canceled["status"] == "canceled"
    assert canceled["error_code"] == "user_canceled"


@pytest.mark.asyncio
async def test_delete_completed_run_removes_file_and_falls_back_to_previous_success(tmp_path) -> None:
    manager = ResearchRunManager(tmp_path)
    first, _ = await manager.start(
        kind="chain", subject="电力", generator_factory=_completed_events,
    )
    await manager._tasks[first["run_id"]]
    second, _ = await manager.start(
        kind="chain", subject="储能", generator_factory=_completed_events,
    )
    await manager._tasks[second["run_id"]]

    deleted = manager.delete(second["run_id"])

    assert deleted["run_id"] == second["run_id"]
    assert manager.get(second["run_id"]) is None
    assert manager.latest("chain")["latest_completed"]["run_id"] == first["run_id"]


@pytest.mark.asyncio
async def test_delete_active_run_requires_explicit_cancel_first(tmp_path) -> None:
    release = asyncio.Event()

    async def delayed_events():
        await release.wait()
        yield {"delta": COMPANY_REPORT}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="company", subject="000539", generator_factory=delayed_events,
    )
    await asyncio.sleep(0)

    with pytest.raises(ActiveResearchRunDeleteError):
        manager.delete(row["run_id"])

    await manager.cancel(row["run_id"])


@pytest.mark.asyncio
async def test_missing_required_company_section_is_not_accepted_as_completed(tmp_path) -> None:
    async def incomplete_company():
        yield {"delta": COMPANY_REPORT.replace("## 八、研究章节\n完整分析\n", "")}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="company", subject="000539", generator_factory=incomplete_company,
    )
    await manager._tasks[row["run_id"]]
    failed = manager.get(row["run_id"])

    assert failed["status"] == "failed"
    assert failed["error_code"] == "required_sections_missing"
    assert "八" in failed["error"]


def test_background_research_api_and_app_state_are_wired() -> None:
    routes = (ROOT / "hermes_trader/api/routes.py").read_text(encoding="utf-8")
    main = (ROOT / "hermes_trader/main.py").read_text(encoding="utf-8")

    for contract in (
        '@router.post("/research/runs")',
        '@router.get("/research/runs/latest")',
        '@router.get("/research/runs/{run_id}")',
        '@router.post("/research/runs/{run_id}/cancel")',
        '@router.delete("/research/runs/{run_id}")',
    ):
        assert contract in routes
    assert "ResearchRunManager(config.data_dir)" in main
    assert "app.state.research_runs = research_runs" in main
    assert "await research_runs.shutdown()" in main


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_kind", "expected_subject"),
    [
        ({"kind": "chain", "industry": "电力", "focus": "利润池"}, "chain", "电力"),
        ({"kind": "company", "code": "000539"}, "company", "000539"),
    ],
)
async def test_create_research_run_api_returns_before_background_completion(
    tmp_path, monkeypatch, payload, expected_kind, expected_subject,
) -> None:
    release = asyncio.Event()

    async def model_available(_engine):
        return "available"

    async def governed_chain(*_args, **_kwargs):
        await release.wait()
        yield CHAIN_REPORT

    class FakeEngine:
        async def chat_stream(self, *_args, **_kwargs):
            await release.wait()
            yield {"delta": COMPANY_REPORT + "\n\n结构化附录"}
            yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    state = SimpleNamespace(engine=FakeEngine(), research_runs=manager)

    class FakeRequest:
        app = SimpleNamespace(state=state)

        async def json(self):
            return payload

    monkeypatch.setattr(routes.diagnosis_mod, "_model_connection_state", model_available)
    monkeypatch.setattr(routes, "_governed_chain_chat_stream", governed_chain)

    response = await routes.create_research_run(FakeRequest())
    body = json.loads(response.body)

    assert response.status_code == 202
    assert body["run"]["status"] == "queued"
    assert body["run"]["kind"] == expected_kind
    assert body["run"]["subject"] == expected_subject
    assert manager.get(body["run"]["run_id"])["status"] in {"queued", "running"}

    release.set()
    await manager._tasks[body["run"]["run_id"]]
    assert manager.get(body["run"]["run_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_delete_research_run_api_removes_completed_record(tmp_path) -> None:
    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="company", subject="000539", generator_factory=lambda: _company_events(),
    )
    await manager._tasks[row["run_id"]]

    class FakeRequest:
        app = SimpleNamespace(state=SimpleNamespace(research_runs=manager))

    response = await routes.delete_research_run(row["run_id"], FakeRequest())

    assert response["ok"] is True
    assert response["deleted_run_id"] == row["run_id"]
    assert response["latest_completed"] is None
    assert manager.get(row["run_id"]) is None


@pytest.mark.asyncio
async def test_delete_research_run_api_rejects_active_record(tmp_path) -> None:
    release = asyncio.Event()

    async def delayed_events():
        await release.wait()
        yield {"delta": COMPANY_REPORT}
        yield {"done": True, "truncated": False}

    manager = ResearchRunManager(tmp_path)
    row, _ = await manager.start(
        kind="company", subject="000539", generator_factory=delayed_events,
    )
    await asyncio.sleep(0)

    class FakeRequest:
        app = SimpleNamespace(state=SimpleNamespace(research_runs=manager))

    response = await routes.delete_research_run(row["run_id"], FakeRequest())
    body = json.loads(response.body)

    assert response.status_code == 409
    assert body["active"]["run_id"] == row["run_id"]
    assert manager.get(row["run_id"])["status"] == "running"
    await manager.cancel(row["run_id"])
