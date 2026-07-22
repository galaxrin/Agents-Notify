# Agents Notify：Agent 任务通知到手表

当 AI Agent（Codex、ZCode 等）的任务完成或需要人工授权时，这个工具会通过 ntfy 向手机发送通知，再由运动健康同步到手表。手表会振动并显示"已完成"或"等待审核"。

## 工作流程

```text
AI Agent（Codex / ZCode / …）
  → 会话日志（JSONL）
  → Mac 后台程序 agent-watch-notify
  → HTTPS 推送到 ntfy
  → 手机上的 ntfy
  → 运动健康同步通知
  → 手表
```

程序只发送固定通知文案，不会发送 Agent 回复、源代码、命令、文件路径或审批原因。

## 适用环境

- macOS 或 Windows，运行 Codex、ZCode、Claude Code、Kimi Code 等 AI Agent
- 安卓手机，已安装 ntfy
- 可以同步手机应用通知的手表
- 已验证组合：小米 17 Pro、Xiaomi Watch S3、F-Droid 版 ntfy

其他安卓手机或可同步通知的手表也可能适用，但未逐一验证。

程序会自动发现 Agent 会话目录（macOS: `~/.*/sessions`，Windows: `%APPDATA%\*\sessions`），新安装的 Agent 无需手动配置即可被监控。

## 手机和手表准备

1. 在安卓手机安装 ntfy。中国大陆版安卓系统建议使用 F-Droid 版，避免依赖 Google FCM。
2. 在 ntfy 中订阅一个足够长、随机且只有自己知道的主题。
3. 为 ntfy 开启通知权限、后台自启动，并把省电策略设为"无限制"。
4. 在最近任务中锁定 ntfy，避免系统清理它的常驻连接。
5. 打开小米运动健康：设备 → 应用通知，允许同步 ntfy 通知。
6. 保持手机与手表蓝牙连接。

## 安装

前提：Python 3.10+ 已安装并加入 PATH。

### 一键安装

macOS：

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/galaxrin/Agents-Notify/main/scripts/bootstrap.sh)"
```

Windows PowerShell：

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/galaxrin/Agents-Notify/main/scripts/bootstrap.ps1)))
```

脚本会从 GitHub 安装最新版、注册开机自启并自动打开 Web 配置，不需要 Git 或克隆仓库。配置保存后立即生效，后台通知继续运行。

### 手动安装

```bash
git clone https://github.com/galaxrin/Agents-Notify.git
cd Agents-Notify
pip install .
agent-watch-notify --install
```

安装程序会交互式提示输入 ntfy 主题地址和令牌，自动检测操作系统并注册开机自启：

- macOS：注册 LaunchAgent（`launchctl`）
- Windows：注册计划任务（`schtasks`）

程序会自动发现所有已安装 Agent 的会话目录，无需手动配置。

安装后验证：

```bash
agent-watch-notify --test
```

重启后台服务：

```bash
# macOS
launchctl kickstart -k "gui/$(id -u)/com.agent.watch-notify"
```

```powershell
# Windows PowerShell
schtasks /end /tn agent-watch-notify
schtasks /run /tn agent-watch-notify
```

macOS 的 `kickstart -k` 会终止当前进程并立即由 LaunchAgent 重新启动；Windows 需要先停止再启动计划任务。

## Web 配置页面

安装后运行以下命令打开可视化配置页面：

```bash
agent-watch-notify --config
```

该命令会启动本地 Web 服务，并自动打开 `http://127.0.0.1:9876`。终端需保持运行；配置完成后按 `Ctrl+C` 关闭 Web 服务。页面可以配置：

- ntfy 连接信息（地址、令牌、监听目录）
- 通知文案（完成/审批的标题、正文）
- 通知优先级和标签（emoji）
- 审批宽限期和轮询间隔
- 测试通知、恢复默认

保存后立即生效，无需重启后台通知服务。Web 服务仅本机可访问，不暴露到局域网。

## 测试通知

安装后运行：

```bash
agent-watch-notify --test
```

程序会自动读取配置文件，无需手动设置环境变量。默认情况下，手机和手表应收到：

```text
任务已完成
Agent 任务已结束
```

## 自定义通知文案

运行以下命令打开配置：

```bash
open -e "$HOME/.config/agent-watch-notify/messages.json"
```

或使用 Web 配置页面（推荐）：

```bash
agent-watch-notify --config
```

配置包含以下项：

```json
{
  "display_name": "Agent",
  "title_separator": "·",
  "complete_title": "任务已完成",
  "complete_body": "Agent 任务已结束",
  "approval_title": "等待审核",
  "approval_body": "请回到 Agent 处理",
  "complete_priority": "default",
  "complete_tags": "white_check_mark",
  "approval_priority": "urgent",
  "approval_tags": "warning"
}
```

| 字段 | 说明 | 可选值 |
|------|------|--------|
| `display_name` | Agent 名称 | 任意文本，例如 `Codex` |
| `title_separator` | Agent 名称与标题的分隔符 | 任意文本；留空则不显示 |
| `complete_priority` | 完成通知优先级 | `min` `low` `default` `high` `urgent` |
| `complete_tags` | 完成通知标签 | ntfy emoji 标签名 |
| `approval_priority` | 审批通知优先级 | 同上 |
| `approval_tags` | 审批通知标签 | 同上 |

保存后，下一条通知立即使用新文案，不需要重启服务。字段缺失、为空、类型错误或 JSON 无效时，对应字段会使用内置默认值。再次运行安装脚本不会覆盖已有自定义文案。

## 运行机制

启动时自动发现所有 Agent 会话目录（`~/.*/sessions`、`~/.*/cli/agents`、`~/.*/cli/rollout`），每秒扫描其中 `*.jsonl` 文件的新增完整行：

- Codex `event_msg / task_complete` 或 ZCode `turn_complete`：发送任务完成通知。
- 需要 `sandbox_permissions=require_escalated` 的工具调用：等待 10 秒宽限期后发送审批通知（宽限期内自动解决则不通知）。

程序使用 `turn_id` 和 `call_id` 去重，最近 500 个已发送事件保存在：

```text
~/.local/state/agent-watch-notify/seen.json
```

启动时会从已有日志末尾开始，不会重放历史任务。网络请求超时为 5 秒，目前不提供离线补发队列。

## 环境变量

| 变量名 | 旧名称（兼容） | 必填 | 默认值 | 说明 |
|--------|---------------|------|--------|------|
| `AGENT_WATCH_NTFY_URL` | `CODEX_WATCH_NTFY_URL` | ✅ | — | ntfy 主题地址（HTTPS） |
| `AGENT_WATCH_NTFY_TOKEN` | `CODEX_WATCH_NTFY_TOKEN` | — | — | ntfy 认证令牌 |
| `AGENT_WATCH_SESSIONS_DIR` | `CODEX_SESSIONS_DIR` | — | 自动发现 | 逗号分隔的会话目录，留空则自动扫描 |
| `AGENT_WATCH_APPROVAL_DELAY` | `CODEX_WATCH_APPROVAL_DELAY` | — | `10` | 审批宽限期（秒） |
| `AGENT_WATCH_POLL_INTERVAL` | `CODEX_WATCH_POLL_INTERVAL` | — | `1` | 轮询间隔（秒，最小 0.5） |

旧变量名仍可使用，新变量名优先级更高。

## 安全说明

- ntfy 地址必须使用 HTTPS。
- 通知只使用配置中的固定文案，不包含会话正文或执行命令。
- 公开 ntfy 主题相当于密码，请使用随机长主题，不要提交到仓库或公开分享。
- 主题地址和 token 保存在权限为 `600` 的配置及 LaunchAgent 文件中。
- Web 配置页面仅监听 `127.0.0.1`，不暴露到局域网。
- 本仓库中的主题和 token 都是占位示例。

## 排障

查看后台服务：

```bash
# macOS
launchctl print "gui/$(id -u)/com.agent.watch-notify"

# Windows
schtasks /query /tn agent-watch-notify /v
```

查看错误日志：

```bash
# macOS
tail -n 100 "$HOME/Library/Logs/agent-watch-notify/stderr.log"

# Windows
Get-Content "$env:USERPROFILE\.local\state\agent-watch-notify\stderr.log" -Tail 100
```

常见边界：

- 测试命令返回失败：检查 Mac 到 ntfy 的网络和主题地址。
- ntfy 网页能看到但手机延迟：确认使用 F-Droid 版，并为 ntfy 开启自启动、后台锁定和无限制省电策略。
- 手机立即收到但手表延迟：检查小米运动健康后台权限、应用通知同步和蓝牙连接。
- 测试通知正常但真实任务不通知：检查后台服务状态和错误日志。

## 运行测试

项目仅使用 Python 标准库：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q agent_watch_notify tests
```

## 卸载

```bash
agent-watch-notify --uninstall
pip uninstall agent-watch-notify
```

如需同时清除去重历史：

```bash
rm -f "$HOME/.local/state/agent-watch-notify/seen.json"
```

### 手动卸载

macOS：

```bash
launchctl bootout "gui/$(id -u)/com.agent.watch-notify"
rm -f "$HOME/Library/LaunchAgents/com.agent.watch-notify.plist"
rm -f "$HOME/.config/agent-watch-notify/env"
rm -f "$HOME/.config/agent-watch-notify/messages.json"
rm -f "$HOME/.local/bin/agent-watch-notify"
rm -f "$HOME/Library/Logs/agent-watch-notify/stderr.log"
rmdir "$HOME/.config/agent-watch-notify" \
  "$HOME/Library/Logs/agent-watch-notify" 2>/dev/null || true
```

Windows：

```powershell
schtasks /delete /tn agent-watch-notify /f
Remove-Item "$env:USERPROFILE\.config\agent-watch-notify" -Recurse -Force
Remove-Item "$env:USERPROFILE\.local\bin\agent-watch-notify.cmd" -Force
Remove-Item "$env:USERPROFILE\.local\state\agent-watch-notify" -Recurse -Force
```

## 从旧版迁移

如果你之前安装过 `codex-watch-notify`，请先卸载旧版，再运行 `agent-watch-notify --install`：

- `~/.config/codex-watch-notify` → `~/.config/agent-watch-notify`
- `~/.local/state/codex-watch-notify` → `~/.local/state/agent-watch-notify`
- `~/Library/Logs/codex-watch-notify` → `~/Library/Logs/agent-watch-notify`
- 已有的自定义文案不会被覆盖
