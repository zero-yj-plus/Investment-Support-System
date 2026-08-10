# Windows 通达信自动登录守护安装与使用

本文只说明通达信自动启动、自动登录和登录守护，不包含 TQ/TDX 桥、Caddy 或 Hermes Trader 主服务的安装配置。

## 1. 使用条件

- Windows 用户必须保持交互式登录，守护不能在 Session 0 Windows 服务中操作登录界面。
- 通达信程序和守护应使用相同权限等级。推荐取消 `TdxW.exe` 的“以管理员身份运行此程序”。
- 自动登录凭据使用当前 Windows 用户的 DPAPI 加密；更换电脑或 Windows 用户后必须重新设置。
- 更换通达信版本后，必须重新校准账号和密码控件 ID。

## 2. 准备路径

把示例路径替换为本机实际路径：

```powershell
$projectRoot = 'C:\WUDI-1.4.1'
$tdxExe = 'C:\new_tdx64\TdxW.exe'
```

检查通达信和守护安装文件：

```powershell
Test-Path $tdxExe
Test-Path "$projectRoot\scripts\tdx-login-guard\install-tdx-login-guard.ps1"
```

两项都应返回 `True`。如果安装脚本不存在，当前部署包不包含自动登录守护，需要从同版本完整项目复制整个 `scripts\tdx-login-guard` 目录。

## 3. 安装守护

在需要操作通达信界面的 Windows 用户下执行：

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$projectRoot\scripts\tdx-login-guard\install-tdx-login-guard.ps1" `
  -TdxPath $tdxExe
```

安装器会：

- 把守护文件复制到 `%LOCALAPPDATA%\HermesTrader\TdxLoginGuard`；
- 注册当前用户计划任务 `HermesTrader-TdxLoginGuard`；
- 立即启动计划任务；
- 在当前用户以后登录 Windows 时自动启动守护。

默认配置不会保存凭据，也不会在通达信进程不存在时主动启动客户端。

## 4. 保存自动登录凭据

```powershell
$guardRoot = Join-Path $env:LOCALAPPDATA 'HermesTrader\TdxLoginGuard'

pwsh.exe -NoProfile `
  -File "$guardRoot\set-tdx-login-credential.ps1"
```

按提示输入通达信账号和密码。凭据会以当前 Windows 用户的 DPAPI 密文保存在：

```text
%LOCALAPPDATA%\HermesTrader\TdxLoginGuard\tdx-login-credential.clixml
```

检查凭据文件是否存在：

```powershell
Test-Path "$guardRoot\tdx-login-credential.clixml"
```

返回 `True` 只表示密文文件存在，不代表登录控件和权限配置已经正确。

## 5. 启用自动启动和自动登录

打开配置文件：

```powershell
notepad "$guardRoot\tdx-login-guard.json"
```

确认或修改以下字段：

```json
{
  "tdx_exe": "C:\\new_tdx64\\TdxW.exe",
  "recovery_mode": "login_then_close",
  "start_tdx_if_missing": true,
  "username_control_id": 1443,
  "password_control_id": 1058
}
```

字段含义：

- `tdx_exe`：当前 `TdxW.exe` 的绝对路径。
- `recovery_mode=login_then_close`：允许守护填写凭据并提交登录。
- `start_tdx_if_missing=true`：通达信进程不存在时允许守护启动客户端。
- `username_control_id`、`password_control_id`：当前登录页的账号、密码控件 ID。

`1443/1058` 是已经校准的通达信 V7.73 示例。客户端版本或登录页变化后不能盲目沿用。

## 6. 校准登录控件

先停止守护，再手动打开通达信并停留在账号密码登录页面：

```powershell
Stop-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard' -ErrorAction SilentlyContinue
Start-Process $tdxExe
```

运行只读控件扫描：

```powershell
pwsh.exe -NoProfile `
  -File "$guardRoot\tdx-login-guard.ps1" `
  -Config "$guardRoot\tdx-login-guard.json" `
  -Inspect
```

重点查找 `visible=True` 的账号和密码输入控件。V7.73 已验证示例：

```text
id=1058 class=SafeEdit  密码框
id=1443 class=SafeEdit  账号框
```

如果实际扫描结果不同，把新的账号、密码 ID 分别写入 `username_control_id` 和 `password_control_id`。

## 7. 前台测试自动登录

保持登录页可见，在普通、非管理员 PowerShell 中执行一次恢复测试：

```powershell
Stop-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard' -ErrorAction SilentlyContinue

pwsh.exe -NoProfile `
  -File "$guardRoot\tdx-login-guard.ps1" `
  -Config "$guardRoot\tdx-login-guard.json"
```

这里不要增加 `-Inspect` 或 `-Watch`。脚本会直接尝试启动客户端、填写凭据并提交登录。

测试完成后查看日志：

```powershell
Get-Content "$guardRoot\tdx-login-guard.log" -Encoding utf8 -Tail 50
```

前台测试成功后再启动长期守护任务。

## 8. 启动、停止和检查守护

启动：

```powershell
Start-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard'
```

停止当前运行：

```powershell
Stop-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard'
```

停止后下次登录仍会自动运行。需要禁用自动启动时：

```powershell
Disable-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard'
```

重新启用：

```powershell
Enable-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard'
Start-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard'
```

查看任务、通达信进程和日志：

```powershell
Get-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard' |
  Select-Object TaskName, State

Get-Process TdxW -ErrorAction SilentlyContinue

Get-Content "$guardRoot\tdx-login-guard.log" -Encoding utf8 -Tail 50
```

任务显示 `Running` 只表示守护脚本进程存在，不能证明通达信已经成功启动或登录。

## 9. 通达信或项目迁移目录

通达信或项目目录改变后，优先从新项目目录重新运行安装器：

```powershell
$projectRoot = 'C:\新的项目目录'
$tdxExe = 'C:\新的通达信目录\TdxW.exe'

Stop-ScheduledTask -TaskName 'HermesTrader-TdxLoginGuard' -ErrorAction SilentlyContinue

pwsh.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$projectRoot\scripts\tdx-login-guard\install-tdx-login-guard.ps1" `
  -TdxPath $tdxExe
```

安装器会保留已有守护配置和 DPAPI 凭据，同时刷新守护文件、通达信路径和计划任务。

如果新目录中没有安装器，但 `%LOCALAPPDATA%\HermesTrader\TdxLoginGuard` 中已有完整守护，可以直接修改：

```powershell
notepad "$guardRoot\tdx-login-guard.json"
```

把 `tdx_exe` 改为新的绝对路径，然后重新启动计划任务。

## 10. 常见故障

### 通达信没有自动启动

检查：

```powershell
$config = Get-Content "$guardRoot\tdx-login-guard.json" -Raw | ConvertFrom-Json

[pscustomobject]@{
  TdxPath          = $config.tdx_exe
  TdxExists        = Test-Path $config.tdx_exe
  RecoveryMode     = $config.recovery_mode
  StartIfMissing   = $config.start_tdx_if_missing
  CredentialExists = Test-Path $config.credential_file
}
```

需要同时满足：

- `TdxExists=True`
- `RecoveryMode=login_then_close`
- `StartIfMissing=True`
- `CredentialExists=True`

### 自动填写凭据提示“拒绝访问”

通达信与守护的权限等级不一致。取消 `TdxW.exe` 属性中的“以管理员身份运行此程序”，退出通达信后由普通交互用户重新启动。

### 提示账号或密码控件 ID 不匹配

保持实际账号密码登录页可见，重新运行 `-Inspect`。登录窗口正在切换或关闭时也可能产生一次临时不匹配，不能只凭单条日志修改 ID。

### 日志显示乱码

显式使用 UTF-8：

```powershell
Get-Content "$guardRoot\tdx-login-guard.log" -Encoding utf8 -Tail 50
```

### 日志提示 `The property 'Id' cannot be found`

当前守护版本在通达信进程列表恰好为空时存在未处理分支，可能导致守护退出。这不是路径、控件或凭据配置错误。临时处理是重新启动计划任务；正式处理需要更新守护脚本，在读取进程 `.Id` 前处理空列表。

## 11. 卸载守护

从包含卸载脚本的完整项目目录运行：

```powershell
pwsh.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$projectRoot\scripts\tdx-login-guard\uninstall-tdx-login-guard.ps1"
```

默认删除计划任务但保留配置和日志。需要删除守护文件时，先阅读卸载脚本参数并确认目标目录。
