"""推送传输层 · Hermes 网关投递（GatewaySender）。

不在本 app 里单独配每个平台的 webhook，而是交给 Hermes 网关的 `hermes send`：
- 只承认客户通过 `/sethome` 保存的 HOME_CHANNEL；
- 生产配置只选一个平台（例如 feishu），具体飞书群/私聊由 `/sethome` 决定；
- 容器部署通过受认证的宿主机 bridge 投递，仍只允许发送到 home channel。
不再把「已连接的平台」等同于「允许接收定时报告的目标」，避免微信、飞书、
内部适配器被同时群发。

与 FeishuPusher 同接口（push_report / push_text / push_test / configured），由 main.py 按
config.push_via 二选一装配；调用方（engine/routes）无需区分。仅本机部署可用（云端容器内无网关 CLI）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import httpx

from .report_push_summary import build_report_public_url, build_report_push_summary

log = logging.getLogger("hermes.notify")

_DISCOVER_TIMEOUT = 15
_SEND_TIMEOUT = 30
_COOLDOWN_RE = re.compile(r"cooldown active for\s*([0-9]+(?:\.[0-9]+)?)s", re.I)

# 平台 key → 用户可读名（用于推送结果回显，避免把 weixin/wecom 这类内部 key 直接给客户看）
_PLATFORM_CN = {
    "weixin": "微信", "wecom": "企业微信", "wecom_callback": "企业微信",
    "feishu": "飞书", "dingtalk": "钉钉", "qqbot": "QQ", "telegram": "Telegram",
    "discord": "Discord", "slack": "Slack", "whatsapp": "WhatsApp",
    "signal": "Signal", "email": "邮件", "sms": "短信",
}


def _friendly(targets) -> str:
    """把平台 key 列表转成去重的中文名串，如 ['weixin'] → '微信'。"""
    seen, out = set(), []
    for t in targets:
        cn = _PLATFORM_CN.get(str(t).lower(), str(t))
        if cn not in seen:
            seen.add(cn)
            out.append(cn)
    return "、".join(out)


_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_TBL_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*#*$")


def _md_to_text(md: str) -> str:
    """Markdown → 纯文本（给微信/Telegram 等纯文本平台；它们不渲染 markdown，原样发会满屏 **/#/|）。
    规则：# 标题→【标题】、去 **粗体**/*斜体*/`码`、表格行去竖线分隔符行+单元用｜连、> 引用去标记、
    --- 分隔线→——、[文字](链接)→文字(链接)。其余（- 列表/1. 编号/正文）原样保留，纯文本里本就好读。"""
    if not md:
        return ""
    out: list[str] = []
    for raw in md.split("\n"):
        ln = raw.rstrip()
        if _HR_RE.match(ln):                       # 水平分隔线
            out.append("————————")
            continue
        if "|" in ln and _TBL_SEP_RE.match(ln):    # 表格分隔行 |---|---| 直接丢
            continue
        m = _HEADING_RE.match(ln)
        if m:                                       # # 标题 → 【标题】
            ln = f"【{m.group(2).strip()}】"
        elif ln.lstrip().startswith("|"):           # 表格数据行：去首尾 | ，单元用 ｜ 连
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            ln = " ｜ ".join(c for c in cells if c)
        ln = re.sub(r"^\s*>\s?", "", ln)            # 引用 >
        ln = re.sub(r"\*\*(.+?)\*\*", r"\1", ln)    # **粗体**
        ln = re.sub(r"__(.+?)__", r"\1", ln)        # __粗体__
        ln = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", ln)  # *斜体*
        ln = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1(\2)", ln)  # [文字](链接)
        ln = ln.replace("`", "")                    # 行内 code
        out.append(ln)
    return "\n".join(out)


class GatewaySender:
    def __init__(self, config):
        self.config = config
        self._cli = config.push_gateway_cli                      # …/.hermes/hermes-agent/venv/bin/python
        # 上溯：python(venv/bin) → venv → hermes-agent(代码/cwd) → .hermes(网关 home，存 .env)
        self._agent_dir = os.path.dirname(os.path.dirname(os.path.dirname(self._cli)))
        self._hermes_home = os.path.dirname(self._agent_dir)
        self._bridge_url = str(getattr(config, "push_gateway_bridge_url", "") or "").strip()
        self._bridge_token = str(getattr(config, "push_gateway_bridge_token", "") or "").strip()
        # 报告与 PDF 可能在相邻分钟同时完成；同一网关串行投递，避免微信
        # iLink 把两个并发发送都打入 30 秒冷却。
        self._send_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        # 本机 CLI 与容器 bridge 二选一。bridge 没有 token 时必须视为
        # 未配置，不能把一个裸 HTTP 地址误报成可用投递链路。
        if self._bridge_url:
            return bool(self._bridge_token)
        return bool(self._cli) and os.path.exists(self._cli)

    async def _run(self, args: list[str], stdin_text: str | None = None, timeout: int = _SEND_TIMEOUT):
        """调 `<cli> -m hermes_cli.main <args>`，返回 (rc, stdout, stderr)。失败不抛。"""
        cwd = self._agent_dir if os.path.isdir(self._agent_dir) else None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli, "-m", "hermes_cli.main", *args,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
            )
            out, err = await asyncio.wait_for(
                proc.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None),
                timeout=timeout,
            )
            return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
            log.warning("hermes send 调用失败: %s", e)
            return 1, "", str(e)

    async def _bridge_request(self, plat: str, body: str = "", *, probe: bool = False) -> tuple[bool, str, dict]:
        """调用宿主机 home-channel bridge；从不接收任意 chat_id。"""
        if not self._bridge_url or not self._bridge_token:
            return False, "delivery bridge is not configured", {}
        try:
            async with httpx.AsyncClient(timeout=_SEND_TIMEOUT, trust_env=False) as client:
                response = await client.post(
                    self._bridge_url,
                    headers={"Authorization": f"Bearer {self._bridge_token}"},
                    json={"platform": plat, "body": body, "probe": probe},
                )
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            ok = response.status_code == 200 and bool(payload.get("ok"))
            detail = str(payload.get("error") or payload.get("message") or "").strip()[:200]
            if not ok and not detail:
                detail = f"delivery bridge HTTP {response.status_code}"
            return ok, detail, payload if isinstance(payload, dict) else {}
        except (httpx.HTTPError, OSError) as exc:
            return False, f"delivery bridge unavailable: {type(exc).__name__}", {}

    async def discover(self) -> list[str]:
        """返回真正设置了 `/sethome` 的平台，不再返回所有已连接平台。"""
        if self._bridge_url:
            requested = [
                str(item).strip().lower()
                for item in (getattr(self.config, "push_gateway_targets", []) or [])
                if str(item).strip().lower() in _PLATFORM_CN
            ]
            ready: list[str] = []
            for plat in dict.fromkeys(requested):
                ok, _, _ = await self._bridge_request(plat, probe=True)
                if ok:
                    ready.append(plat)
            return ready
        found: list[str] = []
        # 扫网关 .env 的 *_HOME_CHANNEL（仅取键名推断平台，不读目标值）。
        # `/sethome` 会持久化到这里；仅仅出现在 channel_directory 不算授权。
        env_path = os.path.join(self._hermes_home, ".env")  # …/.hermes/.env
        if not os.path.exists(env_path):
            env_path = os.path.expanduser("~/.hermes/.env")  # 自定义 cli 路径时的兜底
        try:
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("#") or "_HOME_CHANNEL" not in line or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.endswith("_HOME_CHANNEL") and val.strip().strip("'\""):
                    plat = key[: -len("_HOME_CHANNEL")].lower()
                    # HOME_CHANNEL 也可能属于内部机器人/插件配置；只有产品
                    # 明确认识的消息平台才可作为出站目标。否则 hermes send
                    # 会返回「No live adapter」，并把整个多目标任务拖成 partial。
                    if plat and plat in _PLATFORM_CN and plat not in found:
                        found.append(plat)
        except OSError:
            pass
        return list(dict.fromkeys(found))

    async def _targets(self) -> list[str]:
        explicit = [
            str(item).strip().lower()
            for item in (getattr(self.config, "push_gateway_targets", []) or [])
            if str(item).strip().lower() in _PLATFORM_CN
        ]
        if explicit:
            return list(dict.fromkeys(explicit))
        homes = await self.discover()
        # 无显式平台时只接受唯一 home channel。多个主频道不是“全发”，
        # 而是配置歧义，必须由客户/交付方选定一个平台。
        return homes if len(homes) == 1 else []

    async def preflight(self) -> dict:
        targets = await self._targets() if self.configured else []
        ready_targets: list[str] = []
        if self.configured:
            if self._bridge_url:
                for plat in targets:
                    ok, _, _ = await self._bridge_request(plat, probe=True)
                    if ok:
                        ready_targets.append(plat)
            else:
                homes = set(await self.discover())
                ready_targets = [plat for plat in targets if plat in homes]
        homes = await self.discover() if self.configured and not targets else []
        blocker = None
        if not ready_targets:
            blocker = "push_primary_channel_ambiguous" if len(homes) > 1 else "push_target_unconfigured"
        return {
            "configured": self.configured,
            "targets": list(ready_targets),
            "ready": bool(self.configured and ready_targets),
            "mode": "gateway",
            "home_channel_only": True,
            "blocker": blocker,
        }

    async def _deliver(self, plat: str, body: str) -> tuple[bool, str]:
        """单次投递到某平台，返回 (成功, 错误信息)。解析 --json 的 error 字段。"""
        if self._bridge_url:
            ok, detail, _ = await self._bridge_request(plat, body)
            return ok, detail
        rc, out, err = await self._run(["send", "--to", plat, "--json"], stdin_text=body)
        ok = rc == 0
        msg = (err or out or "").strip()[:200]
        if ok:
            try:
                j = json.loads(out)
                if j.get("error"):
                    ok, msg = False, str(j.get("error"))[:200]
            except json.JSONDecodeError:
                pass
        return ok, msg

    async def _deliver_retry(self, plat: str, body: str,
                             attempts: int = 3, backoffs=(3, 10)) -> tuple[bool, str]:
        """带退避重试的投递：吸收网关瞬时抖动/会话过期/短时限频（降低按时推送失败率）。
        backoffs 取较长间隔以越过 iLink 30s 量级的短冷却。"""
        last = ""
        for i in range(attempts):
            ok, msg = await self._deliver(plat, body)
            if ok:
                if i:
                    log.info("推送到 %s 第 %d 次重试成功", plat, i)
                return True, ""
            last = msg
            log.warning("推送到 %s 第 %d/%d 次失败: %s", plat, i + 1, attempts, msg)
            if "timed out" in str(msg).lower() or "timeout" in str(msg).lower():
                # 网关超时时对端可能已接收，不在传输层盲重发。
                return False, "ambiguous timeout; manual review required"
            if i < attempts - 1:
                match = _COOLDOWN_RE.search(str(msg))
                if match:
                    delay = min(35.0, max(1.0, float(match.group(1)) + 0.5))
                else:
                    delay = backoffs[min(i, len(backoffs) - 1)]
                await asyncio.sleep(delay)
        return False, last

    async def _push_body_result(
        self,
        body: str,
        *,
        target_bodies: dict[str, str] | None = None,
    ) -> dict:
        async with self._send_lock:
            targets = await self._targets()
            if not targets:
                log.warning("网关推送：未发现任何已连平台，跳过（请先在 Hermes 网关连接微信/企业微信/飞书等）")
                return {
                    "ok": False, "status": "failed_terminal",
                    "error": "no delivery target", "error_code": "push_target_unconfigured",
                    "target_results": [],
                }
            results = []
            for plat in targets:
                target_body = (target_bodies or {}).get(plat, body)
                ok, detail = await self._deliver_retry(plat, target_body)
                ambiguous = "ambiguous timeout" in str(detail).lower()
                results.append({
                    "target": plat,
                    "status": "unknown" if ambiguous else ("accepted" if ok else "failed_terminal"),
                    "error": detail if not ok else None,
                })
        successes = sum(row["status"] == "accepted" for row in results)
        ambiguous = any(row["status"] == "unknown" for row in results)
        status = (
            "accepted" if successes == len(results)
            else "partial" if successes
            else "unknown" if ambiguous
            else "failed_terminal"
        )
        return {
            "ok": status == "accepted",
            "status": status,
            "error": None if status == "accepted" else "one or more delivery targets were not accepted",
            "error_code": None if status == "accepted" else "delivery_targets_incomplete",
            "target_results": results,
        }

    async def push_card(self, title: str, md: str, color: str = "blue") -> bool:
        text = _md_to_text(md)                      # markdown→纯文本：微信等不渲染 markdown，避免满屏 **/#
        body = f"{title}\n\n{text}" if title else text
        return bool((await self._push_body_result(body))["ok"])

    async def push_text(self, text: str) -> bool:
        return await self.push_card("", text)

    async def push_file(self, path: str, caption: str = ""):
        """把本地文件（如生成好的 PDF）作为附件投递到已连平台。
        复用 `hermes send` 的 `MEDIA:<path>` 语法发送文档/图片附件。返回 (ok, 可读结果)。"""
        if not self.configured:
            return False, f"找不到本机推送程序（{self._cli}）；请确认消息服务已安装，或在 config.push.gateway.cli 指定路径"
        if not os.path.exists(path):
            return False, f"待推送文件不存在：{path}"
        targets = await self._targets()
        if not targets:
            return False, "尚未连接任何通讯平台。请先连好微信/企业微信等，软件会自动识别"
        # MEDIA:<path> 发附件；caption 作为随附文字（部分平台附件不支持长文，放 caption 行）
        body = (f"{caption}\n" if caption else "") + f"MEDIA:{path}"
        result = await self._push_body_result(body)
        result["message"] = (
            "已推送到：" + _friendly(targets)
            if result["ok"] else f"投递未全部成功（目标：{_friendly(targets)}）"
        )
        return result

    async def push_report(self, meta: dict, rec: dict) -> dict:
        # 非飞书目标保持原报告正文；经 Hermes 网关投递到飞书时与直发路径一致，只发关键摘要。
        title = f"{meta.get('emoji', '')} {self.config.client_name}·{rec.get('title', '报告')} · {rec.get('date', '')}".strip()
        text = _md_to_text(rec.get("content_md", ""))
        if rec.get("content_hash"):
            version = rec.get("plan_version") or rec.get("report_version") or 1
            text += f"\n\n内容版本：v{version} · {str(rec['content_hash'])[:12]}"
        body = f"{title}\n\n{text}" if title else text
        summary_title = f"{title} · 关键摘要" if title else "报告关键摘要"
        summary_text = _md_to_text(build_report_push_summary(rec))
        summary_body = f"{summary_title}\n\n{summary_text}"
        report_url = build_report_public_url(
            rec, getattr(self.config, "public_base_url", "")
        )
        if report_url:
            summary_body += f"\n\n查看完整报告：{report_url}"
        return await self._push_body_result(body, target_bodies={"feishu": summary_body})

    async def push_test(self) -> tuple[bool, str]:
        if not self.configured:
            return False, f"找不到本机推送程序（{self._cli}）；请确认消息服务已安装，或在 config.push.gateway.cli 指定路径"
        targets = await self._targets()
        if not targets:
            return False, "尚未连接任何通讯平台。请先连好微信/企业微信等，软件会自动识别"
        ok = await self.push_card(
            f"✅ {self.config.client_name} · 测试消息",
            "推送链路正常。定时任务将按计划自动推送到：" + _friendly(targets),
        )
        return (ok, "已推送到：" + _friendly(targets)) if ok else (False, f"投递失败（目标：{_friendly(targets)}），详见服务端日志")
