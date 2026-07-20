from __future__ import annotations

from pathlib import Path


SETTINGS_JS = Path(__file__).resolve().parents[1] / "frontend/js/views/settings.js"
APP_JS = Path(__file__).resolve().parents[1] / "frontend/js/app.js"


def test_delivery_status_uses_user_facing_semantics() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "自动发送状态" in source
    assert "当前报告只保留在系统内" in source
    assert "push_channel_unconfigured: '还没有设置报告接收渠道'" in source
    assert "自动交付上线门禁" not in source
    assert "上线门禁已阻断" not in source


def test_delivery_channels_consume_readiness_v2_without_inventing_success() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "deliveryGate?.channels" in source
    for key in ("web", "pdf", "feishu", "wechat"):
        assert f"key: '{key}'" in source
    assert "contract.last_success_at || contract.last_success || null" in source
    assert "contract.state || contract.status || 'unknown'" in source
    assert "能力可用·待首次生成" in source
    assert "待首次生成" in source
    assert "配置状态 ≠ 已送达" in source


def test_delivery_channels_show_failure_reason_and_only_real_retry_actions() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "projected_error" in source
    assert "row.latest_receipt?.error_code" in source
    assert "row.receipt?.delivery_id" in source
    assert 'data-delivery-id=' in source
    assert "无待重试项" in source
    assert "未配置" in source
    assert "最近失败" in source


def test_model_connection_is_custom_and_hermes_is_optional() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "测试当前填写" in source
    assert "自定义连接管理" in source
    assert "可选集成 / 高级诊断（Hermes）" in source
    assert "api.post('/settings/model/test'" in source
    assert "api.get('/settings/model-connections')" in source
    assert "api.post('/settings/model-connections'" in source
    assert "api.put(`/settings/model-connections/${encodeURIComponent(payload.connection_id)}`" in source
    assert "api.post(`/settings/model-connections/${encodeURIComponent(connectionId)}/activate`" in source
    assert "api.del(`/settings/model-connections/${encodeURIComponent(connectionId)}`" in source
    assert "api.del('/settings/model'" not in source
    assert "未启用时是正常状态，不会标记为系统故障" in source
    assert "hermesRuntimeMode !== 'direct_model_api'" in source
    assert "未启用（可选）" in source
    assert "Hermes 状态不用来判定模型连接" in source
    assert "modelCustom && hermes.connected" not in source
    assert "恢复默认配置" not in source


def test_task_center_shows_v1_timing_error_retry_and_real_output_objects() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "status?.task_center?.jobs || status?.jobs || []" in source
    assert "j.next_run_at || j.next_run" in source
    assert "result.duration_ms ?? j.last_duration_ms" in source
    assert "result.error || result.error_code || j.last_error || j.last_error_code" in source
    assert "const blockedJobsAll = jobs.filter" in source
    assert "const blockedJobs = blockedJobsAll.filter(isTodayJob)" in source
    assert "上次阻止（历史）" in source
    assert "job?.last_result?.output_objects" in source
    assert "job?.last_output_objects" in source
    assert "最近产物（实际输出对象）" in source
    assert "尚无产物" in source
    for field in ("content_id", "trade_day", "version", "content_hash", "report_types"):
        assert f"row.{field}" in source


def test_unconfigured_optional_model_does_not_render_as_a_red_failure() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "s.hermes?.custom_connection === true" in source
    assert "AI 增强未配置（可选）" in source
    assert "!customModel," in source


def test_data_source_table_renders_canonical_degraded_state() -> None:
    source = SETTINGS_JS.read_text(encoding="utf-8")

    assert "source.key === 'universe'" in source
    assert "source.status === 'degraded'" in source
    assert "已就绪" in source
    assert "可用但待刷新" in source
    assert "标准字段契约" in source
    assert "meta.meaning" in source
    assert "meta.batch_scope" in source
