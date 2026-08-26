from __future__ import annotations

import stat
from pathlib import Path

import pytest

from hermes_trader.config import Config


def _config(tmp_path: Path) -> Config:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
client:
  id: test
  name: Test
server:
  data_dir: ./data
hermes:
  base_url: https://api.deepseek.com/v1
  api_key: test-key-never-returned
feishu:
  webhook: https://open.feishu.cn/open-apis/bot/v2/hook/test
  secret: test-secret-never-returned
push:
  enabled: true
""",
        encoding="utf-8",
    )
    return Config(cfg)


def test_settings_view_never_returns_credentials(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    view = cfg.settings_view()

    assert view["feishu_webhook"] == ""
    assert view["feishu_webhook_set"] is True
    assert view["feishu_secret"] == ""
    assert view["feishu_secret_set"] is True
    assert view["hermes_api_key"] == ""
    assert view["hermes_api_key_set"] is True
    assert view["public_base_url"] == ""


def test_public_base_url_is_explicit_normalized_and_visible(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
client: {id: test, name: Test}
server:
  data_dir: ./data
  public_base_url: http://111.231.173.220/
""",
        encoding="utf-8",
    )

    cfg = Config(cfg_path)

    assert cfg.public_base_url == "http://111.231.173.220"
    assert cfg.settings_view()["public_base_url"] == "http://111.231.173.220"


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com/app",
        "https://example.com?next=bad",
        "https://example.com/#/premarket",
    ],
)
def test_public_base_url_rejects_unsafe_or_non_root_values(
    tmp_path: Path,
    value: str,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"client: {{id: test, name: Test}}\nserver:\n  data_dir: ./data\n  public_base_url: {value!r}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="server.public_base_url"):
        Config(cfg_path)


def test_gateway_bridge_can_reuse_restricted_gateway_key_during_init(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
client:
  id: bridge-test
  name: Bridge Test
server:
  data_dir: ./data
hermes:
  base_url: http://172.17.0.1:18877/v1
  api_key: restricted-proxy-key
push:
  enabled: true
  via: gateway
  gateway:
    targets: [feishu]
    bridge_url: http://172.17.0.1:18877/v1/delivery/home
""",
        encoding="utf-8",
    )

    cfg = Config(cfg_path)

    assert cfg.push_gateway_bridge_url.endswith("/v1/delivery/home")
    assert cfg.push_gateway_bridge_token == "restricted-proxy-key"
    assert cfg.settings_view()["hermes_api_key"] == ""


def test_client_config_env_and_data_directory_are_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Config 会把客户 .env 注入进程环境；测试必须由
    # monkeypatch 跟踪并在用例后恢复，避免泄漏到其他客户配置测试。
    # 先由 monkeypatch 建立可恢复的值，让 _load_dotenv(setdefault)
    # 不直接创建一个逃离 monkeypatch 记录的全局键。
    monkeypatch.setenv("HERMES_API_KEY", "isolated-permission-test")
    env_path = tmp_path / ".env"
    env_path.write_text("HERMES_API_KEY=placeholder-only\n", encoding="utf-8")

    cfg = _config(tmp_path)

    assert stat.S_IMODE(cfg.config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.data_dir.stat().st_mode) == 0o700


def test_settings_are_atomic_and_private(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.save_overrides({"hermes_model": "safe-model", "push_enabled": False})

    assert cfg.settings_path.exists()
    assert stat.S_IMODE(cfg.settings_path.stat().st_mode) == 0o600
    assert cfg.hermes_model == "safe-model"
    assert cfg.push_enabled is False
    assert not cfg.settings_path.with_name(f".{cfg.settings_path.name}.tmp").exists()


def test_scheduled_push_defaults_on_and_partial_override_persists(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    assert cfg.scheduled_push == {
        "morning": True,
        "tail": True,
        "close": True,
        "evening": True,
        "archive": True,
    }

    cfg.save_overrides({"scheduled_push": {"tail": False}})
    reloaded = Config(cfg.config_path)

    assert reloaded.scheduled_push_enabled("morning") is True
    assert reloaded.scheduled_push_enabled("tail") is False
    assert reloaded.scheduled_push_enabled("midday") is False
    assert reloaded.settings_view()["scheduled_push"]["tail"] is False


def test_custom_model_connection_can_be_removed_without_touching_defaults(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.save_overrides({
        "hermes_base_url": "https://models.example.com/v1",
        "hermes_model": "custom-model",
        "hermes_api_key": "custom-secret",
    })

    assert cfg.model_connection_custom is True
    assert cfg.settings_view()["hermes_api_key"] == ""
    cfg.delete_overrides({"hermes_base_url", "hermes_model", "hermes_api_key"})

    assert cfg.model_connection_custom is False
    assert cfg.hermes_base_url == "https://api.deepseek.com/v1"
    assert cfg.hermes_model != "custom-model"
    assert "custom-secret" not in cfg.settings_path.read_text(encoding="utf-8")


def test_deployment_profile_auto_uses_configured_network_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATA_RELAY_PROXY", raising=False)
    monkeypatch.delenv("DATA_RELAY_API", raising=False)
    cfg = _config(tmp_path)
    assert cfg.deployment_profile_configured == "auto"
    assert cfg.deployment_profile == "cn_home"

    monkeypatch.setenv("DATA_RELAY_PROXY", "socks5://127.0.0.1:11080")
    relayed_dir = tmp_path / "relayed"
    relayed_dir.mkdir()
    relayed = _config(relayed_dir)
    assert relayed.deployment_profile == "overseas_server"


def test_deployment_profile_can_be_explicit_but_cannot_be_invented(
    tmp_path: Path,
) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
client: {id: test, name: Test}
server: {data_dir: ./data}
data_source:
  deployment_profile: cn_server
""",
        encoding="utf-8",
    )
    assert Config(cfg_path).deployment_profile == "cn_server"

    cfg_path.write_text(
        """
client: {id: test, name: Test}
server: {data_dir: ./data}
data_source:
  deployment_profile: guess_by_ip
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="部署地域策略"):
        Config(cfg_path)
