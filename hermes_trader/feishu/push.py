"""飞书推送 — 双通道：
① 自定义机器人 webhook（原路径，签名校验+长文分片卡片）；
② 应用凭据 bot API 直发（webhook 未配时启用）：用互动机器人的 app_id/app_secret 经
   im/v1/messages 发到「home 会话」——客户在飞书里给机器人**任发一条消息即自动绑定**
   （bot.py 收消息落盘 feishu_home.json），无绑定则兜底取机器人所在的第一个群聊。
   零配置推送，不必让客户建群机器人拿 webhook（2026-07-11 审计 M1：网关无平台致推送全断的修复）。
PDF 等文件附件仅 bot API 通道支持（webhook 机器人发不了文件）。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

from ..data.base import now_cn  # 时钟铁律:给人看/落盘的时间戳必须走北京时区,禁用本机裸时钟

log = logging.getLogger("hermes.feishu")

_HOME_FILE = "feishu_home.json"


def save_home_chat(data_dir, chat_id: str) -> None:
    """互动机器人收到消息即落盘 home 会话（谁给机器人发消息，定时报告/PDF 就推给谁）。
    仅变化时写；原子落盘。由 bot.py 在 lark 回调线程调用（纯文件操作，线程安全够用）。"""
    try:
        p = Path(data_dir) / _HOME_FILE
        if p.exists():
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("chat_id") == chat_id:
                    return
            except Exception:  # noqa: BLE001 —— 坏文件直接覆盖重建
                pass
        tmp = f"{p}.{os.getpid()}.tmp"   # pid 后缀：lark 回调线程与主线程兜底并发写时不共用 tmp
        Path(tmp).write_text(
            json.dumps({"chat_id": chat_id, "updated_at": now_cn().strftime("%Y-%m-%d %H:%M:%S")},
                       ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        log.info("飞书推送目标已绑定 home 会话（chat_id %s…）", str(chat_id)[:6])
    except Exception:  # noqa: BLE001
        log.exception("保存飞书 home 会话失败")


def load_home_chat(data_dir) -> str:
    try:
        p = Path(data_dir) / _HOME_FILE
        if p.exists():
            return str(json.loads(p.read_text(encoding="utf-8")).get("chat_id", "") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""

HEADER_COLORS = {
    "morning": "orange",
    "midday": "blue",
    "tail": "purple",
    "close": "wathet",
    "evening": "indigo",
    "trend": "turquoise",
}

_MD_HEADING = re.compile(r"^(#{1,6})\s*(.+)$", re.MULTILINE)

# 单卡片内容上限按【UTF-8 字节数】计：飞书卡片约 30KB，留足余量取 18000 字节。
# 历史 bug：曾按【字符数】20000 切片，报告几乎全中文（UTF-8 每字 3 字节），
# 单片最大可达 ~60KB 远超卡片上限，长报告第一片即被飞书拒收。
CHUNK_BYTES = 18000


def _md_for_card(md: str) -> str:
    """飞书卡片 markdown 元素不支持 # 标题，转为加粗行。"""
    return _MD_HEADING.sub(lambda m: f"**{m.group(2).strip()}**", md)


def _split_by_bytes(s: str, budget: int) -> list[str]:
    """按 UTF-8 字节预算切片，不切断多字节字符（逐字符累加字节数，超预算就开新片）。"""
    chunks: list[str] = []
    cur: list[str] = []
    cur_bytes = 0
    for ch in s:
        b = len(ch.encode("utf-8"))
        if cur and cur_bytes + b > budget:
            chunks.append("".join(cur))
            cur, cur_bytes = [], 0
        cur.append(ch)
        cur_bytes += b
    if cur:
        chunks.append("".join(cur))
    return chunks


class FeishuPusher:
    def __init__(self, config):
        self.config = config
        self._lark_client = None   # bot API 通道的 lark 客户端（懒建）
        self._last_message_ids: list[str] = []
        self._last_send_ambiguous = False

    @property
    def configured(self) -> bool:
        # webhook（自定义群机器人）或 应用凭据（互动机器人 bot API 直发）任一可用即算已配置
        return bool(self.config.feishu_webhook) or self._bot_ready()

    # ------------------------------------------------ bot API 通道（webhook 未配时的直发路径）

    def _bot_ready(self) -> bool:
        return bool(self.config.feishu_app_id and self.config.feishu_app_secret)

    async def preflight(self) -> dict:
        if self.config.feishu_webhook:
            return {"configured": True, "ready": True, "targets": ["feishu_webhook"], "mode": "webhook"}
        bound = load_home_chat(self.config.data_dir) if self._bot_ready() else ""
        return {
            "configured": self.configured,
            "ready": bool(self._bot_ready() and bound),
            "targets": ["feishu_home"] if bound else [],
            "mode": "bot",
            "blocker": None if bound else "feishu_home_unbound",
        }

    def _bot_client(self):
        if self._lark_client is None:
            import lark_oapi as lark  # noqa: PLC0415
            # 与 bot.py 同款豁免：确保对 open.feishu.cn 直连、不被数据中继 SOCKS 代理劫持
            for var in ("NO_PROXY", "no_proxy"):
                cur = os.environ.get(var, "")
                items = [x.strip() for x in cur.split(",") if x.strip()]
                for h in (".feishu.cn", "open.feishu.cn"):
                    if h not in items:
                        items.append(h)
                os.environ[var] = ",".join(items)
            self._lark_client = (lark.Client.builder()
                                 .app_id(self.config.feishu_app_id)
                                 .app_secret(self.config.feishu_app_secret)
                                 .build())
        return self._lark_client

    def _bot_target(self) -> str:
        """推送目标：home 会话（客户给机器人发过消息即绑定）→ 兜底取机器人所在第一个群聊并回写绑定。
        全空返回 ""（调用方给出"发一条消息即绑定"的指引，绝不瞎发）。"""
        cid = load_home_chat(self.config.data_dir)
        if cid:
            return cid
        try:
            from lark_oapi.api.im.v1 import ListChatRequest  # noqa: PLC0415
            resp = self._bot_client().im.v1.chat.list(ListChatRequest.builder().page_size(20).build())
            items = getattr(getattr(resp, "data", None), "items", None) or []
            if resp.success() and items:
                cid = getattr(items[0], "chat_id", "") or ""
                if cid:
                    save_home_chat(self.config.data_dir, cid)
                    return cid
        except Exception:  # noqa: BLE001
            log.exception("查询机器人所在群列表失败")
        return ""

    def _bot_send_content(self, msg_type: str, content) -> bool:
        """im/v1/messages 直发 home 会话。content: dict 自动 JSON 序列化。同步 SDK——调用方需 to_thread。"""
        cid = self._bot_target()
        if not cid:
            log.warning("飞书推送：尚未绑定接收会话——请让接收人在飞书里给机器人任发一条消息，即自动绑定")
            return False
        try:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody  # noqa: PLC0415
            body = (CreateMessageRequestBody.builder()
                    .receive_id(cid).msg_type(msg_type)
                    .content(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
                    .build())
            req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            resp = self._bot_client().im.v1.message.create(req)
            if not resp.success():
                log.error("飞书 bot 发送失败: code=%s msg=%s", resp.code, resp.msg)
            else:
                message_id = str(getattr(getattr(resp, "data", None), "message_id", "") or "")
                if message_id:
                    self._last_message_ids.append(message_id)
            return bool(resp.success())
        except Exception as exc:  # noqa: BLE001
            self._last_send_ambiguous = isinstance(exc, TimeoutError)
            log.exception("飞书 bot 发送异常")
            return False

    def _sign(self, timestamp: int) -> str:
        string_to_sign = f"{timestamp}\n{self.config.feishu_secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def _send(self, payload: dict) -> bool:
        self._last_send_ambiguous = False
        if not self.config.feishu_webhook:
            # webhook 未配 → bot API 通道（应用凭据直发 home 会话）；两者皆无才跳过
            if not self._bot_ready():
                log.warning("飞书 webhook 与应用凭据均未配置，跳过推送")
                return False
            mt = str(payload.get("msg_type", "text"))
            content = payload.get("card") if mt == "interactive" else payload.get("content", {})
            return await asyncio.to_thread(self._bot_send_content, mt, content)
        if self.config.feishu_secret:
            ts = int(time.time())
            payload = {"timestamp": str(ts), "sign": self._sign(ts), **payload}
        try:
            # trust_env=False：base.py 把 DATA_RELAY_PROXY=socks5://... 写进进程级
            # ALL_PROXY/HTTP(S)_PROXY，httpx 默认会读取并把飞书 webhook 误路由经数据中继
            # 隧道——隧道一挂推送全断。飞书直连本机即可，显式关掉环境代理读取。
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                resp = await client.post(self.config.feishu_webhook, json=payload)
                body = resp.json()
                if body.get("code", body.get("StatusCode", -1)) in (0, "0"):
                    return True
                log.error("飞书推送被拒: %s", body)
                return False
        except Exception as e:
            self._last_send_ambiguous = isinstance(e, (httpx.TimeoutException, TimeoutError))
            log.error("飞书推送失败: %s", e)
            return False

    async def push_text(self, text: str) -> bool:
        return await self._send({"msg_type": "text", "content": {"text": text[:9000]}})

    async def push_card(self, title: str, md: str, color: str = "blue") -> bool:
        content = _md_for_card(md)
        chunks = _split_by_bytes(content, CHUNK_BYTES)[:3]
        ok = True
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            suffix = f"（{i + 1}/{total}）" if total > 1 else ""
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": color,
                    "title": {"tag": "plain_text", "content": f"{title}{suffix}"},
                },
                "elements": [{"tag": "markdown", "content": chunk}],
            }
            ok = await self._send({"msg_type": "interactive", "card": card}) and ok
        return ok

    async def push_report(self, meta: dict, rec: dict) -> dict:
        # 报告卡片标题冠以客户名号（按客户 config.client.name 定制）
        title = f"{meta.get('emoji', '')} {self.config.client_name}·{rec.get('title', '报告')} · {rec.get('date', '')}"
        color = HEADER_COLORS.get(rec.get("rtype", ""), "blue")
        self._last_message_ids = []
        content = _md_for_card(rec.get("content_md", ""))
        if rec.get("content_hash"):
            version = rec.get("plan_version") or rec.get("report_version") or 1
            content += f"\n\n---\n内容版本：v{version} · {str(rec['content_hash'])[:12]}"
        chunks = _split_by_bytes(content, CHUNK_BYTES)[:3]
        total = len(chunks)
        target = "feishu_home" if self._bot_ready() and not self.config.feishu_webhook else "feishu_webhook"
        target_results = []
        for i, chunk in enumerate(chunks):
            suffix = f"（{i + 1}/{total}）" if total > 1 else ""
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": color,
                    "title": {"tag": "plain_text", "content": f"{title.strip()}{suffix}"},
                },
                "elements": [{"tag": "markdown", "content": chunk}],
            }
            before = len(self._last_message_ids)
            ok = await self._send({"msg_type": "interactive", "card": card})
            message_id = self._last_message_ids[-1] if len(self._last_message_ids) > before else None
            target_results.append({
                "target": f"{target}:chunk:{i + 1}",
                "status": "accepted" if ok else ("unknown" if self._last_send_ambiguous else "failed_terminal"),
                "message_id": message_id,
            })
        accepted = sum(row["status"] == "accepted" for row in target_results)
        ambiguous = any(row["status"] == "unknown" for row in target_results)
        status = (
            "accepted" if target_results and accepted == len(target_results)
            else "partial" if accepted
            else "unknown" if ambiguous
            else "failed_terminal"
        )
        return {
            "ok": status == "accepted",
            "status": status,
            "message_id": ",".join(self._last_message_ids) or None,
            "error_code": None if status == "accepted" else "feishu_chunks_incomplete",
            "target_results": target_results,
        }

    async def push_file(self, path: str, caption: str = "") -> tuple[bool, str]:
        """PDF 等附件（与 GatewaySender.push_file 同接口，ext 盘前/盘后 PDF 推送调用）。
        webhook 机器人发不了文件 → 一律走 bot API：im/v1/files 上传 → 发 file 消息。"""
        if not self._bot_ready():
            return False, "飞书应用凭据未配置，无法发送文件附件"
        if not os.path.exists(path):
            return False, f"待推送文件不存在：{path}"

        def _do():
            try:
                from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody  # noqa: PLC0415
                ftype = "pdf" if path.lower().endswith(".pdf") else "stream"
                with open(path, "rb") as f:
                    req = (CreateFileRequest.builder()
                           .request_body(CreateFileRequestBody.builder()
                                         .file_type(ftype)
                                         .file_name(os.path.basename(path))
                                         .file(f)
                                         .build())
                           .build())
                    resp = self._bot_client().im.v1.file.create(req)
                fkey = getattr(getattr(resp, "data", None), "file_key", "") or ""
                if not (resp.success() and fkey):
                    return False, f"文件上传失败: code={getattr(resp, 'code', '?')} msg={getattr(resp, 'msg', '')}"
                self._last_message_ids = []
                results = []
                if caption:
                    caption_ok = self._bot_send_content("text", {"text": caption[:2000]})
                    results.append({
                        "target": "feishu_home:caption",
                        "status": "accepted" if caption_ok else ("unknown" if self._last_send_ambiguous else "failed_terminal"),
                        "message_id": self._last_message_ids[-1] if caption_ok and self._last_message_ids else None,
                    })
                file_ok = self._bot_send_content("file", {"file_key": fkey})
                results.append({
                    "target": "feishu_home:file",
                    "status": "accepted" if file_ok else ("unknown" if self._last_send_ambiguous else "failed_terminal"),
                    "message_id": self._last_message_ids[-1] if file_ok and self._last_message_ids else None,
                })
                accepted = sum(row["status"] == "accepted" for row in results)
                ambiguous = any(row["status"] == "unknown" for row in results)
                status = (
                    "accepted" if accepted == len(results)
                    else "partial" if accepted
                    else "unknown" if ambiguous
                    else "failed_terminal"
                )
                return {
                    "ok": status == "accepted", "status": status,
                    "message": "已推送到飞书" if status == "accepted" else "飞书文件交付未全部成功",
                    "message_id": ",".join(self._last_message_ids) or None,
                    "error_code": None if status == "accepted" else "feishu_file_delivery_incomplete",
                    "target_results": results,
                }
            except Exception as e:  # noqa: BLE001
                log.exception("飞书文件推送异常")
                return False, f"文件推送异常：{e}"

        return await asyncio.to_thread(_do)

    async def push_test(self) -> tuple[bool, str]:
        if not self.configured:
            return False, "飞书未配置：请填写 webhook，或配置应用凭据(app_id/app_secret)后给机器人发一条消息完成绑定"
        ok = await self.push_card(
            f"✅ {self.config.client_name} · 测试消息",
            f"**客户**：{self.config.client_name}（{self.config.client_id}）\n"
            "推送链路正常。定时任务将按计划自动推送：\n"
            "早盘内参 → 尾盘决策定稿 → 收盘复盘 → 次日策略 → 晚间存档",
            "green",
        )
        if ok:
            return True, ""
        if not self.config.feishu_webhook and self._bot_ready() and not load_home_chat(self.config.data_dir):
            return False, "尚未绑定接收会话：请让接收人在飞书里给机器人任发一条消息（如「绑定」），然后重试"
        return False, "推送失败，请检查 webhook 地址与签名密钥（或服务端日志）"
