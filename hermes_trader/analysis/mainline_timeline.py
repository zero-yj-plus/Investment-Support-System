"""主线时间尺度与确认状态。

``mainline.py`` 只负责当前截面的确定性结构评分；本模块把同一结果放进
盘中 10 分钟档，区分「候选 / 形成中 / 已确认 / 收盘定稿」，并为跨日持续性
保留最小点时样本。它不改动四因子权重，也不在数据缺失时伪造「未形成」。
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import threading
from typing import Any


SCHEMA_VERSION = "mainline-timeline-v1"
BUCKET_MINUTES = 10
MAX_OBSERVATIONS = 600
_LOCK = threading.Lock()

_SESSION_LABELS = {
    "opening_discovery": "开盘观察",
    "forming": "盘中形成",
    "intraday_confirm": "盘中确认",
    "closing_confirm": "尾盘确认",
    "post_close": "收盘定稿",
    "recent_close": "最近交易日定稿",
}

_STATUS_LABELS = {
    "candidate": "候选",
    "forming": "形成中",
    "confirmed": "已确认",
    "final": "收盘定稿",
    "unformed": "未形成",
    "unavailable": "不可判定",
    "observed": "当日观察",
}


def _session_stage(observed_at: datetime, data_date: str) -> str:
    """返回 A 股日内判定阶段。

    早盘只给候选；10:00 后允许进入形成中；10:30 后只有连续档命中才可
    确认。15:00 后使用收盘完整截面定稿。
    """
    if observed_at.date().isoformat() != data_date:
        return "recent_close"
    current = observed_at.timetz().replace(tzinfo=None)
    if current < time(10, 0):
        return "opening_discovery"
    if current < time(10, 30):
        return "forming"
    if current < time(14, 30):
        return "intraday_confirm"
    if current < time(15, 0):
        return "closing_confirm"
    return "post_close"


def _bucket_at(observed_at: datetime) -> str:
    minute = observed_at.minute - (observed_at.minute % BUCKET_MINUTES)
    return observed_at.replace(minute=minute, second=0, microsecond=0).isoformat()


def _formed_direction_roles(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for role in response.get("roles") or []:
        if not isinstance(role, Mapping):
            continue
        if role.get("label") not in {"主线", "次主线"} or role.get("status") != "formed":
            continue
        name = str(role.get("name") or "").strip()
        if not name:
            continue
        rows.append({
            "label": str(role.get("label")),
            "name": name,
            "structure_score": role.get("structure_score"),
        })
    return rows


def _make_observation(response: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    directions = _formed_direction_roles(response)
    primary = next((row["name"] for row in directions if row["label"] == "主线"), None)
    return {
        "data_date": str(response.get("data_date") or ""),
        "bucket_at": _bucket_at(observed_at),
        "observed_at": observed_at.isoformat(),
        "data_state": str(response.get("data_state") or "missing"),
        "primary_name": primary,
        "directions": directions,
    }


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return []
    rows = payload.get("observations") if isinstance(payload, Mapping) else None
    return [dict(row) for row in (rows or []) if isinstance(row, Mapping)]


def _write(path: Path, observations: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"version": SCHEMA_VERSION, "observations": list(observations)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _upsert(observations: Sequence[Mapping[str, Any]], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(row) for row in observations
        if not (
            row.get("data_date") == current.get("data_date")
            and row.get("bucket_at") == current.get("bucket_at")
        )
    ]
    rows.append(dict(current))
    rows.sort(key=lambda row: str(row.get("observed_at") or ""))
    return rows[-MAX_OBSERVATIONS:]


def _direction_names(observation: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get("name")) for row in observation.get("directions") or []
        if isinstance(row, Mapping) and row.get("name")
    }


def _consecutive_hits(name: str, observations: Sequence[Mapping[str, Any]], data_date: str) -> int:
    hits = 0
    newer_bucket: datetime | None = None
    for row in reversed(observations):
        if row.get("data_date") != data_date:
            continue
        try:
            bucket = datetime.fromisoformat(str(row.get("bucket_at") or ""))
        except ValueError:
            break
        if newer_bucket is not None and newer_bucket - bucket > timedelta(minutes=BUCKET_MINUTES):
            break
        if name not in _direction_names(row):
            break
        hits += 1
        newer_bucket = bucket
    return hits


def _was_confirmed(
    name: str,
    observations: Sequence[Mapping[str, Any]],
    data_date: str,
) -> bool:
    """历史上是否已满足盘中两档确认，或在收盘完整截面定稿。"""
    streak = 0
    previous_bucket: datetime | None = None
    for row in observations:
        if row.get("data_date") != data_date:
            continue
        try:
            bucket = datetime.fromisoformat(str(row.get("bucket_at") or ""))
            observed_at = datetime.fromisoformat(str(row.get("observed_at") or ""))
        except ValueError:
            continue
        if name not in _direction_names(row):
            streak = 0
            previous_bucket = bucket
            continue
        if previous_bucket is not None and bucket - previous_bucket <= timedelta(minutes=BUCKET_MINUTES):
            streak += 1
        else:
            streak = 1
        previous_bucket = bucket
        local_time = observed_at.timetz().replace(tzinfo=None)
        if local_time >= time(15, 0) or (local_time >= time(10, 30) and streak >= 2):
            return True
    return False


def _miss_streak(name: str, observations: Sequence[Mapping[str, Any]], data_date: str) -> int:
    misses = 0
    newer_bucket: datetime | None = None
    for row in reversed(observations):
        if row.get("data_date") != data_date:
            continue
        try:
            bucket = datetime.fromisoformat(str(row.get("bucket_at") or ""))
        except ValueError:
            break
        if newer_bucket is not None and newer_bucket - bucket > timedelta(minutes=BUCKET_MINUTES):
            break
        if name in _direction_names(row):
            break
        misses += 1
        newer_bucket = bucket
    return misses


def _withdrawn_mainlines(
    observations: Sequence[Mapping[str, Any]],
    data_date: str,
    *,
    current_names: set[str],
    session_stage: str,
) -> list[dict[str, Any]]:
    historical_names = {
        name
        for row in observations if row.get("data_date") == data_date
        for name in _direction_names(row)
    }
    out: list[dict[str, Any]] = []
    for name in sorted(historical_names - current_names):
        if not _was_confirmed(name, observations, data_date):
            continue
        misses = _miss_streak(name, observations, data_date)
        if misses <= 0:
            continue
        final_session = session_stage in {"post_close", "recent_close"}
        revoked = final_session or misses >= 2
        last_seen = next(
            (
                str(row.get("observed_at") or "")
                for row in reversed(observations)
                if row.get("data_date") == data_date and name in _direction_names(row)
            ),
            None,
        )
        out.append({
            "name": name,
            "status": "revoked" if revoked else "weakening",
            "status_label": "已撤销" if revoked else "降级观察",
            "miss_streak": misses,
            "last_seen_at": last_seen,
            "reason": (
                "收盘完整截面未再通过结构硬门槛"
                if final_session else f"连续{misses}个10分钟档未通过结构硬门槛"
            ),
        })
    out.sort(key=lambda row: (row["status"] != "weakening", -row["miss_streak"], row["name"]))
    return out[:8]


def _persistent_mainlines(observations: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """使用每个交易日的最后一档主线快照判断 2/3 日或 3/5 日持续性。"""
    daily: dict[str, Mapping[str, Any]] = {}
    for row in observations:
        data_date = str(row.get("data_date") or "")
        if data_date and row.get("data_state") != "missing":
            daily[data_date] = row
    dates = sorted(daily)[-5:]
    last_three = dates[-3:]
    counts3 = Counter(
        str(daily[date].get("primary_name"))
        for date in last_three if daily[date].get("primary_name")
    )
    counts5 = Counter(
        str(daily[date].get("primary_name"))
        for date in dates if daily[date].get("primary_name")
    )
    names = sorted({*counts3, *counts5})
    persistent = [
        {
            "name": name,
            "hits_3d": counts3.get(name, 0),
            "hits_5d": counts5.get(name, 0),
            "status": "confirmed",
        }
        for name in names
        if counts3.get(name, 0) >= 2 or counts5.get(name, 0) >= 3
    ]
    coverage = {
        "status": "accumulating" if len(dates) < 3 else "ready",
        "covered_days": len(dates),
        "minimum_days": 3,
        "available_dates": dates,
        "rule": "最近3个已覆盖交易日中至少2日，或最近5日中至少3日收盘成为第一主线",
    }
    return persistent, coverage


def annotate_response(
    response: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    observed_at: datetime,
) -> dict[str, Any]:
    """在不改动截面评分的前提下，增加时间尺度、确认状态与跨日样本覆盖。"""
    result = deepcopy(dict(response))
    data_date = str(result.get("data_date") or "")
    session_stage = _session_stage(observed_at, data_date)
    session_label = _SESSION_LABELS[session_stage]
    data_state = str(result.get("data_state") or "missing")
    current_day_rows = [row for row in observations if row.get("data_date") == data_date]
    current_bucket = _bucket_at(observed_at)
    previous_observation = next(
        (
            row for row in reversed(current_day_rows)
            if str(row.get("bucket_at") or "") != current_bucket
        ),
        None,
    )
    previous_labels = {
        str(row.get("name")): str(row.get("label"))
        for row in (previous_observation or {}).get("directions") or []
        if isinstance(row, Mapping) and row.get("name")
    }

    enriched_roles: list[dict[str, Any]] = []
    for source in result.get("roles") or []:
        if not isinstance(source, Mapping):
            continue
        role = dict(source)
        label = str(role.get("label") or "")
        if role.get("status") != "formed":
            time_status = "unavailable" if data_state == "missing" else "unformed"
            hits = 0
        elif label not in {"主线", "次主线"}:
            time_status = "observed"
            hits = 1
        else:
            name = str(role.get("name") or "")
            hits = _consecutive_hits(name, current_day_rows, data_date)
            if session_stage in {"post_close", "recent_close"}:
                time_status = "final"
            elif session_stage == "opening_discovery":
                time_status = "candidate"
            elif session_stage == "forming" or hits < 2:
                time_status = "forming"
            else:
                time_status = "confirmed"
        role["time_status"] = time_status
        role["time_status_label"] = _STATUS_LABELS[time_status]
        role["consecutive_hits"] = hits
        if role.get("status") == "formed" and label in {"主线", "次主线"}:
            previous_label = previous_labels.get(str(role.get("name") or ""))
            if previous_label == "主线" and label == "次主线":
                transition, transition_label = "demoted", "主转次"
            elif previous_label == "次主线" and label == "主线":
                transition, transition_label = "promoted", "次升主"
            elif previous_label is None:
                transition, transition_label = "entered", "新进入"
            else:
                transition, transition_label = "stable", "角色稳定"
            role["previous_label"] = previous_label
            role["role_transition"] = transition
            role["role_transition_label"] = transition_label
        enriched_roles.append(role)
    result["roles"] = enriched_roles

    direction_roles = [
        role for role in enriched_roles
        if role.get("label") in {"主线", "次主线"} and role.get("status") == "formed"
    ]
    result["today_mainlines"] = [
        role for role in direction_roles if role.get("time_status") in {"confirmed", "final"}
    ]
    result["today_candidates"] = [
        role for role in direction_roles if role.get("time_status") in {"candidate", "forming"}
    ]
    result["role_transitions"] = [
        {
            "name": role.get("name"),
            "from": role.get("previous_label"),
            "to": role.get("label"),
            "status": role.get("role_transition"),
            "status_label": role.get("role_transition_label"),
        }
        for role in direction_roles
        if role.get("role_transition") in {"entered", "promoted", "demoted"}
    ]
    current_direction_names = {str(role.get("name")) for role in direction_roles if role.get("name")}
    result["withdrawn_mainlines"] = _withdrawn_mainlines(
        current_day_rows,
        data_date,
        current_names=current_direction_names,
        session_stage=session_stage,
    )
    persistent, persistence = _persistent_mainlines(observations)
    result["persistent_mainlines"] = persistent
    result["persistence"] = persistence
    result["time_scope"] = {
        "schema_version": SCHEMA_VERSION,
        "horizon": "intraday",
        "universe": "industry",
        "product_label": "今日行业主线",
        "persistent_label": "持续行业主线",
        "session_stage": session_stage,
        "session_label": session_label,
        "as_of": observed_at.isoformat(),
        "sampling_bucket_minutes": BUCKET_MINUTES,
        "observation_count": len(current_day_rows),
        "confirmation_rule": "10:30后连续2个不同10分钟档达标才确认；收盘使用完整当日截面定稿",
        "downgrade_rule": "已确认方向第1个有效10分钟档失守→降级观察",
        "revocation_rule": "连续2个有效10分钟档失守→撤销；收盘完整截面失守可直接撤销",
        "role_change_rule": "仍满足次主线门槛时可主转次，不按撤销处理；次主线升至第一则标次升主",
        "missing_data_rule": "数据缺失档不触发形成、降级或撤销",
    }
    algorithm = dict(result.get("algorithm") or {})
    algorithm["temporal_version"] = SCHEMA_VERSION
    result["algorithm"] = algorithm
    return result


def observe_response(data_dir: str | Path, response: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    """按客户 ``data_dir`` 隔离落盘，并返回带时间状态的响应。"""
    path = Path(data_dir) / "mainline_timeline.json"
    current = _make_observation(response, observed_at)
    with _LOCK:
        observations = _read(path)
        if current["data_date"] and current["data_state"] != "missing":
            observations = _upsert(observations, current)
            _write(path, observations)
    return annotate_response(response, observations, observed_at)
