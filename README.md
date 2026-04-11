# batch-add-gsc

批量添加域名到 Google Search Console 的 Claude Code 插件。

自动化完整流程：获取 DNS 验证 token → 写入 Cloudflare TXT 记录 → 验证域名所有权 → 注册为 GSC 属性。

## 安装

```bash
claude plugin add --from github:FunnyPCC/batch-add-gsc
```

## 前置条件

1. **Google Cloud 项目** — 启用 Search Console API 和 Site Verification API
2. **OAuth 2.0 客户端凭据**（Desktop 类型）— JSON 文件或 Client ID + Client Secret
3. **Cloudflare 账号** — 拥有域名的 API 访问权限
4. **[uv](https://docs.astral.sh/uv/)** — Python 包运行器（自动安装依赖）

## 使用

安装后，直接告诉 Claude Code：

> "帮我批量添加域名到 Google Search Console"

或

> "batch add my domains to GSC"

插件会引导你完成：
1. 收集凭据（支持 1Password、环境变量、手动输入）
2. 创建域名列表文件
3. 生成并配置脚本
4. 运行批量处理

## 凭据来源

脚本支持多种凭据获取方式：

| 来源 | Google OAuth | Cloudflare |
|------|-------------|------------|
| 1Password `op` CLI | client_id + client_secret + refresh_token | username + API key |
| 环境变量 | — | CF_EMAIL + CF_API_KEY |
| JSON 文件 | Google Cloud Console 导出的 OAuth JSON | — |
| 手动输入 | Client ID + Client Secret | — |

## 跨平台支持

本仓库包含 `AGENTS.md`，可供 Codex 等其他 AI 编程平台使用相同的工作流。

## 许可证

MIT

---

*Created by @FunnyPC & Claude Code*
