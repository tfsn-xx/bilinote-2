# Windows Cookie 获取服务

此服务配合网页下载配置中的“获取 Cookie”按钮使用，支持哔哩哔哩、YouTube、抖音和快手。

## 启动

需要 Windows、PowerShell 7、Node.js 22 或更新版本，以及 Chrome 或 Edge。服务在 Windows 主机运行，Docker 中的 BiliNote 不需要安装浏览器。

在项目根目录运行：

```powershell
pwsh -File .\cookie-helper\start.ps1
```

如需登录 Windows 后自动启动，运行：

```powershell
pwsh -File .\cookie-helper\install.ps1
```

自动启动项指向当前项目目录，移动项目后重新运行安装脚本。移除自动启动项：

```powershell
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name BiliNoteCookieHelper
```

进入 BiliNote 的“设置 → 下载配置”，选择平台并点击“获取 Cookie”。首次在弹出的专用浏览器窗口登录，完成后会自动填入并保存；保存失败时，可用原有“保存”按钮重试。获取过程中可取消，切换平台也会取消尚未完成的获取。

登录资料保存在 `%LOCALAPPDATA%\BiliNote\cookie-browser`，后续获取会复用该登录状态。服务只监听 `127.0.0.1:18765`，仅导出所选平台的 Cookie，不输出 Cookie 到日志。

## 自定义网页端口

默认允许本机 `3015` 和 `18080` 端口（`localhost`、`127.0.0.1`、`[::1]`）。使用其他端口时，在启动服务前配置网页来源，例如：

```powershell
$env:BILINOTE_COOKIE_ORIGINS = 'http://localhost:8080,http://127.0.0.1:8080'
pwsh -File .\cookie-helper\start.ps1
```

该配置对新启动的服务生效。自动启动时需要读取同一配置，可将 `BILINOTE_COOKIE_ORIGINS` 设置为 Windows 用户环境变量。

## 验证

```powershell
node --test cookie-helper/helper.test.mjs cookie-helper/browser.integration.test.mjs
```

HTTP 测试使用模拟登录信息；Windows 浏览器集成测试在临时独立配置目录内验证四个平台的 HttpOnly Cookie 读取，结束后关闭浏览器并清理目录。
