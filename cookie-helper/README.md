# Windows Cookie 获取服务

此服务配合网页下载配置中的“获取 Cookie”按钮使用，支持哔哩哔哩、YouTube、抖音和快手。

## 一次安装，随 Docker 容器运行

需要 Windows、PowerShell 7、Node.js 22 或更新版本，以及 Chrome 或 Edge。服务在 Windows 主机运行，Docker 中的 BiliNote 不需要安装浏览器。

在项目根目录运行一次：

```powershell
pwsh -File .\cookie-helper\install.ps1
```

之后直接在 Docker Desktop 启动或停止 `bilinote-2`：Cookie 服务会在约 1–2 秒内开放或关闭 `127.0.0.1:18765`。首次进入“下载配置”默认显示哔哩哔哩表单，无需再点平台或刷新页面。

原生 Windows 浏览器不能由 Linux 容器直接启动，因此由轻量 Windows 桥接监听 Docker 状态。Cookie 服务只在容器运行时开放；桥接空闲时不打开浏览器。没有 Windows 登录触发器，计划任务中的定时检查用于恢复桥接进程。安装脚本将运行文件复制到固定目录，移动项目不会影响它。

固定目录为 Windows 公用文档中的 `BiliNote/<当前用户 SID>/`，权限限定为当前用户和 SYSTEM；其中 `cookie-helper/monitor.log` 记录启动错误和服务状态，不记录 Cookie。原有 `%LOCALAPPDATA%\BiliNote\cookie-browser` 登录资料会在首次安装时迁移到该目录的 `cookie-browser/`，后续继续复用。

停止并移除桥接任务：

```powershell
Stop-ScheduledTask -TaskName BiliNoteCookieDockerBridge
Unregister-ScheduledTask -TaskName BiliNoteCookieDockerBridge -Confirm:$false
```

进入 BiliNote 的“设置 → 下载配置”，选择平台并点击“获取 Cookie”。首次在弹出的专用浏览器窗口登录，完成后会自动填入并保存；保存失败时，可用原有“保存”按钮重试。获取过程中可取消，切换平台也会取消尚未完成的获取。

服务只监听 `127.0.0.1:18765`，仅导出所选平台的 Cookie，不输出 Cookie 到日志。

## 自定义网页端口

默认允许本机 `3015` 和 `18080` 端口（`localhost`、`127.0.0.1`、`[::1]`）。独立手动启动模式支持其他端口，例如：

```powershell
$env:BILINOTE_COOKIE_ORIGINS = 'http://localhost:8080,http://127.0.0.1:8080'
node .\cookie-helper\server.mjs
```

该配置对新启动的服务生效。

## 验证

```powershell
node --test cookie-helper/helper.test.mjs cookie-helper/browser.integration.test.mjs cookie-helper/lifecycle.test.mjs
```

HTTP 测试使用模拟登录信息；Windows 浏览器集成测试在临时独立配置目录内验证四个平台的 HttpOnly Cookie 读取，结束后关闭浏览器并清理目录。
