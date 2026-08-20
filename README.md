# Hermes Trader 白标 A 股决策支持源码包

Hermes Trader 是共享产品内核，`W.U.D.I.` 是示例配置中的默认客户品牌。部署给其他客户时只修改 `config.yaml` 的 `client` 段，不重命名 Python 包、不修改前端源码或业务逻辑。

本包是母版当前工作树的隔离源码快照，不含任何现有客户目录、客户配置、运行数据、历史报告、日志、数据库、密钥或 Git 历史。

## 包含内容

- `hermes_trader/`：FastAPI 后端、确定性分析、数据契约、任务与可选 AI 网关。
- `frontend-vue/`：Vue 3 + TypeScript 正式工作台；构建产物是默认生产入口。
- `frontend/`：默认关闭的原生 JavaScript 旧版对照，仅在 `ui.legacy_enabled: true` 时挂载到 `/legacy/`。
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

### Windows 部署包

Windows 部署包已包含 `frontend-vue/dist/`，目标机不需要安装 Node.js 或运行 npm。
使用 64 位 Python 3.10+，在命令提示符或 PowerShell 中依次运行：

```bat
install.bat
start.bat
```

`start.bat` 默认同时启动 API 进程和独立重型后台 Worker，不保留 CMD 窗口；输出分别写入
`data\logs\hermes-start-*.log` 与 `data\logs\hermes-worker-start-*.log`。技术全市场刷新和
新闻观察只在 Worker 中执行。需要在当前窗口查看实时输出并用 Ctrl+C 同时停止两者时，运行
`start.bat --foreground`。

如使用本机通达信 TQ HTTP 服务，先在 `.env` 中配置桥接密钥和地址，再在独立
后台运行 `start-tdx-bridge.bat`，其日志写入 `data\logs\tdx-bridge-start-*.log`；
需要常驻窗口时运行 `start-tdx-bridge.bat --foreground`。服务启动后运行 `check.bat`
检查首页、核心 API 和已配置的本地桥。

已有 Windows 客户升级时，把新版部署 ZIP、同名 `.sha256` 和打包时生成的
`WUDI-版本-Windows-upgrade-时间.bat` 放到原安装目录，双击升级 BAT。升级器
会从已校验的新版 ZIP 中加载最新升级助手，只替换程序、预构建前端和部署文件，保留现有
`config.yaml`、`.env`、`data/` 与 `.venv`；它会先校验压缩包、停止属于当前
安装目录的服务、备份客户状态，升级后执行 HTTP 健康检查，失败则恢复旧程序。
如目录中有多个部署 ZIP，应显式指定新版文件：

```bat
upgrade.bat WUDI-1.5.1-Windows-deploy-YYYYMMDD-HHMMSS.zip
```

### 客户品牌配置

```yaml
client:
  name: "客户甲默认实例"       # 实例名称、报告和推送署名
  id: "customer-a"            # 稳定客户标识，不使用中文或显示名
  brand: "客户甲投研"          # 页面主品牌
  brand_sub: "A 股决策支持"    # 页面品牌副标题
```

`brand` 和 `brand_sub` 可按客户修改；`hermes_trader` 是稳定的内部包名，不随客户重命名。浏览器标题和 Vue 侧栏从 `/api/shell-status` 读取品牌配置；接口不可用时回退为通用内核名 `Hermes Trader`。

### Vue 正式工作台开发

先启动 FastAPI 后端，再在另一个终端运行：

```bash
cd frontend-vue
npm install
npm run dev
```

开发地址为 `http://127.0.0.1:5173`，Vite 会把 `/api` 代理到
`http://127.0.0.1:18869`。正式部署由 FastAPI 在 `/` 挂载 `frontend-vue/dist/`。

```bash
npm run build
npm run audit:ui
```

### Windows 本地通达信 TQ 桥接

如果本机通达信已开启 TQ HTTP 服务 `http://127.0.0.1:17709/`，可运行独立的
只读兼容桥，让现有 TDX API 适配器复用本地实时行情、股票池、板块关系和基础财务，
无需修改业务数据链路。配置、启动和能力边界见
[`docs/TQ_TDX_BRIDGE.md`](docs/TQ_TDX_BRIDGE.md)。

全 A 股身份主名录使用 TDX `instruments_a`；只有完成全部 cursor 分页、通过沪深京覆盖
校验并写入当前权威缓存后，系统才会把未命中代码判定为不存在。旧缓存和内置启动快照
只作降级展示，不具有负向身份判定权。

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
