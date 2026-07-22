# 需求文档：多 Agent 适配 + 通知文案自定义

## 背景

当前项目 `codex-watch-notify` 仅监听 Codex Desktop 的会话日志（`~/.codex/sessions/**/*.jsonl`），硬编码了 Codex 相关的路径、名称和事件格式。需要扩展为通用的 Agent 任务通知工具，支持 Codex、ZCode 及未来其他 Agent。

## 需求 A：适配所有 Agent（含 ZCode）

### A1. 可配置的会话目录

- 当前：硬编码 `~/.codex/sessions`
- 目标：支持通过环境变量 `CODEX_WATCH_SESSIONS_DIR` 配置监听目录
- 默认值保持 `~/.codex/sessions`（向后兼容）
- 可监听多个目录（逗号分隔），例如：
  ```
  CODEX_WATCH_SESSIONS_DIR=~/.codex/sessions,~/.zcode/sessions
  ```

### A2. 事件解析兼容 ZCode

- ZCode 的会话日志格式与 Codex 基本一致（同为 OpenAI Codex 引擎）
- `task_complete` 事件：ZCode 使用相同的 `event_msg / task_complete` 结构
- 审批事件：ZCode 同样使用 `custom_tool_call` + `sandbox_permissions=require_escalated`
- 确认：当前 `parse_event` 逻辑无需修改即可兼容 ZCode

### A3. 通知标题去除硬编码品牌名

- 当前默认文案包含 "Codex" 前缀（如 "Codex · 已完成"）
- 目标：默认文案改为通用表述，品牌名由用户通过 `messages.json` 自行添加
- 新默认值：
  ```json
  {
    "complete_title": "任务已完成",
    "complete_body": "Agent 任务已结束",
    "approval_title": "等待审核",
    "approval_body": "请回到 Agent 处理"
  }
  ```
- 已有自定义文案的用户不受影响（安装器不覆盖已有 `messages.json`）

### A4. LaunchAgent / plist 适配

- plist 文件名从 `com.codex.watch-notify` 改为 `com.agent.watch-notify`
- LaunchAgent label 同步更新
- 环境变量名称更新：
  - `CODEX_WATCH_NTFY_URL` → `AGENT_WATCH_NTFY_URL`
  - `CODEX_WATCH_NTFY_TOKEN` → `AGENT_WATCH_NTFY_TOKEN`
  - `CODEX_WATCH_SESSIONS_DIR`（新增）
  - `CODEX_SESSIONS_DIR`（保留向后兼容，优先级低于新名称）

### A5. 安装器更新

- 安装器中的目录名从 `codex-watch-notify` 改为 `agent-watch-notify`
  - `~/.config/codex-watch-notify` → `~/.config/agent-watch-notify`
  - `~/.local/state/codex-watch-notify` → `~/.local/state/agent-watch-notify`
  - `~/Library/Logs/codex-watch-notify` → `~/Library/Logs/agent-watch-notify`
- 二进制名从 `codex-watch-notify` 改为 `agent-watch-notify`
- 提供迁移逻辑：检测旧路径并提示用户

### A6. README 更新

- 标题从 "Agents Notify：Codex 任务通知到小米手表" 改为 "Agents Notify：Agent 任务通知到手表"
- 工作流程图更新，体现多 Agent 支持
- 安装和测试命令同步更新

## 需求 B：通知文案自定义

### 现状

`messages.json` 自定义功能**已经实现**，当前支持：
- `complete_title` / `complete_body`：任务完成文案
- `approval_title` / `approval_body`：等待审核文案
- 热加载：保存后下一条通知立即生效，无需重启
- 容错：字段缺失、为空、类型错误或 JSON 无效时使用默认值
- 安装时不覆盖已有自定义文案

### B1. 通知文案

当前实现已满足文案自定义的全部要求。仅需：
- 更新默认文案（见 A3）
- 更新 README 中的示例文案

### B2. ntfy 优先级

ntfy 支持 5 个优先级：`min`、`low`、`default`、`high`、`urgent`，不同优先级在手机端对应不同的振动强度和通知样式。

- 在 `messages.json` 中新增两个字段：
  ```json
  {
    "complete_priority": "default",
    "approval_priority": "urgent"
  }
  ```
- `publish` 发送时将优先级写入 `X-Priority` header
- 字段缺失或无效时使用 `default`
- 审批通知默认 `urgent`，手表强振动提醒

### B3. ntfy 标签 / emoji

ntfy 支持 `Tags` header，手机通知栏可显示 emoji 图标。

- 在 `messages.json` 中新增两个字段：
  ```json
  {
    "complete_tags": "white_check_mark",
    "approval_tags": "warning"
  }
  ```
- `publish` 发送时将标签写入 `Tags` header
- 默认值：完成用 `white_check_mark`（✅），审批用 `warning`（⚠️）
- 字段缺失或为空时不发送 Tags header

### B4. 审批宽限期

审批事件在发送通知前等待宽限期，防止用户已在 Agent 界面操作但仍收到通知。

- 当前硬编码 10 秒
- 新增环境变量 `AGENT_WATCH_APPROVAL_DELAY`，单位秒，默认 `10`
- 浮点数，支持小数（如 `5.5`）
- 无效值（负数、非数字）静默回退默认值

### B5. 轮询间隔

- 当前硬编码 1 秒
- 新增环境变量 `AGENT_WATCH_POLL_INTERVAL`，单位秒，默认 `1`
- 最小值 0.5 秒，低于此值静默回退默认值
- 省电场景可设为 3-5 秒

## 需求 C：本地 Web 配置页面

### C1. 概述

提供一个本地 Web 页面作为配置载体，用户在浏览器中可视化编辑 ntfy 连接信息和通知文案，替代手动编辑 JSON 文件和环境变量。

### C2. 技术方案

- 使用 Python 内置 `http.server` + 单个 HTML 页面，零外部依赖
- 启动命令：`agent-watch-notify --config`，自动打开浏览器
- 默认监听 `127.0.0.1:9876`，仅本机可访问
- 读写同一份配置文件，保存后立即生效（热加载机制已有）

### C3. 页面功能

页面分为两个配置区域：

**ntfy 连接配置**
| 字段 | 对应环境变量 | 说明 |
|------|-------------|------|
| ntfy 主题地址 | `AGENT_WATCH_NTFY_URL` | 必填，HTTPS URL |
| ntfy 认证令牌 | `AGENT_WATCH_NTFY_TOKEN` | 选填，Bearer token |
| 监听目录 | `AGENT_WATCH_SESSIONS_DIR` | 逗号分隔，默认 `~/.codex/sessions` |
| 审批宽限期（秒） | `AGENT_WATCH_APPROVAL_DELAY` | 默认 10 |
| 轮询间隔（秒） | `AGENT_WATCH_POLL_INTERVAL` | 默认 1 |

**通知文案配置**
| 字段 | 对应 messages.json key | 说明 |
|------|----------------------|------|
| 完成标题 | `complete_title` | 默认 "任务已完成" |
| 完成正文 | `complete_body` | 默认 "Agent 任务已结束" |
| 完成优先级 | `complete_priority` | 下拉选择，默认 `default` |
| 完成标签 | `complete_tags` | emoji 标签，默认 `white_check_mark` |
| 审批标题 | `approval_title` | 默认 "等待审核" |
| 审批正文 | `approval_body` | 默认 "请回到 Agent 处理" |
| 审批优先级 | `approval_priority` | 下拉选择，默认 `urgent` |
| 审批标签 | `approval_tags` | emoji 标签，默认 `warning` |

### C4. 交互要求

- 页面加载时读取当前配置并填入表单
- 保存时写入配置文件和 env 文件，显示"已保存"提示
- 提供"测试通知"按钮，发送一条测试通知验证连通性
- 提供"恢复默认"按钮，清空自定义文案回退默认值
- env 文件和 messages.json 路径遵循项目约定：
  - `~/.config/agent-watch-notify/env`
  - `~/.config/agent-watch-notify/messages.json`

### C5. 安全

- 仅监听 `127.0.0.1`，不暴露到局域网
- 不记录、不传输 ntfy 地址和 token 到任何外部服务
- 页面无登录要求（本机访问，威胁模型等同于直接编辑文件）

### C6. 安装集成

- 安装器中增加 `--config` 快捷方式的提示
- Web 页面位于 `agent_watch_notify/config_server.py`，不随系统自启
- 用户按需手动启动，不随系统自启

## 不在本次范围内

- 支持非 JSONL 格式的 Agent 日志（如 Cursor、Windsurf 等）
- 支持 Telegram、Bark、企业微信等其他通知渠道
- 点击通知跳转 Agent 窗口（需 URL scheme 支持，后续考虑）

## 验收标准

1. `AGENT_WATCH_SESSIONS_DIR=~/.codex/sessions,~/.zcode/sessions` 可同时监听两个目录
2. 旧环境变量 `CODEX_WATCH_NTFY_URL` 仍可工作（向后兼容）
3. 默认文案不含 "Codex" 字样
4. 已有 `messages.json` 不被覆盖
5. `messages.json` 中设置 `approval_priority: "urgent"` 后，审批通知携带 `X-Priority: urgent` header
6. `messages.json` 中设置 `complete_tags: "white_check_mark"` 后，完成通知携带 `Tags: white_check_mark` header
7. `AGENT_WATCH_APPROVAL_DELAY=5` 可将审批宽限期改为 5 秒
8. `AGENT_WATCH_POLL_INTERVAL=3` 可将轮询间隔改为 3 秒
9. `python3 -m unittest discover -s tests -v` 全部通过
10. `python3 -m compileall -q agent_watch_notify tests` 语法检查通过
11. `agent-watch-notify --config` 启动 Web 配置页面，浏览器打开 `http://127.0.0.1:9876` 可编辑所有配置
12. Web 页面保存后，messages.json 和 env 文件内容正确更新
