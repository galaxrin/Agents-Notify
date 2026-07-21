# Agents Notify：Codex 任务通知到小米手表

当 Codex Desktop 的任务完成或需要人工授权时，这个小工具会通过 ntfy 向安卓手机发送通知，再由小米运动健康同步到 Xiaomi Watch S3。手表会振动并显示“已完成”或“等待审核”。

## 工作流程

```text
Codex Desktop
  → ~/.codex/sessions 中的 JSONL 会话日志
  → Mac 后台程序 codex-watch-notify
  → HTTPS 推送到 ntfy
  → 安卓手机上的 ntfy
  → 小米运动健康同步通知
  → Xiaomi Watch S3
```

程序只发送固定通知文案，不会发送 Codex 回复、源代码、命令、文件路径或审批原因。

## 适用环境

- macOS，运行 Codex Desktop
- 安卓手机，已安装 ntfy
- 可以同步手机应用通知的手表
- 已验证组合：小米 17 Pro、Xiaomi Watch S3、F-Droid 版 ntfy

其他安卓手机或可同步通知的手表也可能适用，但未逐一验证。

## 手机和手表准备

1. 在安卓手机安装 ntfy。中国大陆版安卓系统建议使用 F-Droid 版，避免依赖 Google FCM。
2. 在 ntfy 中订阅一个足够长、随机且只有自己知道的主题。
3. 为 ntfy 开启通知权限、后台自启动，并把省电策略设为“无限制”。
4. 在最近任务中锁定 ntfy，避免系统清理它的常驻连接。
5. 打开小米运动健康：设备 → 应用通知，允许同步 ntfy 通知。
6. 保持手机与手表蓝牙连接。

## Mac 安装

克隆仓库后进入项目目录：

```bash
git clone https://github.com/galaxrin/Agents-Notify.git
cd Agents-Notify
```

设置 ntfy 主题地址并安装：

```bash
export CODEX_WATCH_NTFY_URL='https://ntfy.sh/<随机长主题>'
sh scripts/install.sh
```

如果使用需要认证的自建 ntfy 服务，可额外设置 token：

```bash
export CODEX_WATCH_NTFY_TOKEN='tk_<你的令牌>'
```

安装脚本会创建：

```text
~/.local/bin/codex-watch-notify
~/.config/codex-watch-notify/env
~/.config/codex-watch-notify/messages.json
~/Library/LaunchAgents/com.codex.watch-notify.plist
~/Library/Logs/codex-watch-notify/stderr.log
```

LaunchAgent 会在用户登录 macOS 时启动监听服务。

## 测试通知

安装后运行：

```bash
set -a
. "$HOME/.config/codex-watch-notify/env"
set +a
"$HOME/.local/bin/codex-watch-notify" --test
```

默认情况下，手机和手表应收到：

```text
Codex · 已完成
Codex 任务已结束
```

随后可以分别完成一个真实 Codex 任务、触发一次需要授权的工具操作，确认两种通知各发送一次。

## 自定义通知文案

运行以下命令打开配置：

```bash
open -e "$HOME/.config/codex-watch-notify/messages.json"
```

配置包含四项文案：

```json
{
  "complete_title": "Codex · 已完成",
  "complete_body": "Codex 任务已结束",
  "approval_title": "Codex · 等待审核",
  "approval_body": "请回到 Codex 处理"
}
```

保存后，下一条通知立即使用新文案，不需要重启服务。字段缺失、为空、类型错误或 JSON 无效时，对应字段会使用内置默认值。再次运行安装脚本不会覆盖已有自定义文案。

## 运行机制

后台程序每秒扫描 `~/.codex/sessions/**/*.jsonl` 的新增完整行：

- `event_msg / task_complete`：发送任务完成通知。
- 需要 `sandbox_permissions=require_escalated` 的工具调用：发送等待审核通知。

程序使用 `turn_id` 和 `call_id` 去重，最近 500 个已发送事件保存在：

```text
~/.local/state/codex-watch-notify/seen.json
```

启动时会从已有日志末尾开始，不会重放历史任务。网络请求超时为 5 秒，目前不提供离线补发队列。

## 安全说明

- ntfy 地址必须使用 HTTPS。
- 通知只使用配置中的固定文案，不包含会话正文或执行命令。
- 公开 ntfy 主题相当于密码，请使用随机长主题，不要提交到仓库或公开分享。
- 主题地址和 token 保存在权限为 `600` 的配置及 LaunchAgent 文件中。
- 本仓库中的主题和 token 都是占位示例。

## 排障

查看后台服务：

```bash
launchctl print "gui/$(id -u)/com.codex.watch-notify"
```

查看错误日志：

```bash
tail -n 100 "$HOME/Library/Logs/codex-watch-notify/stderr.log"
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
sh -n scripts/install.sh
```

## 卸载

```bash
launchctl bootout "gui/$(id -u)/com.codex.watch-notify"
rm -f "$HOME/Library/LaunchAgents/com.codex.watch-notify.plist"
rm -f "$HOME/.config/codex-watch-notify/env"
rm -f "$HOME/.config/codex-watch-notify/messages.json"
rm -f "$HOME/.local/bin/codex-watch-notify"
rm -f "$HOME/Library/Logs/codex-watch-notify/stderr.log"
rmdir "$HOME/.config/codex-watch-notify" \
  "$HOME/Library/Logs/codex-watch-notify" 2>/dev/null || true
```

如需同时清除去重历史：

```bash
rm -f "$HOME/.local/state/codex-watch-notify/seen.json"
```
