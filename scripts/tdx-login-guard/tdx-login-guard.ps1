[CmdletBinding(DefaultParameterSetName = 'Once')]
param(
    [Parameter(ParameterSetName = 'Watch')][switch]$Watch,
    [Parameter(ParameterSetName = 'Inspect')][switch]$Inspect,
    [Parameter(ParameterSetName = 'Once')][switch]$Once,
    [string]$Config = (Join-Path $PSScriptRoot 'tdx-login-guard.json'),
    [switch]$DryRun,
    [switch]$NoFileLog
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

if (-not ('TdxLoginGuard.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace TdxLoginGuard {
    public delegate bool EnumWindowProc(IntPtr hWnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct Rect {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    public static class NativeMethods {
        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern IntPtr SetFocus(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern IntPtr GetFocus();

        [DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("kernel32.dll")]
        public static extern uint GetCurrentThreadId();

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetClassName(IntPtr hWnd, System.Text.StringBuilder lpClassName, int nMaxCount);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool GetWindowRect(IntPtr hWnd, out Rect rect);

        [DllImport("user32.dll")]
        public static extern int GetDlgCtrlID(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern IntPtr GetDlgItem(IntPtr hDlg, int nIDDlgItem);

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "SendMessageW")]
        public static extern IntPtr SendMessageText(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam);
    }
}
'@
}

function Add-DefaultProperty {
    param(
        [Parameter(Mandatory = $true)][psobject]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Value
    )
    if ($null -eq $Object.PSObject.Properties[$Name]) {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Read-GuardConfig {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "找不到守护配置：$Path。请先复制 tdx-login-guard.config.example.json。"
    }
    $Settings = Get-Content -Raw -LiteralPath $Path -Encoding utf8 | ConvertFrom-Json
    foreach ($Required in @('tdx_exe', 'process_name', 'tq_url', 'probe_code')) {
        if ($null -eq $Settings.PSObject.Properties[$Required] -or -not [string]$Settings.$Required) {
            throw "守护配置缺少必填项：$Required"
        }
    }

    Add-DefaultProperty $Settings 'bridge_url' 'http://127.0.0.1:18879'
    Add-DefaultProperty $Settings 'project_env_file' ''
    Add-DefaultProperty $Settings 'poll_seconds' 30
    Add-DefaultProperty $Settings 'unhealthy_threshold' 2
    Add-DefaultProperty $Settings 'action_cooldown_seconds' 180
    Add-DefaultProperty $Settings 'startup_timeout_seconds' 45
    Add-DefaultProperty $Settings 'login_timeout_seconds' 120
    Add-DefaultProperty $Settings 'popup_wait_seconds' 20
    Add-DefaultProperty $Settings 'recovery_mode' 'close_dialog'
    Add-DefaultProperty $Settings 'start_tdx_if_missing' $false
    Add-DefaultProperty $Settings 'credential_file' ''
    Add-DefaultProperty $Settings 'username_control_id' 1443
    Add-DefaultProperty $Settings 'password_control_id' 1058
    Add-DefaultProperty $Settings 'max_login_attempts_per_recovery' 1
    Add-DefaultProperty $Settings 'enable_keyboard_fallback' $false
    Add-DefaultProperty $Settings 'close_login_dialog_after_action' $false
    Add-DefaultProperty $Settings 'close_login_dialog_delay_seconds' 4
    Add-DefaultProperty $Settings 'login_window_patterns' @('登录', '通达信')
    Add-DefaultProperty $Settings 'login_window_classes' @('#32770')
    Add-DefaultProperty $Settings 'login_button_names' @('登录', '免费精选行情登录', '注册用户登录')
    Add-DefaultProperty $Settings 'dismiss_window_patterns' @('扫码', '二维码', '手机登录', '账号登录')
    Add-DefaultProperty $Settings 'dismiss_window_classes' @('#32770')
    Add-DefaultProperty $Settings 'dismiss_button_names' @('关闭', '以后再说', '暂不登录', '跳过', '取消', '×')
    Add-DefaultProperty $Settings 'log_path' ''

    if ([string]$Settings.recovery_mode -notin @('close_dialog', 'login_then_close')) {
        throw 'recovery_mode 只能是 close_dialog 或 login_then_close。'
    }

    foreach ($Bounded in @(
        @{ Name = 'poll_seconds'; Min = 10; Max = 300 },
        @{ Name = 'unhealthy_threshold'; Min = 1; Max = 10 },
        @{ Name = 'action_cooldown_seconds'; Min = 30; Max = 3600 },
        @{ Name = 'startup_timeout_seconds'; Min = 10; Max = 180 },
        @{ Name = 'login_timeout_seconds'; Min = 30; Max = 600 },
        @{ Name = 'popup_wait_seconds'; Min = 0; Max = 120 },
        @{ Name = 'close_login_dialog_delay_seconds'; Min = 1; Max = 30 },
        @{ Name = 'max_login_attempts_per_recovery'; Min = 1; Max = 3 }
    )) {
        $Number = [int]$Settings.($Bounded.Name)
        if ($Number -lt $Bounded.Min -or $Number -gt $Bounded.Max) {
            throw "$($Bounded.Name) 必须在 $($Bounded.Min) 到 $($Bounded.Max) 之间。"
        }
    }
    return $Settings
}

$script:Settings = Read-GuardConfig -Path $Config
$script:LogPath = if ([string]$script:Settings.log_path) {
    [string]$script:Settings.log_path
}
else {
    Join-Path $PSScriptRoot 'tdx-login-guard.log'
}
$script:LastTqError = ''
$script:LastBridgeError = ''

function Write-GuardLog {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('INFO', 'WARN', 'ERROR')][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $Line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $Line
    if ($NoFileLog) {
        return
    }
    try {
        $Directory = Split-Path -Parent $script:LogPath
        if ($Directory -and -not (Test-Path -LiteralPath $Directory -PathType Container)) {
            New-Item -ItemType Directory -Path $Directory -Force | Out-Null
        }
        Add-Content -LiteralPath $script:LogPath -Value $Line -Encoding utf8
    }
    catch {
        Write-Warning "无法写入守护日志：$($_.Exception.Message)"
    }
}

function Import-SelectedDotEnv {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    foreach ($RawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
        $Line = $RawLine.Trim()
        if (-not $Line -or $Line.StartsWith('#')) {
            continue
        }
        $Parts = $Line.Split('=', 2)
        if ($Parts.Count -ne 2) {
            continue
        }
        $Name = $Parts[0].Trim()
        if ($Name -notin @('TQ_BRIDGE_API_KEY', 'TDXAPI_KEY', 'TDXAPI_BASE')) {
            continue
        }
        $Value = $Parts[1].Trim()
        if ($Value.Length -ge 2 -and (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'")))) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
    }
}

Import-SelectedDotEnv -Path ([string]$script:Settings.project_env_file)

function Test-TqSession {
    try {
        $Payload = @{
            id = 1
            method = 'get_market_snapshot'
            params = @{
                stock_code = [string]$script:Settings.probe_code
                field_list = @()
            }
        } | ConvertTo-Json -Depth 5 -Compress
        $Response = Invoke-RestMethod -Uri ([string]$script:Settings.tq_url) -Method Post -ContentType 'application/json' -Body $Payload -TimeoutSec 8
        $ErrorId = [string]$Response.result.ErrorId
        if ($ErrorId -ne '0') {
            $script:LastTqError = "TQ ErrorId=$ErrorId"
            return $false
        }
        $script:LastTqError = ''
        return $true
    }
    catch {
        $script:LastTqError = $_.Exception.Message
        return $false
    }
}

function Test-BridgeQuote {
    $BridgeKey = [Environment]::GetEnvironmentVariable('TQ_BRIDGE_API_KEY', 'Process')
    if (-not $BridgeKey) {
        $BridgeKey = [Environment]::GetEnvironmentVariable('TDXAPI_KEY', 'Process')
    }
    if (-not $BridgeKey) {
        $script:LastBridgeError = '未配置桥密钥，跳过桥验证'
        return $null
    }

    $BridgeBase = [Environment]::GetEnvironmentVariable('TDXAPI_BASE', 'Process')
    if (-not $BridgeBase) {
        $BridgeBase = [string]$script:Settings.bridge_url
    }
    if ($BridgeBase -notmatch '^https?://(127\.0\.0\.1|localhost|\[::1\])(?::\d+)?/?$') {
        $script:LastBridgeError = '桥地址不是 loopback，拒绝守护程序探测'
        return $null
    }

    try {
        $Headers = @{ 'X-API-Key' = $BridgeKey }
        $Base = $BridgeBase.TrimEnd('/')
        $Health = Invoke-RestMethod -Uri "$Base/v1/meta/health" -Headers $Headers -Method Get -TimeoutSec 8
        if ($Health.ok -ne $true) {
            $script:LastBridgeError = '桥健康响应不是 ok=true'
            return $false
        }
        $Quote = Invoke-RestMethod -Uri "$Base/v1/realtime/quotes?codes=$([uri]::EscapeDataString([string]$script:Settings.probe_code))" -Headers $Headers -Method Get -TimeoutSec 12
        if ($null -eq $Quote) {
            $script:LastBridgeError = '桥行情响应为空'
            return $false
        }
        $script:LastBridgeError = ''
        return $true
    }
    catch {
        $script:LastBridgeError = $_.Exception.Message
        return $false
    }
}

function Get-TdxProcesses {
    return @(Get-Process -Name ([string]$script:Settings.process_name) -ErrorAction SilentlyContinue)
}

function Start-TdxIfNeeded {
    $Processes = @(Get-TdxProcesses)
    if ($Processes.Count -gt 0) {
        return $Processes
    }
    if (-not [bool]$script:Settings.start_tdx_if_missing) {
        Write-GuardLog ERROR '未发现通达信进程；安全配置禁止自动启动和操作首次登录页。'
        return @()
    }
    if ($DryRun) {
        Write-GuardLog INFO "DRY-RUN：将启动 $($script:Settings.tdx_exe)"
        return @()
    }
    $TdxPath = [IO.Path]::GetFullPath([string]$script:Settings.tdx_exe)
    if (-not (Test-Path -LiteralPath $TdxPath -PathType Leaf)) {
        throw "找不到通达信主程序：$TdxPath"
    }
    Write-GuardLog WARN "未发现通达信进程，正在启动 $TdxPath"
    $Started = Start-Process -FilePath $TdxPath -WorkingDirectory (Split-Path -Parent $TdxPath) -PassThru
    $Deadline = (Get-Date).AddSeconds([int]$script:Settings.startup_timeout_seconds)
    do {
        Start-Sleep -Seconds 2
        $Processes = @(Get-TdxProcesses)
        if ($Processes.Count -gt 0) {
            return $Processes
        }
    } while ((Get-Date) -lt $Deadline)
    throw "启动通达信后 $($script:Settings.startup_timeout_seconds) 秒内未发现进程。"
}

function Get-TopWindows {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)

    $Windows = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
    foreach ($ProcessId in $ProcessIds) {
        $Condition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            $ProcessId
        )
        $Found = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            $Condition
        )
        foreach ($Element in $Found) {
            $Windows.Add($Element)
        }
    }
    return @($Windows)
}

function Get-ElementName {
    param([Parameter(Mandatory = $true)][System.Windows.Automation.AutomationElement]$Element)
    try { return [string]$Element.Current.Name } catch { return '' }
}

function Get-Win32Text {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    $Buffer = [Text.StringBuilder]::new(512)
    $null = [TdxLoginGuard.NativeMethods]::GetWindowText($Handle, $Buffer, $Buffer.Capacity)
    return $Buffer.ToString()
}

function Get-Win32Class {
    param([Parameter(Mandatory = $true)][IntPtr]$Handle)
    $Buffer = [Text.StringBuilder]::new(256)
    $null = [TdxLoginGuard.NativeMethods]::GetClassName($Handle, $Buffer, $Buffer.Capacity)
    return $Buffer.ToString()
}

function Get-Win32Children {
    param([Parameter(Mandatory = $true)][IntPtr]$RootHandle)

    $Items = [System.Collections.Generic.List[object]]::new()
    $Callback = [TdxLoginGuard.EnumWindowProc]{
        param([IntPtr]$ChildHandle, [IntPtr]$Data)
        $ClassName = Get-Win32Class -Handle $ChildHandle
        $RawText = Get-Win32Text -Handle $ChildHandle
        $SafeText = if ($ClassName -match '(?i)edit|password') {
            '[REDACTED]'
        }
        elseif ($RawText.Length -le 80) {
            $RawText
        }
        else {
            $RawText.Substring(0, 80)
        }
        $Items.Add([pscustomobject]@{
            Handle = $ChildHandle
            ClassName = $ClassName
            Text = $SafeText
            RawText = $RawText
            ControlId = [TdxLoginGuard.NativeMethods]::GetDlgCtrlID($ChildHandle)
            Visible = [TdxLoginGuard.NativeMethods]::IsWindowVisible($ChildHandle)
            Rect = $(
                $Rectangle = [TdxLoginGuard.Rect]::new()
                if ([TdxLoginGuard.NativeMethods]::GetWindowRect($ChildHandle, [ref]$Rectangle)) {
                    '{0},{1},{2},{3}' -f $Rectangle.Left, $Rectangle.Top, ($Rectangle.Right - $Rectangle.Left), ($Rectangle.Bottom - $Rectangle.Top)
                }
                else {
                    ''
                }
            )
        })
        return $true
    }
    $null = [TdxLoginGuard.NativeMethods]::EnumChildWindows($RootHandle, $Callback, [IntPtr]::Zero)
    return @($Items)
}

function Invoke-Win32NamedButton {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$RootHandle,
        [Parameter(Mandatory = $true)][object[]]$Names
    )
    foreach ($Child in @(Get-Win32Children -RootHandle $RootHandle)) {
        if ($Child.ClassName -notmatch '(?i)^button$') {
            continue
        }
        foreach ($Wanted in @($Names)) {
            if ($Child.RawText.Trim() -ne ([string]$Wanted).Trim()) {
                continue
            }
            if ($DryRun) {
                Write-GuardLog INFO "DRY-RUN：将调用 Win32 按钮“$($Child.Text)” id=$($Child.ControlId) visible=$($Child.Visible)"
                return $true
            }
            if ($Child.Visible) {
                $null = [TdxLoginGuard.NativeMethods]::SendMessage($Child.Handle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
            }
            else {
                $null = [TdxLoginGuard.NativeMethods]::PostMessage($RootHandle, 0x0111, [IntPtr]$Child.ControlId, $Child.Handle)
            }
            return $true
        }
    }
    return $false
}

function ConvertFrom-ProtectedValue {
    param([Parameter(Mandatory = $true)][Security.SecureString]$Value)
    return ([Management.Automation.PSCredential]::new('protected', $Value)).GetNetworkCredential().Password
}

function Get-StoredLoginCredential {
    $CredentialFile = [string]$script:Settings.credential_file
    if (-not $CredentialFile -or -not (Test-Path -LiteralPath $CredentialFile -PathType Leaf)) {
        return $null
    }
    try {
        $Protected = Import-Clixml -LiteralPath $CredentialFile
        if ($Protected.Username -isnot [Security.SecureString] -or $Protected.Password -isnot [Security.SecureString]) {
            throw '凭据文件格式无效'
        }
        return [pscustomobject]@{
            Username = ConvertFrom-ProtectedValue -Value $Protected.Username
            Password = ConvertFrom-ProtectedValue -Value $Protected.Password
        }
    }
    catch {
        Write-GuardLog ERROR "无法读取当前 Windows 用户的加密登录凭据：$($_.Exception.Message)"
        return $null
    }
}

function ConvertTo-SendKeysText {
    param([Parameter(Mandatory = $true)][string]$Value)

    $Builder = [Text.StringBuilder]::new()
    foreach ($Character in $Value.ToCharArray()) {
        $Encoded = switch ([string]$Character) {
            '{' { '{{}'; break }
            '}' { '{}}'; break }
            default {
                if ('+^%~()[]'.Contains($Character)) {
                    '{' + [string]$Character + '}'
                }
                else {
                    [string]$Character
                }
            }
        }
        $null = $Builder.Append($Encoded)
    }
    return $Builder.ToString()
}

function Set-ExternalControlFocus {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$RootHandle,
        [Parameter(Mandatory = $true)][IntPtr]$ControlHandle
    )

    [uint32]$TargetProcessId = 0
    $TargetThreadId = [TdxLoginGuard.NativeMethods]::GetWindowThreadProcessId($RootHandle, [ref]$TargetProcessId)
    $CurrentThreadId = [TdxLoginGuard.NativeMethods]::GetCurrentThreadId()
    $Attached = $false
    try {
        if ($TargetThreadId -ne $CurrentThreadId) {
            $Attached = [TdxLoginGuard.NativeMethods]::AttachThreadInput($CurrentThreadId, $TargetThreadId, $true)
            if (-not $Attached) {
                return $false
            }
        }
        $null = [TdxLoginGuard.NativeMethods]::SetForegroundWindow($RootHandle)
        $null = [TdxLoginGuard.NativeMethods]::SetFocus($ControlHandle)
        return [TdxLoginGuard.NativeMethods]::GetFocus() -eq $ControlHandle
    }
    finally {
        if ($Attached) {
            $null = [TdxLoginGuard.NativeMethods]::AttachThreadInput($CurrentThreadId, $TargetThreadId, $false)
        }
    }
}

function Set-LoginCredentialByKeyboard {
    param(
        [Parameter(Mandatory = $true)][IntPtr]$RootHandle,
        [Parameter(Mandatory = $true)][IntPtr]$UsernameHandle,
        [Parameter(Mandatory = $true)][IntPtr]$PasswordHandle,
        [Parameter(Mandatory = $true)][object]$Credential
    )

    [uint32]$TargetProcessId = 0
    $TargetThreadId = [TdxLoginGuard.NativeMethods]::GetWindowThreadProcessId($RootHandle, [ref]$TargetProcessId)
    $CurrentThreadId = [TdxLoginGuard.NativeMethods]::GetCurrentThreadId()
    $Attached = $false
    try {
        if ($TargetThreadId -ne $CurrentThreadId) {
            $Attached = [TdxLoginGuard.NativeMethods]::AttachThreadInput($CurrentThreadId, $TargetThreadId, $true)
            if (-not $Attached) {
                Write-GuardLog ERROR '无法附加到通达信输入线程。'
                return $false
            }
        }
        $null = [TdxLoginGuard.NativeMethods]::SetForegroundWindow($RootHandle)
        $null = [TdxLoginGuard.NativeMethods]::SetFocus($UsernameHandle)
        if ([TdxLoginGuard.NativeMethods]::GetFocus() -ne $UsernameHandle) {
            Write-GuardLog ERROR '无法把键盘焦点限定到账号输入框。'
            return $false
        }
        [Windows.Forms.SendKeys]::SendWait('^a')
        [Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKeysText -Value ([string]$Credential.Username)))
        $null = [TdxLoginGuard.NativeMethods]::SetFocus($PasswordHandle)
        if ([TdxLoginGuard.NativeMethods]::GetFocus() -ne $PasswordHandle) {
            Write-GuardLog ERROR '账号输入后无法把焦点切换到已校准的密码框。'
            return $false
        }
        [Windows.Forms.SendKeys]::SendWait('^a')
        [Windows.Forms.SendKeys]::SendWait((ConvertTo-SendKeysText -Value ([string]$Credential.Password)))
        Write-GuardLog INFO '已向两个已校准控件发送真实键盘输入（内容未记录）。'
        return $true
    }
    finally {
        if ($Attached) {
            $null = [TdxLoginGuard.NativeMethods]::AttachThreadInput($CurrentThreadId, $TargetThreadId, $false)
        }
    }
}

function Set-Win32LoginCredential {
    param([Parameter(Mandatory = $true)][IntPtr]$RootHandle)

    $Credential = Get-StoredLoginCredential
    if ($null -eq $Credential) {
        return $false
    }
    $UsernameHandle = [TdxLoginGuard.NativeMethods]::GetDlgItem($RootHandle, [int]$script:Settings.username_control_id)
    $PasswordHandle = [TdxLoginGuard.NativeMethods]::GetDlgItem($RootHandle, [int]$script:Settings.password_control_id)
    if ($UsernameHandle -eq [IntPtr]::Zero -or $PasswordHandle -eq [IntPtr]::Zero) {
        Write-GuardLog ERROR '登录页账号或密码控件 ID 与当前客户端版本不匹配。'
        return $false
    }
    if ($DryRun) {
        Write-GuardLog INFO 'DRY-RUN：将向已校准控件填写本机 DPAPI 凭据（内容不记录）。'
        return $true
    }
    try {
        return Set-LoginCredentialByKeyboard -RootHandle $RootHandle -UsernameHandle $UsernameHandle -PasswordHandle $PasswordHandle -Credential $Credential
    }
    finally {
        $Credential.Username = $null
        $Credential.Password = $null
    }
}

function Test-AnyPattern {
    param(
        [string]$Text,
        [object[]]$Patterns
    )
    foreach ($Pattern in @($Patterns)) {
        if ($Text -match [string]$Pattern) {
            return $true
        }
    }
    return $false
}

function Find-NamedButton {
    param(
        [Parameter(Mandatory = $true)][System.Windows.Automation.AutomationElement]$Root,
        [Parameter(Mandatory = $true)][object[]]$Names
    )
    $ButtonCondition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )
    $Buttons = $Root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $ButtonCondition)
    foreach ($Button in $Buttons) {
        $CurrentName = (Get-ElementName $Button).Trim()
        foreach ($Wanted in @($Names)) {
            if ($CurrentName -eq ([string]$Wanted).Trim()) {
                return $Button
            }
        }
    }
    return $null
}

function Invoke-UiElement {
    param([Parameter(Mandatory = $true)][System.Windows.Automation.AutomationElement]$Element)

    if ($DryRun) {
        Write-GuardLog INFO "DRY-RUN：将调用控件“$(Get-ElementName $Element)”"
        return $true
    }
    try {
        $Pattern = $Element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        ([System.Windows.Automation.InvokePattern]$Pattern).Invoke()
        return $true
    }
    catch {
        try {
            $Legacy = $Element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
            ([System.Windows.Automation.LegacyIAccessiblePattern]$Legacy).DoDefaultAction()
            return $true
        }
        catch {
            Write-GuardLog WARN "控件调用失败：$($_.Exception.Message)"
            return $false
        }
    }
}

function Invoke-KeyFallback {
    param(
        [Parameter(Mandatory = $true)][System.Windows.Automation.AutomationElement]$Window,
        [Parameter(Mandatory = $true)][string]$Keys
    )
    if (-not [bool]$script:Settings.enable_keyboard_fallback) {
        return $false
    }
    if ($DryRun) {
        Write-GuardLog INFO "DRY-RUN：将向窗口“$(Get-ElementName $Window)”发送 $Keys"
        return $true
    }
    try {
        $Handle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($Handle -eq [IntPtr]::Zero) {
            return $false
        }
        $null = [TdxLoginGuard.NativeMethods]::SetForegroundWindow($Handle)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait($Keys)
        return $true
    }
    catch {
        Write-GuardLog WARN "键盘回退失败：$($_.Exception.Message)"
        return $false
    }
}

function Invoke-LoginAction {
    param([Parameter(Mandatory = $true)][object[]]$Windows)

    $Candidates = @($Windows | Where-Object {
        $NameMatches = Test-AnyPattern -Text (Get-ElementName $_) -Patterns @($script:Settings.login_window_patterns)
        $ClassMatches = ([string]$_.Current.ClassName) -in @($script:Settings.login_window_classes)
        $NameMatches -and $ClassMatches
    })
    if ($Candidates.Count -eq 0) {
        $Candidates = @($Windows)
    }
    foreach ($Window in $Candidates) {
        $RootHandle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($RootHandle -eq [IntPtr]::Zero) {
            continue
        }
        if ([string]$script:Settings.recovery_mode -eq 'login_then_close') {
            $BlockingPrompt = Find-NamedButton -Root $Window -Names @('确定')
            if ($null -ne $BlockingPrompt) {
                Write-GuardLog WARN '发现上一次登录提示，先关闭提示再填写凭据。'
                if (-not (Invoke-UiElement -Element $BlockingPrompt)) {
                    return $false
                }
                Start-Sleep -Milliseconds 800
            }
        }
        if ([string]$script:Settings.recovery_mode -eq 'login_then_close' -and -not (Set-Win32LoginCredential -RootHandle $RootHandle)) {
            Write-GuardLog ERROR '自动登录已启用，但无法安全填写凭据；拒绝提交登录。'
            return $false
        }
        if (Invoke-Win32NamedButton -RootHandle $RootHandle -Names @($script:Settings.login_button_names)) {
            Write-GuardLog WARN '发现 Win32 登录按钮，已按控件句柄调用。'
            return $true
        }
    }
    foreach ($Window in $Candidates) {
        $Button = Find-NamedButton -Root $Window -Names @($script:Settings.login_button_names)
        if ($null -ne $Button) {
            Write-GuardLog WARN "发现登录控件“$(Get-ElementName $Button)”，准备调用。"
            return Invoke-UiElement -Element $Button
        }
    }
    foreach ($Window in $Candidates) {
        if (Invoke-KeyFallback -Window $Window -Keys '{ENTER}') {
            Write-GuardLog WARN '未找到可调用登录控件，已使用受限 Enter 回退。'
            return $true
        }
    }
    return $false
}

function Close-LoginDialog {
    param([Parameter(Mandatory = $true)][object[]]$Windows)
    $Candidates = @($Windows | Where-Object {
        (Test-AnyPattern -Text (Get-ElementName $_) -Patterns @($script:Settings.login_window_patterns)) -and
        (([string]$_.Current.ClassName) -in @($script:Settings.login_window_classes))
    })
    foreach ($Window in $Candidates) {
        $Handle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($Handle -eq [IntPtr]::Zero) {
            continue
        }
        if ($DryRun) {
            Write-GuardLog INFO "DRY-RUN：将关闭登录对话框“$(Get-ElementName $Window)” class=$($Window.Current.ClassName)"
            return $true
        }
        if ([TdxLoginGuard.NativeMethods]::PostMessage($Handle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
            Write-GuardLog WARN "已关闭登录/扫码页面“$(Get-ElementName $Window)”。"
            return $true
        }
    }
    return $false
}

function Dismiss-PostLoginPopup {
    param(
        [Parameter(Mandatory = $true)][object[]]$Windows,
        [switch]$AllowClassClose
    )

    foreach ($Window in $Windows) {
        $WindowName = Get-ElementName $Window
        $WindowClass = [string]$Window.Current.ClassName
        $NameMatches = Test-AnyPattern -Text $WindowName -Patterns @($script:Settings.dismiss_window_patterns)
        $ClassMatches = $AllowClassClose -and $WindowClass -in @($script:Settings.dismiss_window_classes)
        if (-not $NameMatches -and -not $ClassMatches) {
            continue
        }
        $RootHandle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($RootHandle -ne [IntPtr]::Zero -and (Invoke-Win32NamedButton -RootHandle $RootHandle -Names @($script:Settings.dismiss_button_names))) {
            Write-GuardLog WARN "发现登录后弹窗“$WindowName”的 Win32 关闭按钮，已调用。"
            return $true
        }
        $Button = Find-NamedButton -Root $Window -Names @($script:Settings.dismiss_button_names)
        if ($null -ne $Button) {
            Write-GuardLog WARN "发现登录后弹窗“$WindowName”，调用“$(Get-ElementName $Button)”。"
            return Invoke-UiElement -Element $Button
        }
        if ($ClassMatches -and $RootHandle -ne [IntPtr]::Zero) {
            if ($DryRun) {
                Write-GuardLog INFO "DRY-RUN：TQ 恢复后将关闭对话框“$WindowName” class=$WindowClass"
                return $true
            }
            if ([TdxLoginGuard.NativeMethods]::PostMessage($RootHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
                Write-GuardLog WARN "TQ 恢复后已关闭登录/扫码对话框“$WindowName” class=$WindowClass。"
                return $true
            }
        }
        if (Invoke-KeyFallback -Window $Window -Keys '{ESC}') {
            Write-GuardLog WARN "登录后弹窗“$WindowName”未暴露关闭控件，已使用受限 Esc 回退。"
            return $true
        }
    }
    return $false
}

function Show-UiInventory {
    $Processes = @(Get-TdxProcesses)
    if ($Processes.Count -eq 0) {
        Write-GuardLog INFO 'UI-SCAN 未发现通达信进程。'
        return
    }
    $Windows = @(Get-TopWindows -ProcessIds @($Processes.Id))
    if ($Windows.Count -eq 0) {
        Write-GuardLog INFO 'UI-SCAN 未发现当前桌面会话中的通达信顶层窗口。'
        return
    }
    foreach ($Window in $Windows) {
        Write-GuardLog INFO ("UI-SCAN WINDOW pid={0} name={1} class={2} handle={3}" -f $Window.Current.ProcessId, (Get-ElementName $Window), $Window.Current.ClassName, $Window.Current.NativeWindowHandle)
        $ButtonCondition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
        $Buttons = $Window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $ButtonCondition)
        $Shown = 0
        foreach ($Button in $Buttons) {
            if ($Shown -ge 80) { break }
            Write-GuardLog INFO ("UI-SCAN BUTTON name={0} automation_id={1} class={2}" -f (Get-ElementName $Button), $Button.Current.AutomationId, $Button.Current.ClassName)
            $Shown++
        }
        $TextCondition = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Text
        )
        $TextElements = $Window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $TextCondition)
        $Shown = 0
        foreach ($TextElement in $TextElements) {
            if ($Shown -ge 80) { break }
            $TextName = (Get-ElementName $TextElement).Trim()
            if ($TextName) {
                Write-GuardLog INFO ("UI-SCAN TEXT name={0} automation_id={1} class={2}" -f $TextName, $TextElement.Current.AutomationId, $TextElement.Current.ClassName)
                $Shown++
            }
        }
        $RootHandle = [IntPtr]$Window.Current.NativeWindowHandle
        if ($RootHandle -ne [IntPtr]::Zero) {
            $Shown = 0
            foreach ($Child in @(Get-Win32Children -RootHandle $RootHandle)) {
                if ($Shown -ge 120) { break }
                Write-GuardLog INFO ("UI-SCAN WIN32 handle={0} visible={1} id={2} class={3} rect={4} text={5}" -f $Child.Handle, $Child.Visible, $Child.ControlId, $Child.ClassName, $Child.Rect, $Child.Text)
                $Shown++
            }
        }
    }
}

function Invoke-BridgeVerificationLog {
    $Bridge = Test-BridgeQuote
    if ($Bridge -eq $true) {
        Write-GuardLog INFO 'TQ 与桥实时行情验证通过。'
    }
    elseif ($Bridge -eq $false) {
        Write-GuardLog WARN "TQ 已恢复，但桥验证失败：$script:LastBridgeError"
    }
    else {
        Write-GuardLog INFO $script:LastBridgeError
    }
}

function Invoke-TdxRecovery {
    if (-not [Environment]::UserInteractive) {
        Write-GuardLog ERROR '当前不是交互式用户会话，拒绝执行 UI 登录。'
        return $false
    }

    $Processes = @(Start-TdxIfNeeded)
    if ($Processes.Count -eq 0) {
        return $false
    }
    $Deadline = (Get-Date).AddSeconds([int]$script:Settings.login_timeout_seconds)
    $LastAction = [datetime]::MinValue
    $LoginAttempts = 0
    do {
        if (Test-TqSession) {
            Write-GuardLog INFO 'TQ 登录会话已恢复。'
            $PopupDeadline = (Get-Date).AddSeconds([int]$script:Settings.popup_wait_seconds)
            do {
                $Processes = @(Get-TdxProcesses)
                $Windows = @(Get-TopWindows -ProcessIds @($Processes.Id))
                if (Dismiss-PostLoginPopup -Windows $Windows -AllowClassClose) {
                    break
                }
                if ([int]$script:Settings.popup_wait_seconds -le 0) {
                    break
                }
                Start-Sleep -Seconds 2
            } while ((Get-Date) -lt $PopupDeadline)
            Invoke-BridgeVerificationLog
            return $true
        }

        $Processes = @(Get-TdxProcesses)
        if ($Processes.Count -eq 0) {
            $Processes = @(Start-TdxIfNeeded)
            if ($Processes.Count -eq 0) {
                return $false
            }
        }
        if (((Get-Date) - $LastAction).TotalSeconds -ge 10) {
            $Windows = @(Get-TopWindows -ProcessIds @($Processes.Id))
            if ([string]$script:Settings.recovery_mode -eq 'close_dialog') {
                if (Close-LoginDialog -Windows $Windows) {
                    $LastAction = Get-Date
                }
            }
            elseif ($LoginAttempts -lt [int]$script:Settings.max_login_attempts_per_recovery) {
                $LoginAttempts++
                if (Invoke-LoginAction -Windows $Windows) {
                    $LastAction = Get-Date
                    if ([bool]$script:Settings.close_login_dialog_after_action) {
                        Start-Sleep -Seconds ([int]$script:Settings.close_login_dialog_delay_seconds)
                        if (-not (Test-TqSession)) {
                            $Processes = @(Get-TdxProcesses)
                            $Windows = @(Get-TopWindows -ProcessIds @($Processes.Id))
                            $null = Close-LoginDialog -Windows $Windows
                        }
                    }
                }
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $Deadline)

    Write-GuardLog ERROR "自动恢复超时：$script:LastTqError。请运行 -Inspect 校准控件。"
    return $false
}

if ($Inspect) {
    Show-UiInventory
    exit 0
}

$MutexName = 'Local\HermesTrader-TdxLoginGuard'
$CreatedNew = $false
$Mutex = [Threading.Mutex]::new($true, $MutexName, [ref]$CreatedNew)
if (-not $CreatedNew) {
    Write-GuardLog WARN '已有 TDX 登录守护实例运行，本实例退出。'
    $Mutex.Dispose()
    exit 0
}

try {
    if (-not $Watch) {
        if (Test-TqSession) {
            Write-GuardLog INFO 'TQ 登录会话正常，无需恢复。'
            Invoke-BridgeVerificationLog
            exit 0
        }
        Write-GuardLog WARN "TQ 登录会话不可用：$script:LastTqError"
        if ([string]$script:Settings.recovery_mode -eq 'close_dialog') {
            Write-GuardLog ERROR 'close_dialog 单次模式未观察到“健康后失效”转换，拒绝关闭窗口。'
            exit 2
        }
        if (Invoke-TdxRecovery) { exit 0 }
        exit 2
    }

    Write-GuardLog INFO 'TDX 登录守护已启动。'
    $ConsecutiveFailures = 0
    $LastRecoveryAttempt = [datetime]::MinValue
    $WasHealthy = $null
    $RecoveryArmed = $false
    while ($true) {
        $Healthy = Test-TqSession
        if ($Healthy) {
            if ($WasHealthy -ne $true) {
                Write-GuardLog INFO 'TQ 登录会话正常。'
            }
            $ConsecutiveFailures = 0
            $WasHealthy = $true
            $RecoveryArmed = $true
            $Processes = @(Get-TdxProcesses)
            if ($Processes.Count -gt 0) {
                $Windows = @(Get-TopWindows -ProcessIds @($Processes.Id))
                $null = Dismiss-PostLoginPopup -Windows $Windows
            }
        }
        else {
            $ConsecutiveFailures++
            if ($WasHealthy -ne $false) {
                Write-GuardLog WARN "TQ 登录会话异常：$script:LastTqError"
            }
            $WasHealthy = $false
            $CooldownElapsed = ((Get-Date) - $LastRecoveryAttempt).TotalSeconds -ge [int]$script:Settings.action_cooldown_seconds
            if ($ConsecutiveFailures -ge [int]$script:Settings.unhealthy_threshold -and $CooldownElapsed) {
                $LastRecoveryAttempt = Get-Date
                $Processes = @(Get-TdxProcesses)
                if ($Processes.Count -eq 0) {
                    $CanStartAndLogin = (
                        [string]$script:Settings.recovery_mode -eq 'login_then_close' -and
                        [bool]$script:Settings.start_tdx_if_missing
                    )
                    if ($CanStartAndLogin) {
                        Write-GuardLog WARN '会话失效且通达信进程不存在；按自动登录配置启动客户端。'
                        $null = Invoke-TdxRecovery
                    }
                    else {
                        Write-GuardLog ERROR '会话失效且通达信进程不存在；当前安全配置拒绝自动启动首次登录页。'
                        $RecoveryArmed = $false
                    }
                }
                elseif (-not $RecoveryArmed -and [string]$script:Settings.recovery_mode -eq 'close_dialog') {
                    Write-GuardLog WARN '启动后尚未观察到健康会话，拒绝自动关闭登录页。'
                }
                else {
                    $null = Invoke-TdxRecovery
                }
                $ConsecutiveFailures = 0
            }
        }
        Start-Sleep -Seconds ([int]$script:Settings.poll_seconds)
    }
}
catch {
    Write-GuardLog ERROR ("守护异常：{0}；位置：{1}" -f $_.Exception.Message, $_.InvocationInfo.PositionMessage.Trim())
    exit 1
}
finally {
    if ($CreatedNew) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
