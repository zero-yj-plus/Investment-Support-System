from pathlib import Path

import pytest

from hermes_trader.gateway_home_delivery import (
    home_platforms_from_env,
    translate_delivery_media,
)


def test_home_platforms_only_accepts_supported_sethome_keys(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "FEISHU_HOME_CHANNEL=oc_feishu\n"
        "WEIXIN_HOME_CHANNEL=wx_user\n"
        "LIGHTCLAWBOT_HOME_CHANNEL=internal\n"
        "TELEGRAM_HOME_CHANNEL=\n",
        encoding="utf-8",
    )

    assert home_platforms_from_env(tmp_path) == {"feishu", "weixin"}


def test_media_mapping_stays_inside_customer_shared_data(tmp_path: Path) -> None:
    shared = tmp_path / "shared" / "data"
    media = shared / "pdf" / "report.pdf"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"pdf")

    body = "盘前报告\nMEDIA:/app/clients/customer/data/pdf/report.pdf"
    translated = translate_delivery_media(body, shared)

    assert translated == f"盘前报告\nMEDIA:{media.resolve()}"


def test_media_mapping_rejects_arbitrary_host_file(tmp_path: Path) -> None:
    shared = tmp_path / "shared" / "data"
    shared.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="outside the customer shared data"):
        translate_delivery_media(f"MEDIA:{outside}", shared)
