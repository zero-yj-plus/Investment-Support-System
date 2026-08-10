# Caddy Windows 安装与运维

本文说明如何在 Windows 上使用 Caddy 反向代理 Hermes Trader，并配置密码保护和 Windows 自动启动服务。

## 1. 部署结构

以下命令假设：

- 项目目录：`C:\Users\Administrator\Desktop\投研助手`
- 后端地址：`127.0.0.1:18869`
- Caddy 程序：`caddy_windows_amd64.exe`
- Caddy 配置：`Caddyfile`
- Windows 服务名：`caddy`

如果实际路径不同，只修改下面的 `$root`。

所有服务安装命令必须在“以管理员身份运行”的 PowerShell 中执行。

```powershell
$root='C:\Users\Administrator\Desktop\投研助手'
Set-Location $root
```

## 2. 准备 Caddy

从 [Caddy 官方下载页](https://caddyserver.com/download) 下载 Windows amd64 版本，把程序放入项目目录。文件可以保留下载名称，也可以重命名为 `caddy.exe`。

确认文件存在并检查版本：

```powershell
$exe=Join-Path $root 'caddy_windows_amd64.exe'
Test-Path $exe
& $exe version
```

如果文件名是 `caddy.exe`，使用：

```powershell
$exe=Join-Path $root 'caddy.exe'
```

## 3. 生成密码哈希

推荐使用 Argon2id。不要把明文密码放进命令参数，否则可能留在 PowerShell 历史记录中。

```powershell
& $exe hash-password --algorithm argon2id
```

根据提示输入密码，复制输出的完整 `$argon2id$...` 字符串。

## 4. 创建 Caddyfile

### 方案 A：仅使用 HTTP

适用于受信任的内网或临时测试。Basic Auth 在明文 HTTP 上不能保护传输过程，不建议直接暴露到公网。

```caddyfile
:80 {
    basic_auth argon2id {
        admin $argon2id$v=19$m=...替换为完整哈希...
    }

    reverse_proxy 127.0.0.1:18869
}
```

### 方案 B：域名加自动 HTTPS

推荐用于公网。需要域名已经解析到服务器，并放行 TCP 80、443 端口。

```caddyfile
example.com {
    basic_auth argon2id {
        admin $argon2id$v=19$m=...替换为完整哈希...
    }

    reverse_proxy 127.0.0.1:18869
}
```

将 `example.com` 改成实际域名。不要在 `Caddyfile` 中保存明文密码。

## 5. 验证配置

```powershell
$config=Join-Path $root 'Caddyfile'
Test-Path $config
& $exe validate --config $config --adapter caddyfile
```

只有看到配置有效后，才继续安装或重启服务。

若验证失败，可以前台运行以查看完整错误：

```powershell
& $exe run --config $config --adapter caddyfile
```

按 `Ctrl+C` 停止前台进程。

## 6. 安装 Windows 服务

先生成服务启动命令：

```powershell
$binPath="`"$exe`" run --config `"$config`" --adapter caddyfile"
```

检查服务是否已经存在：

```powershell
Get-Service -Name caddy -ErrorAction SilentlyContinue
```

### 服务不存在

```powershell
sc.exe create caddy start= auto binPath= $binPath
```

### 服务已经存在

不要重复执行 `create`，直接修复程序路径和自动启动方式：

```powershell
sc.exe config caddy start= auto binPath= $binPath
```

检查服务保存的实际路径：

```powershell
sc.exe qc caddy
```

`BINARY_PATH_NAME` 中的程序和 `Caddyfile` 必须是存在的绝对路径。

## 7. 启动与验证

启动服务：

```powershell
sc.exe start caddy
```

检查服务状态：

```powershell
sc.exe query caddy
```

成功时应显示：

```text
STATE : 4 RUNNING
```

检查监听端口：

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 80,443
```

检查密码保护：

```powershell
curl.exe -I http://127.0.0.1
```

启用 Basic Auth 后，未携带账号密码的请求应返回 `401 Unauthorized`。如果配置了域名和 HTTPS，改用实际的 `https://域名` 验证。

## 8. 修改配置或密码

修改 `Caddyfile` 后先验证：

```powershell
& $exe validate --config $config --adapter caddyfile
```

服务正在正常运行时，可以无中断重新加载：

```powershell
& $exe reload --address 127.0.0.1:2019 --config $config --adapter caddyfile
```

如果管理接口未运行或重新加载失败，完整重启服务：

```powershell
Restart-Service -Name caddy
Get-Service -Name caddy
```

修改密码时，重新运行 `hash-password`，只替换 `Caddyfile` 中对应用户的完整哈希，然后验证并重新加载。

## 9. Windows 防火墙

只在确实需要从其他机器访问时放行端口。

HTTP：

```powershell
New-NetFirewallRule -DisplayName 'Caddy HTTP 80' -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow
```

HTTPS：

```powershell
New-NetFirewallRule -DisplayName 'Caddy HTTPS 443' -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
```

Windows 防火墙放行不等于公网一定可达。云服务器还需要检查安全组；家庭网络还需要检查路由器端口映射和运营商 CGNAT。

## 10. 常见问题

### `CreateService 失败 1073：指定的服务已存在`

服务已经注册，不需要删除或重新创建。执行：

```powershell
sc.exe config caddy start= auto binPath= $binPath
```

### `StartService 失败 2：系统找不到指定的文件`

服务保存的可执行文件或配置文件路径已经失效。检查：

```powershell
sc.exe qc caddy
Test-Path $exe
Test-Path $config
```

确认 `$exe` 和 `$config` 正确后修复：

```powershell
$binPath="`"$exe`" run --config `"$config`" --adapter caddyfile"
sc.exe config caddy start= auto binPath= $binPath
sc.exe start caddy
```

### `reload` 提示无法连接 `127.0.0.1:2019`

Caddy 服务尚未运行，或者管理接口不可用。先执行：

```powershell
sc.exe start caddy
```

如果服务启动失败，用前台模式查看配置或端口错误：

```powershell
& $exe run --config $config --adapter caddyfile
```

### 服务运行但网页打不开

依次检查：

```powershell
Get-Service -Name caddy
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 80,443,18869
curl.exe -I http://127.0.0.1:18869
curl.exe -I http://127.0.0.1
```

如果 `18869` 不通，问题在 Hermes Trader 后端而不是 Caddy。如果 `18869` 正常但 80/443 不通，再检查 Caddy 配置、服务和端口占用。

## 11. 停止或卸载

停止服务：

```powershell
Stop-Service -Name caddy
```

卸载服务会移除服务注册，但不会删除 Caddy 程序和配置文件：

```powershell
Stop-Service -Name caddy -ErrorAction SilentlyContinue
sc.exe delete caddy
```
