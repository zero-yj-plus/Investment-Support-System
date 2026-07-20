# MCP 只读能力与权限边界

`hermes_trader.mcp_server` 是 Agent 查询头，不是业务管理后台。工具只包装已存在的数据层、确定性分析引擎和本地审计账本，不在 MCP 内发明新数据口径。

## 默认权限

- 只读查询：市场、资金、公告、新闻观察、报告 lineage、任务状态、交付回执。
- 不注册：设置修改、连接删除、对外发送、推荐写入/审批、交易执行。
- 唯一历史兼容写入是 `capital_snapshot`：默认禁用，只能通过客户配置 `mcp.allow_write_tools=true` 显式开启，且仅追加资金快照。
- `mcp_capabilities` 会返回当前实际注册工具、可选维护写入的开关状态，以及四类明确禁止权限。

## 本次补齐的只读工具

| 工具 | 真实来源 | 读取边界 |
| --- | --- | --- |
| `news_event_history` | `news_events/events.v1.jsonl` + `observations.v1.jsonl` | 不刷新新闻，不请求行情，不推断因果 |
| `report_lineage` | `Store.get_report()` 定稿元数据 | 只返回内容哈希、版本、上游报告和证据索引；不返回正文/本地路径 |
| `daily_review_evidence` | 收盘报告 lineage + 新闻观察对账 | 不做历史行情回放，缺失就明示缺失 |
| `task_status` | `jobs_state.json` | MCP 进程不伪造 APScheduler 注册状态或下次运行时间，不返回原始异常文本 |
| `delivery_receipts` | `delivery_receipts/receipts.v1.jsonl` | 只返回脱敏元数据；不返回正文、目标、凭据，不提供重试/确认/发送 |
| `mcp_capabilities` | FastMCP 实际注册表 + 运行时写工具开关 | 权限自描述，不改变任何状态 |

`company_announcements` 与 `official_longhu` 已经分别连到官方公告 adapter 和指定交易日官方快照缓存；前者不代理 PDF 全文，后者不在 Agent 请求链发起网络访问。

## 诚实降级

- 账本未初始化时返回 `observation_ledger_not_found`，不会因查询创建观察记录。
- 报告不存在时返回 `report_not_found`，不会用其他交易日回填。
- 任务状态只能证明最近一次持久化结果；`registration_state=unknown_in_mcp_process` 明示 MCP 进程看不到 Web 进程的实时调度器。
- 所有新工具异常都转换为结构化错误，不向 MCP 返回 URL、token 或本地路径。
