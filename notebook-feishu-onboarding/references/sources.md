# 官方来源与核对记录

核对日期：2026-10-22（Asia/Shanghai）。本包是独立编写的引导；图片包括官方公开教程的局部浏览器截图和 v0.3 补入的官方原图，权利归原作者。保留来源链接以便核对与更新，不把示例图冒充实际用户页面。

| 官方来源 | 本包使用范围 |
|---|---|
| [笔记同步助手安装与开通教程](https://www.bijitongbu.site/tutorials/obsidian-tutorial/) | 公众号发送 Obsidian 取密钥、添加助手好友、开始/完成通知 |
| [开发者集成说明](https://www.bijitongbu.site/tutorials/developer-integration/) | 官方 NoteHelper 入口、能力、客户端 UA、计费差异 |
| [NoteHelper 官方发布页](https://clawhub.ai/notesynchelper/skills/notehelper) | 公布的 API 路由、x-api-key、异步 task_id、GraphQL variables、安装入口 |
| [积分说明](https://www.bijitongbu.site/tutorials/points-explained/) | 当前费率以官方实际页面为准 |
| [飞书官方 Agent 安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md) | 安装 CLI、官方 skills、配置应用、授权 |
| [飞书官网图文教程](https://www.feishu.cn/content/article/7644418854143315157) | 安装、应用创建、用户确认授权的示例图 |
| [飞书官方 CLI 仓库](https://github.com/larksuite/cli) | CLI 来源、支持范围、身份和授权选项 |
| [Node.js 官方下载](https://nodejs.org/en/download) | 没有 Node/npm 时的安装入口 |
| [Python 官方下载](https://www.python.org/downloads/)与[Windows 官方指引](https://docs.python.org/3/using/windows.html) | 2026-10-22 补核：先复用已有工具提供的运行时，缺失时由 Agent 安装；Windows 安装管理器与 PowerShell 安装路线 |

## 实际核对的版本与差异

- 官方 ClawHub 发布者为 `notesynchelper`，页面显示版本 v1.0.3；当前页面安装命令已带发布者命名空间。
- 本机只读帮助核对版本为 lark-cli 1.0.96，不要求新用户固定到这个版本；新装以官方当前版本为准。
- 当前本机帮助确认：`auth login --recommend` 等同全业务域；旧图文里的“推荐权限”表述不能当成本包的授权范围。
- 当前帮助确认：OpenClaw/Hermes 中优先使用 `config bind`；绑定前确认目标应用和身份策略。
- npm 启动器会将 `lark-cli install --help` 交给安装向导，不能把它当只读检查。本次核对曾触发升级尝试，已停止，原 1.0.96 CLI 可用。
- 官方开发者指引明确要求显式 UA；本包 v0.2 Python 客户端设为 `notebook-feishu-onboarding/0.2`。

截图只是对应时点的示例，授权链接/二维码每个用户必须由其 Agent 当场生成。不将敏敏的飞书链接、应用凭证、私人笔记、日志或账号配置放进此包。

## v0.3 原图与排障补核

新增 13 张官方原图，逐张来源、哈希和日期见 [原图清单](../assets/official/manifest.json)。原图直接保存，未重画界面；开通消息中的密钥由官方遮盖。官网公开关注二维码可用于关注，个人助手绑定和飞书授权必须采用用户当次入口。

补核来源：[官网](https://www.bijitongbu.site/)、[OneNote 教程中的微信通用按钮](https://www.bijitongbu.site/tutorials/onenote-tutorial/)、[同步问题排查](https://www.bijitongbu.site/tutorials/sync-troubleshooting/)、[重置密钥](https://www.bijitongbu.site/tutorials/reset-key-tutorial/)、[常见问题](https://www.bijitongbu.site/tutorials/obsidian-faq/)、[权益说明](https://www.bijitongbu.site/tutorials/premium-faq/)、[教程索引](https://www.bijitongbu.site/tutorials/)。问题到官方章节的映射见 [提问与排障](help-and-official-docs.md)。用户提问后仍须实时查对应官方页面，不能把本次核对日期当成永远有效。

客户端继续使用 v0.2，未改代码或密钥处理方式；v0.3 为说明与原图补充版。未配置看板，也未完成真实新账号授权验收。

## v0.4 流程收敛

2026-10-22 按用户明确要求：先验证现有飞书连接，仅补缺失项；用户资料统一从微信转发进入，不在主流程提供 Agent 代交链接或另装官方 NoteHelper Skill 的选择。客户端、截图和看板占位未改。截图中的官方 NoteHelper 来源已实时核对为 [开发者集成说明](https://www.bijitongbu.site/tutorials/developer-integration/)里的官方条目 [notesynchelper/notehelper](https://clawhub.ai/notesynchelper/notehelper)；本包客户端使用其公布的接口，不是该官方 Skill 的原样分发。

## v0.8 下载兜底与手机操作示意

官网开通教程于 2026-10-22 经命令行 HTTPS 重新读取，关注、发送 Obsidian、取密钥及添加自己助手的链路保持。新增四张手机操作示意由本项目制作，不是官方原图或真实手机截图；分别说明微信搜索、关注、切换输入并发送 Obsidian、手机开启置顶。示意不包含账号、个人绑定码或密钥。官网未提供对应逐屏原图，故不伪造界面；出处及 SHA-256 见 assets/user-guide/manifest.json。原有官方图和已置顶电脑图保留。

网页口令及 install.md 增加网页读取失败后的命令行 HTTPS 获取、完整 ZIP 与哈希校验；安装后日常转发和定时同步不依赖下载网页常开。运行客户端及飞书、定时逻辑不变。

## v0.5 连续动作分组与置顶图

按用户确认的交互示例，将用户操作改为整组交接：微信接入含置顶、微信转发与通知、确实需要时的飞书创建、飞书个人授权。组内动作与图片一次发齐，只有整组结束、报错或真实后端依赖时交接。客户端、旧图片、飞书复用分支、看板占位均保留。

新增 assets/user-guide/wechat-pin-redacted.png 为用户提供的微信桌面截图经 AI 辅助隐私处理后的配图，不是官方原图；其它聊天头像、名称和消息已遮盖。菜单“取消置顶”是原图已有状态，表示已置顶，不改成另一状态。原始用户截图未收入分享包，隐私处理图的用途与哈希见同目录 manifest.json。

## v0.6 复测修正

移除图解中开通后立即回复的旧话术；置顶图增加“已置顶，不用点取消”横幅。客户端增加正文必填的 --id、单条 --limit 1、元信息 result_after 定位及响应ID核对；不符合时不输出/导出，不继续正文分页。UA 更新为 notebook-feishu-onboarding/0.6。

2026-10-22 重新打开 [官方Skill](https://clawhub.ai/notesynchelper/skills/notehelper)：当前公布 variables.query/first/after 搜索分页，未公布独立按ID取正文的接口。本包采用已公布分页取选定位置一条后严格核对ID，不虚构ID过滤接口；服务意外返回多条时拒绝结果，不能声称服务端绝不会返回其它正文。真实服务对单条分页的行为仍需账号实测。

## v0.7 用户验收后增量定时同步

2026-10-22 核对 [WorkBuddy官方定时任务说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Automation-Guide)：普通对话可创建任务，另有新建菜单、从任务新建、表单、立即运行、运行记录；本地运行需本机客户端可用，云端不能读取本机路径，默认权限可能待确认。新增两张官方公开界面原图，来源/哈希见 assets/official/manifest.json，图中的示例内容不复制为用户配置。

本版增加 sync_queue.py 的状态队列和宿主Agent执行指引：它不直接调用飞书、不直接创建任何调度器任务，飞书写入与回读由受调度的 Agent 按本机官方 lark-doc 完成。测试用户确认、真实调度创建及后台新增试跑是启用前提。仅初始化文件、空跑或登记虚构任务ID不构成验收。board-placeholder.json 仍为空，实际任务配置存放用户固定目录 job.json。

队列保留未就绪、缺失和位置不符的单项并继续后续正常资料；本轮临时跳过不改变未完成状态。客户端仅新增 SelectionError 类型以区分单项位置核对失败与认证/网络错误，原有正文 ID 核对及输出限制不变。
