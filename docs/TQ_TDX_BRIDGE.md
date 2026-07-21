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

```powershell
py -3 scripts\tq_tdx_bridge.py
```

另开一个 PowerShell 检查：

```powershell
$headers=@{'X-API-Key'=$env:TQ_BRIDGE_API_KEY}
Invoke-RestMethod 'http://127.0.0.1:18879/v1/meta/health' -Headers $headers
Invoke-RestMethod 'http://127.0.0.1:18879/v1/realtime/quotes?codes=000001.SZ' -Headers $headers
```

项目仍按原方式启动。若 TQ 客户端退出或登录失效，桥会返回结构化上游错误，业务层按
既有降级链路使用腾讯等公开源或明确显示缺失，不会以零值伪装数据。

## 单位与刷新

- TQ 实时成交量（股）在桥内转换为 TDX 契约的手，现有适配器再统一为股。
- TQ 实时成交额（元）在桥内转换为 TDX 契约的万元，现有适配器再统一为元。
- 股票/板块目录缓存 6 小时，`pricevol` 缓存 45 秒；实时行情按请求读取。
- 单次实时行情最多 64 只、股票关系最多 120 只、板块成分最多 40 个板块。

官方 HTTP 请求格式为 `{"id":1,"method":"方法名","params":{...}}`。桥只会生成固定
只读方法，不接受调用方传入任意 TQ 方法名。
