# 本地通达信 TQ 只读兼容桥

本桥把通达信客户端提供的 `POST http://127.0.0.1:17709/` TQ 服务转换为项目现有
`TDX Data API v2.8` 的只读子集。业务源码仍通过 `TDXAPI_BASE/TDXAPI_KEY` 访问，
无需修改 `hermes_trader` 数据链路。

## 能力边界

支持：

- 实时行情与盘口：`/v1/realtime/quotes`
- 股票名录：`instruments_a`
- 板块目录与成分：`sectors`、`sector_members`
- 股票行业/概念关系：`relation`
- 基础财务：`stock_info`
- 全市场价格和成交量：`pricevol`
- 健康检查：`/v1/meta/health`、`/v1/realtime/status`

显式不支持并返回 HTTP `501`：板块资金排名、新闻舆情、机构一致预期、十大股东。
账户、资产、持仓、委托、下单、撤单、公式执行和客户端控制方法不在转发白名单中。

## 配置

先启动支持 TQ 的通达信客户端，并确认它监听 `127.0.0.1:17709`。然后为桥和项目
设置相同的本地密钥，例如在 PowerShell 当前窗口中：

```powershell
$env:TQ_HTTP_BASE='http://127.0.0.1:17709'
$env:TQ_BRIDGE_HOST='127.0.0.1'
$env:TQ_BRIDGE_PORT='18879'
$env:TQ_BRIDGE_API_KEY='请替换为随机长字符串'
$env:TDXAPI_BASE='http://127.0.0.1:18879'
$env:TDXAPI_KEY=$env:TQ_BRIDGE_API_KEY
```

也可以把同名变量放入项目 `.env`。兼容桥拒绝无密钥启动，也拒绝绑定到非 loopback
地址；不要把端口暴露到局域网或公网。

## 启动与检查

```bat
start-tdx-bridge.bat
```

另开一个 PowerShell 检查：

```powershell
$headers=@{'X-API-Key'=$env:TQ_BRIDGE_API_KEY}
Invoke-RestMethod 'http://127.0.0.1:18879/v1/meta/health' -Headers $headers
Invoke-RestMethod 'http://127.0.0.1:18879/v1/realtime/quotes?codes=000001.SZ' -Headers $headers
```

项目仍按原方式启动。若 TQ 客户端退出或登录失效，桥会返回结构化上游错误，业务层按
既有降级链路使用腾讯等公开源或明确显示缺失，不会以零值伪装数据。

## Windows 登录守护（可选）

通达信可能主动结束行情会话并回到登录界面。仓库提供可选的当前用户守护组件：

```powershell
.\scripts\tdx-login-guard\install-tdx-login-guard.ps1 -TdxPath 'D:\tdx\TdxW.exe'
```

安装器把守护程序和独立配置复制到当前用户的
`%LOCALAPPDATA%\HermesTrader\TdxLoginGuard`，并注册仅在该用户交互式登录时运行的
计划任务。默认 `recovery_mode=close_dialog` 不保存凭据，只关闭已校准的超时登录页。
需要完整自动登录时，先运行安装目录中的凭据设置脚本：

```powershell
$root=Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'
pwsh.exe -NoProfile -File "$root\set-tdx-login-credential.ps1"
```

凭据文件使用当前 Windows 用户 DPAPI 加密。将配置切换为 `login_then_close` 后，守护按
已校准控件填写账号密码，单次恢复最多提交一次。连续探测失败和操作冷却可以避免短暂
断线触发重复操作。恢复成功必须同时满足 TQ `get_market_snapshot` 返回 `ErrorId=0`；
配置了本地桥密钥时还会检查桥健康和一只股票的实时行情。

守护只在同一运行周期内先观察到健康会话、随后发现会话失效且 `TdxW` 进程仍存在时
执行关闭动作。默认 `start_tdx_if_missing=false`；只有显式启用账号密码自动登录后才应
改为 `true`。本机 V7.73 已校准账号控件 `1443`、密码控件 `1058`。自动登录成功后的
20 秒内，若同一 TdxW 进程出现 `#32770` 登录/扫码对话框，守护会关闭它；正常主界面
`TdxW_MainFrame_Class` 不在关闭范围内。

首次安装或迁移到新机器后，先在登录页运行只读控件扫描：

```powershell
$root=Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'
pwsh.exe -NoProfile -File "$root\tdx-login-guard.ps1" `
  -Config "$root\tdx-login-guard.json" -Inspect
```

迁移到新电脑或更换 Windows 用户后，DPAPI 密文不能复用，必须重新运行凭据设置脚本；
客户端版本变化时也要重新校准控件。UI 操作要求 Windows 用户会话保持登录，不能作为
Session 0 Windows 服务运行。卸载计划任务但保留日志：

```powershell
.\scripts\tdx-login-guard\uninstall-tdx-login-guard.ps1
```

## 单位与刷新

- TQ 实时成交量（股）在桥内转换为 TDX 契约的手，现有适配器再统一为股。
- TQ 实时成交额（元）在桥内转换为 TDX 契约的万元，现有适配器再统一为元。
- 股票/板块目录缓存 6 小时，`pricevol` 缓存 45 秒。实时行情使用有界后台刷新：
  `wait=0` 立即返回新鲜缓存或 `pending`，不会同步等待 TQ；`wait>0` 最多等待指定的
  整体秒数。缓存达到 `max_age` 一半时会提前刷新，避免正常轮询周期出现断档。
- 单次实时行情最多 300 只、股票关系最多 120 只、板块成分最多 40 个板块。超过
  4 只的实时行情批次使用 TQ `get_pricevol` 批量读取基础价量；小批次使用
  `get_market_snapshot` 补充盘口等详细字段。

官方 HTTP 请求格式为 `{"id":1,"method":"方法名","params":{...}}`。桥只会生成固定
只读方法，不接受调用方传入任意 TQ 方法名。
