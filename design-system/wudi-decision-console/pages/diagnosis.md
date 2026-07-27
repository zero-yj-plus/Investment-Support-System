# 个股诊断页规则

> 本页继承 `../MASTER.md`。仅记录个股诊断的页面级信息架构与状态规则。

## 用户任务与信息顺序

1. 输入并核验 A 股代码，明确当前诊断标的与数据时点。
2. 首屏展示服务端给出的确定性结论、唯一主动作及其适用边界。
3. 紧接展示风险、仓位边界、禁止条件和关键价格位；前端不得重算。
4. 再展示行情、技术、题材、资金等结构化证据及各自完整度。
5. 深度解释和明细后置；数据健康、版本与治理说明置于末尾。

## 布局与交互

- 页面采用“查询区 → 决策摘要 → 风险边界 → 证据模块 → 深度解释 → 数据治理”的单向阅读路径。
- 桌面端证据可分栏，低于 900px 必须按阅读优先级转为单列；任何卡片使用 `min-width: 0` 防止页面级横向滚动。
- 代码输入保留可见标签、格式提示和就近错误；查询与深度诊断使用原生 `<button>`，最小 44×44px。
- 刷新、快速诊断、深度诊断均防重复提交，并以 `aria-live="polite"` 反馈阶段与结果。
- 价格、涨跌幅、仓位比例等数字使用等宽字体和 `tabular-nums`；价格单位为元，百分数字段明确显示 `%`。

## 状态与语义

- 初始态不请求无关重型接口，只提示输入股票代码。
- 加载态保留查询上下文；快速与深度请求状态独立，不用全页遮罩。
- `null`/缺失字段显示“— / 暂无数据”，`missing` 显示“数据缺失”，`degraded` 显示“降级数据”，真实空数组显示“暂无记录”，传输失败显示“暂不可用”。
- 结论绑定证据为空时，必须展示 `market_evidence_quality.reason` 的可理解原因。已成功返回的快速行情可作独立核对快照，但必须标注“未绑定证据 ID”，不得冒充结论证据。
- 只要 `decision_market_evidence` 字段存在，就代表服务端已声明冻结快照；即使它为空，也不能用当前 `market_evidence` 自动顶替。
- 旧数据必须显示数据日期或陈旧标识，不得写成“实时”。
- 风险等级、建议动作、仓位上限、止损/压力/支撑等价格位全部以服务端字段为准；缺失时不推断、不补 0。
- 风险色与行情涨跌色分离，所有颜色状态同时提供文字标签。
- 各证据模块独立失败、独立重试，任一接口失败不得清空已成功模块。

## 真实接口与字段口径

### `GET /api/quote/{code}`

- 仅用于快速身份与行情预览，不参与正式动作重算。
- `quote.price`、`prev_close`：元；`change_pct`、`turnover_rate`：百分数；`amount`：元；`volume_ratio`：倍。
- `state=not_found`、`state=quote_unavailable`、`quote=null` 与 HTTP 失败必须分开；报价失败不清空已成功的正式诊断。

### `GET /api/diagnose?code={code}`

- 正式结论：`action`、`signal_action`、`conviction`、`confidence`、`summary`。
- 风险与仓位：`risk_decision.decision/reasons/rule_version`、`position_cap_pct`。服务端显式返回 `0` 时表示新增仓位上限为 0；字段为 `null` 时表示未提供，二者不得混淆。
- 价格条件：`entry_zone`、`stop` 与 `trade_plan`；文字和价位均由服务端返回，前端不解析或重算。
- 结构证据：`analysis_pipeline.stages`、`technical_analysis`、`evidence`。真实空数组显示“暂无证据”，字段缺失显示“未返回”。
- 字段级证据：`market_evidence` 与 `decision_market_evidence`；`unit` 使用 `CNY/percent/ratio/share`，`quality_status` 使用 `ok/degraded/conflict/missing`。
- 证据质量：`market_evidence_quality` / `decision_market_evidence_quality` 的 `captured`、`requested`、`missing_fields`、`reason`。`reason` 已知机器码包括 `market_evidence_timeout`、`diagnosis_data_capacity_busy`、`market_evidence_failed`。
- 时点：`decision_generated_at` 为结论生成时点，`analysis_pipeline.data_as_of` 为分析数据时点；当前证据 ID 与结论证据 ID 不一致时分开展示。
- `missing` 为缺口清单；空数组只表示接口未声明缺口。`analysis_status=partial`、证据质量降级/冲突/缺失、陈旧标记和传输失败分别展示。
- `advice_record` 只表示建议留痕及正式推荐事件创建情况，不代表平台接受或已送达。

## 内容安全与治理

- 深度解释如含 Markdown，只通过安全 Vue 节点解析；禁止未经清洗的 `v-html`。
- 明确标注诊断为决策支持，不连接券商真实下单。
- 深度解释不能覆盖服务端确定性决策卡；证据不足时优先展示不足原因。

## 验收重点

- Hash 路由可直接到达并可从旧研究页语义映射到该页面。
- 覆盖初始、加载、成功、真实空/缺失、降级、陈旧与局部接口失败状态。
- 375px 单列无页面级横向滚动；1440px 深浅主题边界清晰；200% 缩放后仍可查询和阅读核心结论。
- 所有可见交互目标至少 44×44px，键盘焦点可见，支持 `prefers-reduced-motion`。
