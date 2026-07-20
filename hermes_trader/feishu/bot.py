"""飞书互动机器人（可选）— 长连接收消息，盘中直接在飞书里问股。

依赖 lark-oapi（pip install lark-oapi），并在飞书开放平台创建自建应用：
  - 开启「机器人」能力，事件订阅选择「使用长连接接收事件」
  - 订阅事件 im.message.receive_v1，开通 im:message 收发权限
config.yaml 填入 app_id / app_secret 后自动启用；未配置则静默跳过。

支持指令：
  600519 / 问600519 怎么看   → 盘中问股（自动注入实时盘面）
  早报/内参 复盘 策略 尾盘/观察 趋势  → 返回今日对应报告
  持仓                        → 持仓诊断
  其他任意文本                 → 自由问答（注入市场快照）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from types import SimpleNamespace

from .push import save_home_chat

log = logging.getLogger("hermes.feishu.bot")

_CODE_RE = re.compile(r"\b(\d{6})\b")

REPORT_ALIASES = {
    "早报": "morning", "内参": "morning", "早盘": "morning",
    "午后": "midday", "机会": "midday",
    "尾盘": "tail", "观察": "tail",
    "复盘": "close",
    "策略": "evening", "次日": "evening",
    "趋势": "trend",
}


class FeishuBot:
    """封装 lark-oapi 长连接客户端；在独立线程运行，回调切回主事件循环。"""

    def __init__(self, config, engine, loop: asyncio.AbstractEventLoop):
        self.config = config
        self.engine = engine
        self.loop = loop
        self._thread: threading.Thread | None = None
        self._api_client = None

    @property
    def configured(self) -> bool:
        return bool(self.config.feishu_app_id and self.config.feishu_app_secret)

    def start(self) -> bool:
        if not self.configured:
            return False
        try:
            import lark_oapi as lark  # noqa: PLC0415
        except ImportError:
            log.warning("未安装 lark-oapi，飞书互动机器人不启用（pip install lark-oapi）")
            return False

        # lark-oapi 底层走 requests（HTTP 调用与 ws 长连接握手），trust_env 默认开启，
        # 会读取 base.py 写入的 ALL_PROXY/HTTPS_PROXY=socks5://... 把对 open.feishu.cn 的
        # 回复请求路由经数据中继隧道——隧道一挂盘中问股回复全断。lark.Client.builder()
        # 未暴露 trust_env/proxies 配置，故在进程级 NO_PROXY 补入飞书域兜底豁免。
        # （base.py 的 _apply_relay_env 已含同样豁免，此处仅为不依赖其加载时序的双保险。）
        self._bypass_feishu_proxy()

        self._api_client = (
            lark.Client.builder()
            .app_id(self.config.feishu_app_id)
            .app_secret(self.config.feishu_app_secret)
            .build()
        )

        def on_message(data) -> None:
            try:
                msg = data.event.message
                # 零配置推送绑定：任一入站消息即把该会话记为推送 home（谁给机器人发消息，
                # 定时报告/PDF 就推给谁）。FeishuPusher 的 bot API 通道读此绑定。
                cid = getattr(msg, "chat_id", "") or ""
                if cid:
                    save_home_chat(self.config.data_dir, cid)
                if msg.message_type != "text":
                    return
                text = json.loads(msg.content).get("text", "").strip()
                if not text:
                    return
                asyncio.run_coroutine_threadsafe(
                    self._handle(msg.message_id, text), self.loop
                )
            except Exception:
                log.exception("处理飞书消息失败")

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        ws_client = lark.ws.Client(
            self.config.feishu_app_id,
            self.config.feishu_app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )
        def _run_ws() -> None:
            # 给本线程一个干净的新事件循环，并【重绑 lark ws 模块级全局 loop】：
            # lark-oapi 1.7.x 的 ws.client 在 import 时执行 `loop = asyncio.get_event_loop()`——
            # 生产里 lark 在 uvicorn 主线程被 import → 该全局绑到正在运行的主循环 →
            # start() 的 run_until_complete 必撞 "event loop is already running"（本线程 set 新
            # 循环救不了模块全局）。此处强制把模块全局 loop 指到本线程新循环
            # （2026-07-05 服务器实测修复，热修回灌转正）。
            try:
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
                try:
                    import lark_oapi.ws.client as _wsm  # noqa: PLC0415
                    _wsm.loop = _loop
                except Exception:
                    pass  # 旧版 lark 无此全局，维持原行为
                ws_client.start()
            except Exception:
                log.exception("飞书 ws 长连接异常退出（不影响主服务与网页）")

        self._thread = threading.Thread(target=_run_ws, daemon=True, name="feishu-ws")
        self._thread.start()
        log.info("飞书互动机器人已启动（长连接）")
        return True

    @staticmethod
    def _bypass_feishu_proxy() -> None:
        """把飞书域名并入进程级 NO_PROXY，让 lark-oapi 的 requests 直连 open.feishu.cn，
        不被中继 SOCKS 代理劫持。幂等：已含则不重复追加。"""
        feishu_hosts = (".feishu.cn", "open.feishu.cn")
        for var in ("NO_PROXY", "no_proxy"):
            cur = os.environ.get(var, "")
            items = [x.strip() for x in cur.split(",") if x.strip()]
            for h in feishu_hosts:
                if h not in items:
                    items.append(h)
            os.environ[var] = ",".join(items)

    # ------------------------------------------------ 消息处理

    async def _handle(self, message_id: str, text: str) -> None:
        try:
            reply = await self._dispatch(text)
        except Exception as e:
            log.exception("生成回复失败")
            reply = f"⚠️ 处理失败：{e}"
        await asyncio.to_thread(self._reply, message_id, reply)

    async def _dispatch(self, text: str) -> str:
        # 报告指令
        for alias, rtype in REPORT_ALIASES.items():
            if alias in text and len(text) <= 6:
                rec = self.engine.store.get_report(rtype)
                if rec:
                    marker = ""
                    if rec.get("content_hash"):
                        version = rec.get("plan_version") or rec.get("report_version") or 1
                        marker = f"\n\n内容版本：v{version} · {str(rec['content_hash'])[:12]}"
                    return f"【{rec['title']} · {rec['date']}】\n\n{rec['content_md'][:9000]}{marker}"
                return f"今日《{alias}》尚未生成，可在可视化界面手动生成，或等待定时任务。"
        if text in ("持仓", "持仓诊断"):
            return await self._collect_governed_positions()
        # 盘中问股
        m = _CODE_RE.search(text)
        if m:
            code = m.group(1)
            return await self._collect_governed_stock(code)
        # 自由问答
        return await self._collect(mode="free", message=text)

    async def _collect_governed_stock(self, code: str) -> str:
        """飞书问股复用 Web 的确定性诊断+风险闸+留痕，不走自由模型旁路。"""
        from ..api.routes import _governed_stock_chat_stream  # noqa: PLC0415

        gateway = getattr(self.engine, "market_gateway", None)
        state = SimpleNamespace(
            engine=self.engine,
            store=self.engine.store,
            market_gateway=gateway,
            evidence_store=getattr(gateway, "store", None),
        )
        parts = [chunk async for chunk in _governed_stock_chat_stream(state, code)]
        return "".join(parts).strip() or "当前无法完成证据核验与建议留痕。"

    async def _collect_governed_positions(self) -> str:
        from ..api.routes import _governed_position_chat_stream  # noqa: PLC0415

        gateway = getattr(self.engine, "market_gateway", None)
        state = SimpleNamespace(
            engine=self.engine,
            store=self.engine.store,
            market_gateway=gateway,
            evidence_store=getattr(gateway, "store", None),
        )
        parts = [chunk async for chunk in _governed_position_chat_stream(state, [])]
        return "".join(parts).strip() or "当前没有可诊断的持仓。"

    async def _collect(self, mode: str, message: str, code: str = "") -> str:
        parts: list[str] = []
        async for event in self.engine.chat_stream(mode, message, code=code):
            if "error" in event:
                return f"⚠️ {event['error']}"
            if "delta" in event:
                parts.append(event["delta"])
        return "".join(parts).strip() or "（Hermes 未返回内容）"

    def _reply(self, message_id: str, text: str) -> None:
        try:
            from lark_oapi.api.im.v1 import (  # noqa: PLC0415
                CreateMessageReplyRequest,
                CreateMessageReplyRequestBody,
            )

            req = (
                CreateMessageReplyRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReplyRequestBody.builder()
                    .content(json.dumps({"text": text[:9500]}, ensure_ascii=False))
                    .msg_type("text")
                    .build()
                )
                .build()
            )
            resp = self._api_client.im.v1.message.reply(req)
            if not resp.success():
                log.error("飞书回复失败: code=%s msg=%s", resp.code, resp.msg)
        except Exception:
            log.exception("飞书回复异常")
