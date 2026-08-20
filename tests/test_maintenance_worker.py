from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hermes_trader.api import routes
from hermes_trader import worker as worker_mod
from hermes_trader.maintenance_queue import MaintenanceQueue


def test_queue_is_single_active_and_reclaims_expired_lease(tmp_path) -> None:
    queue = MaintenanceQueue(tmp_path)
    first, created = queue.enqueue(
        worker_mod.TECHNICAL_JOB,
        {"workers": 2},
        idempotency_key="manual-1",
    )
    duplicate, duplicate_created = queue.enqueue(
        worker_mod.TECHNICAL_JOB,
        {"workers": 4},
        idempotency_key="manual-2",
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate["job_id"] == first["job_id"]

    claimed = queue.claim_next("worker-a", kinds=[worker_mod.TECHNICAL_JOB])
    assert claimed is not None
    assert claimed["status"] == "running"
    queue.heartbeat(
        claimed["job_id"], "worker-a", progress={"processed": 40, "cursor": "600040"},
    )
    with queue._connect() as db:
        db.execute(
            "UPDATE maintenance_jobs SET lease_expires_epoch=0 WHERE job_id=?",
            (claimed["job_id"],),
        )

    resumed = queue.claim_next("worker-b", kinds=[worker_mod.TECHNICAL_JOB])
    assert resumed is not None
    assert resumed["job_id"] == claimed["job_id"]
    assert resumed["attempts"] == 2
    assert resumed["progress"] == {"cursor": "600040", "processed": 40}


def test_worker_passes_durable_progress_back_to_resumed_refresh(tmp_path, monkeypatch) -> None:
    config = SimpleNamespace(
        data_dir=tmp_path,
        section=lambda name: {"poll_seconds": 1, "lease_seconds": 60}
        if name == "background_worker" else {},
    )
    worker = worker_mod.MaintenanceWorker(config)
    queued, _created = worker.queue.enqueue(
        worker_mod.TECHNICAL_JOB,
        {"workers": 2, "max_symbols": 50},
    )
    claimed = worker.queue.claim_next(
        worker.worker_id, kinds=[worker_mod.TECHNICAL_JOB], lease_seconds=60,
    )
    assert claimed is not None
    worker.queue.heartbeat(
        claimed["job_id"], worker.worker_id,
        progress={"processed": 20, "succeeded": 19, "failed": 1, "cursor": "600020"},
    )
    claimed = worker.queue.get(queued["job_id"])
    seen = {}

    def fake_refresh_all(**kwargs):
        seen.update(kwargs)
        kwargs["progress_callback"]({
            "processed": 50,
            "succeeded": 49,
            "failed": 1,
            "cursor": "600050",
        })
        return {
            "ok": True,
            "state": "ready",
            "processed": 50,
            "succeeded": 49,
            "failed": 1,
        }

    monkeypatch.setattr(worker_mod.technical_screener, "refresh_all", fake_refresh_all)

    asyncio.run(worker.run_job(claimed))

    finished = worker.queue.get(queued["job_id"])
    assert seen["resume"]["cursor"] == "600020"
    assert seen["max_symbols"] == 50
    assert finished["status"] == "completed"
    assert finished["progress"]["cursor"] == "600050"
    assert finished["result"]["state"] == "ready"


def test_only_one_worker_lease_is_active_per_data_dir(tmp_path) -> None:
    queue = MaintenanceQueue(tmp_path)
    assert queue.acquire_worker_lease("worker-a", lease_seconds=60) is True
    assert queue.acquire_worker_lease("worker-b", lease_seconds=60) is False
    queue.release_worker_lease("worker-a")
    assert queue.acquire_worker_lease("worker-b", lease_seconds=60) is True


def test_technical_refresh_api_only_enqueues_worker_job(tmp_path, monkeypatch) -> None:
    queue = MaintenanceQueue(tmp_path)

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(
            maintenance_queue=queue,
            config=SimpleNamespace(section=lambda _name: {"workers": 2}),
        ))

        @staticmethod
        async def json():
            return {"max_symbols": 80}

    monkeypatch.setattr(
        routes.technical_screener_mod,
        "refresh_all",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("API must not run refresh")),
    )

    response = asyncio.run(routes.technical_screen_refresh(Request()))
    payload = json.loads(response.body)

    assert response.status_code == 202
    assert payload["state"] == "queued"
    queued = queue.get(payload["job_id"])
    assert queued["payload"] == {"max_symbols": 80, "workers": 2}


def test_technical_refresh_rejects_when_worker_is_disabled(tmp_path) -> None:
    queue = MaintenanceQueue(tmp_path)

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(
            maintenance_queue=queue,
            config=SimpleNamespace(section=lambda name: (
                {"enabled": False} if name == "background_worker" else {"workers": 2}
            )),
        ))

        @staticmethod
        async def json():
            return {}

    response = asyncio.run(routes.technical_screen_refresh(Request()))
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["error_code"] == "maintenance_worker_disabled"
    assert queue.latest(worker_mod.TECHNICAL_JOB) is None
