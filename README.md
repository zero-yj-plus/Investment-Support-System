# W.U.D.I. A 股决策支持源码包

本包是母版当前工作树的隔离源码快照，不含任何现有客户目录、客户配置、运行数据、历史报告、日志、数据库、密钥或 Git 历史。

## 包含内容

- `hermes_trader/`：FastAPI 后端、确定性分析、数据契约、任务与可选 AI 网关。
- `frontend/`：原生 JavaScript SPA。
- `frontend-vue/`：Vue 3 + TypeScript 决策总览重构试验；尚未替换生产静态前端。
- `design-system/`：重构页面的设计令牌、组件规则和页面级约束。
- `scripts/`：数据探测、备份恢复、受限模型网关与生产验收脚本。
- `tests/`：不依赖任何现有客户扩展的母版测试。
- `docs/`：通用 API、MCP 与审计说明。
- `config.example.yaml`、`.env.example`：无凭据的中性启动模板。

本产品不接券商自动交易。行情、分析和风险闸门可独立于 AI 运行；AI 仅作可选解释增强。

## 快速启动

```bash
cp config.example.yaml config.yaml
cp .env.example .env
chmod 600 config.yaml .env
./run.sh
```

默认访问地址：`http://127.0.0.1:18869`。

### Vue 决策总览（并行试验）

先启动 FastAPI 后端，再在另一个终端运行：

```bash
cd frontend-vue
npm install
npm run dev
```

开发地址为 `http://127.0.0.1:5173`，Vite 会把 `/api` 代理到
`http://127.0.0.1:18869`。该目录当前用于逐页迁移和验收，不改变生产入口 `frontend/`。

```bash
npm run build
npm run audit:ui
```

### Windows 本地通达信 TQ 桥接

如果本机通达信已开启 TQ HTTP 服务 `http://127.0.0.1:17709/`，可运行独立的
只读兼容桥，让现有 TDX API 适配器复用本地实时行情、股票池、板块关系和基础财务，
无需修改业务数据链路。配置、启动和能力边界见
[`docs/TQ_TDX_BRIDGE.md`](docs/TQ_TDX_BRIDGE.md)。

部署前必须按真实位置把 `data_source.deployment_profile` 明确设置为：

- `cn_home`：国内家庭机。
- `cn_server`：国内服务器。
- `overseas_server`：海外服务器；需要境内中继时再填写中继变量。

不要通过公网 IP 自动猜测地域。

## 测试

```bash
uv run --with pytest --with pytest-asyncio \
  --with-requirements requirements.txt \
  --with-requirements requirements-dev.txt \
  python -m pytest -q
```

文件完整性使用 `RELEASE_FILES.sha256` 校验；压缩包校验值见同目录的 `.sha256` 文件。



$env:TQ_BRIDGE_API_KEY='11111111'\
.\.venv\Scripts\python.exe .\scripts\tq_tdx_bridge.py




npm --prefix .\frontend-vue run build

.\.venv\Scripts\python.exe -m hermes_trader --config .\config.yaml