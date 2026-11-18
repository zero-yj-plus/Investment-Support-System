# Hermes Trader

可本地部署的 A 股研究与决策支持工作台，使用 FastAPI、Vue 3 和 TypeScript 构建。

项目聚合行情与金融数据，提供个股诊断、持仓风险评估、条件筛选、研究报告和本地模拟交易。分析与风控规则在服务端计算，取不到的数据会显示缺失或降级状态。AI 可用于问答和报告解释，需要自行配置模型连接。

`W.U.D.I.` 是示例配置中的显示品牌。实例名称和品牌可通过 `config.yaml` 的 `client` 段调整。

## 功能

- 多源行情与数据质量展示，支持可选的本地通达信只读桥接。
- 个股诊断、技术面筛选、ETF 观察和市场研究。
- 持仓风险评估、观察池与研究报告留存。
- 本地模拟订单与成交记录。
- 可配置的模型连接、飞书报告推送和只读 MCP 工具。

## 从源码启动

准备 64 位 Python 3.10+、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Node.js 22.12+。以下命令在仓库根目录执行。

### macOS / Linux

复制配置并构建前端。

```bash
cp config.example.yaml config.yaml
cp .env.example .env
chmod 600 config.yaml .env

cd frontend-vue
npm ci
npm run build
cd ..
```

编辑 `config.yaml`，将 `data_source.deployment_profile` 设置为实际部署环境。

| 配置值 | 部署环境 |
| --- | --- |
| `cn_home` | 中国大陆家庭电脑 |
| `cn_server` | 中国大陆服务器 |
| `overseas_server` | 中国大陆以外的服务器 |

按需在 `.env` 中填写模型、数据源或飞书凭据，然后启动服务。

```bash
bash run.sh
```

`run.sh` 使用现有 `.venv`，或通过 uv 加载 `requirements.txt` 中的依赖，同时启动 API 与后台 Worker。首次运行需要下载 Python 依赖。使用 Ctrl+C 结束前台进程。

浏览器打开 [本地工作台](http://127.0.0.1:18869)。

### Windows

使用命令提示符复制配置并构建前端。

```bat
copy config.example.yaml config.yaml
copy .env.example .env
cd frontend-vue
npm ci
npm run build
cd ..
```

按上面的部署环境说明编辑配置，再运行安装和启动脚本。

```bat
install.bat
start.bat --foreground
```

`install.bat` 创建 Python 虚拟环境并安装依赖。`start.bat --foreground` 启动 API 与后台 Worker，日志写入 `data\logs\`。浏览器访问同一个本地工作台地址，运行 `check.bat` 检查服务。

由 `package-windows.ps1` 生成的部署包自带前端构建产物，使用该部署包时可直接从安装和启动脚本开始。升级与回滚由 `upgrade.bat` 和对应发布包配合完成。

## 配置

| 文件或配置项 | 用途 |
| --- | --- |
| `config.example.yaml` | 服务、实例、数据源和任务配置模板 |
| `.env.example` | 凭据及外部服务地址模板 |
| `client.name` / `client.id` | 实例显示名称与稳定标识 |
| `client.brand` / `client.brand_sub` | 工作台主品牌与副标题 |
| `server.host` / `server.port` | 服务监听地址，默认 `127.0.0.1:18869` |
| `server.data_dir` | 运行数据目录，默认 `./data` |

真实凭据保存在本地 `.env` 中。配置示例使用空值或环境变量引用。`.env`、`config.yaml`、运行数据和数据库已加入 `.gitignore`。

模型可通过设置页管理，也可配置 `hermes` 段连接 OpenAI 兼容接口。默认网关地址为 `http://127.0.0.1:8874/v1`。启用 AI 功能前，需要配置可用的模型服务。

如使用 Windows 本地通达信，先配置并启动只读桥接服务，再启动工作台。桥接密钥应由部署者生成，并在客户端和桥接服务中设置一致。详细步骤见 [TQ 桥接说明](docs/TQ_TDX_BRIDGE.md) 和 [Windows 登录守护说明](docs/WINDOWS_TDX_LOGIN_GUARD.md)。

## 开发与测试

启动后端后，在另一个终端运行前端开发服务器。

```bash
cd frontend-vue
npm ci
npm run dev
```

开发服务器位于 [localhost:5173](http://127.0.0.1:5173)，Vite 将 `/api` 代理到本地 `18869` 端口。生产构建由 FastAPI 从 `frontend-vue/dist/` 提供。

前端构建包含 TypeScript 类型检查。

```bash
cd frontend-vue
npm run build
```

在仓库根目录运行后端与契约测试。

```bash
uv run --with-requirements requirements-dev.txt python -m pytest -q
```

UI 审计脚本位于 `frontend-vue/scripts/`，需要已启动的工作台与本机浏览器环境。具体命令见 `frontend-vue/package.json`。

## 代码结构

| 目录 | 内容 |
| --- | --- |
| `hermes_trader/` | FastAPI 服务、数据适配、确定性分析、调度与模型网关 |
| `frontend-vue/` | Vue 3 正式工作台 |
| `frontend/` | 旧版前端，启用 `ui.legacy_enabled` 后可通过 `/legacy/` 访问 |
| `scripts/` | 数据探测、部署、备份恢复与验收工具 |
| `tests/` | 后端、接口与前端契约测试 |
| `docs/` | API 契约与集成说明 |
| `design-system/` | 工作台视觉与组件规范 |

架构、模块职责和阅读顺序见 [项目导读](PROJECT_GUIDE.md)。接口字段与降级语义见 [API 契约](docs/API_CONTRACT.md)。
