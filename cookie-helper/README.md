# Windows Cookie 获取服务

此服务配合网页下载配置中的“获取 Cookie”按钮使用，支持哔哩哔哩、YouTube、抖音和快手。

## 一次安装，点击按钮时启动

需要 Windows、PowerShell 7、Node.js 22 或更新版本，以及 Chrome 或 Edge。服务在 Windows 主机运行，Docker 中的 BiliNote 不需要安装浏览器。

在项目根目录运行一次：

```powershell
pwsh -File .\cookie-helper\install.ps1
```

之后进入 BiliNote 的“设置 → 下载配置”，点击“获取 Cookie”即可。服务未运行时，按钮通过 `bilinote-cookie://start` 唤起本机助手；浏览器首次询问是否打开 BiliNote Cookie 助手时，选择允许。助手启动后继续打开原生 Chrome/Edge 平台登录窗口，完成登录后自动填入并保存 Cookie。

助手由无控制台的 Windows 启动器运行，不显示 Node 命令窗口。没有 Docker 状态监听、计划任务、开机启动或登录启动。没有获取会话时，服务空闲 30 秒自动退出；等待登录时最长保留 5 分钟。取消或完成获取后重新开始空闲计时，下次点击按钮会再次唤起。

固定目录为 Windows 公用文档中的 `BiliNote/<当前用户 SID>/`，权限限定为当前用户和 SYSTEM；其中 `cookie-helper/helper.log` 仅记录启动错误，不记录 Cookie。原有登录资料继续保留在 `cookie-browser/`，旧版 `%LOCALAPPDATA%\BiliNote\cookie-browser` 会在首次安装时迁移。

安装脚本会移除旧版的 Docker 监听任务和登录启动项。首次进入“下载配置”默认显示哔哩哔哩表单，无需选择平台或刷新。保存失败时可用原有“保存”按钮重试；获取过程中可取消，切换平台也会取消尚未完成的获取。

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
node --test cookie-helper/helper.test.mjs cookie-helper/browser.integration.test.mjs cookie-helper/on-demand.test.mjs
```

HTTP 测试使用模拟登录信息；Windows 浏览器集成测试在临时独立配置目录内验证四个平台的 HttpOnly Cookie 读取，结束后关闭浏览器并清理目录。
