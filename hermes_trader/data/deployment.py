"""Deployment-aware source routing facts.

This module never calls an IP geolocation service and never mutates proxy
settings. It translates explicit configuration into a small, inspectable
policy that health pages and adapters can share.
"""
from __future__ import annotations

import os

_VALID_PROFILES = frozenset({"cn_home", "cn_server", "overseas_server"})


def deployment_profile() -> str:
    value = os.environ.get("HERMES_DEPLOYMENT_PROFILE", "cn_home").strip().lower()
    return value if value in _VALID_PROFILES else "cn_home"


def source_route_policy() -> dict:
    profile = deployment_profile()
    relay_api = bool(os.environ.get("DATA_RELAY_API", "").strip())
    relay_proxy = bool(os.environ.get("DATA_RELAY_PROXY", "").strip())
    relay = relay_api or relay_proxy
    eastmoney_direct = os.environ.get("HERMES_EM_DIRECT") == "1"

    if profile == "overseas_server":
        china_public_route = "relay" if relay else "disabled"
        public_note = (
            "国内公开源经已配置境内中继；TDX API 与腾讯仍各自直连"
            if relay else
            "未配置境内中继，国内 IP 敏感公开源停用；不以跨境重复重试拖慢产品"
        )
    elif profile == "cn_server":
        china_public_route = "direct"
        public_note = "国内公开源直连；东财仅在显式 eastmoney_direct=true 时进入备用链"
    else:
        china_public_route = "direct_low_concurrency"
        public_note = "家庭网络按低并发直连；高频首屏只用 TDX API 与腾讯"

    return {
        "profile": profile,
        "configured": os.environ.get(
            "HERMES_DEPLOYMENT_PROFILE_CONFIGURED", "auto"
        ),
        "quote_primary": "tdx_api",
        "quote_fallback": "tencent",
        "china_public_route": china_public_route,
        "eastmoney_direct": eastmoney_direct,
        "relay_configured": relay,
        "relay_api_configured": relay_api,
        "relay_proxy_configured": relay_proxy,
        "public_note": public_note,
        "principles": [
            "不使用公网 IP 猜测部署地域",
            "交互首屏不等待国内公开增强源",
            "单个增强源失败只降级对应模块",
        ],
    }
