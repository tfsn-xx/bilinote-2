# BiliNote-2

> 非官方社区版本，基于 [BiliNote](https://github.com/JefferyHcool/BiliNote) v2.4.4 修改。
> 本项目保留原项目的 MIT 许可证与作者署名，详见 [LICENSE](./LICENSE) 和 [NOTICE.md](./NOTICE.md)。

BiliNote-2 重点改进了长视频总结的稳定性、任务进度反馈、Bilibili 下载与字幕获取，以及 Docker 部署体验。

## 主要改进

### 1. 长视频总结更稳定

- 按 Token 数量智能切分长文本，降低上下文超限和内容截断的概率。
- 支持分块并发、失败重试和断点续跑，单个分块失败时尽量保留已完成结果。
- 优化流式输出、分块合并与章节衔接，减少长视频总结中断或内容重复。
- 模型调用失败时提供回退机制，并显示更明确的失败原因。

### 2. 任务进度和错误处理

- 增加下载、字幕获取、转写、总结、保存等阶段的实时进度。
- 支持取消任务和失败后重试。
- 后端使用结构化错误信息，前端可以显示更具体的处理建议。
- 优化任务状态轮询和结果渲染，减少页面卡顿与状态不同步。

### 3. Bilibili 下载和字幕获取

- 优化 Bilibili 请求参数、重试策略和浏览器特征模拟，提高下载成功率。
- 优先读取视频官方字幕；没有字幕时再调用 Whisper 转写。
- 可识别 Cookie 失效或登录状态异常，并给出相应提示。
- 字幕接口不可用时自动回退到音频下载与语音转写流程。

### 4. 转写和模型配置

- Whisper 改为按需初始化，存在官方字幕时不再提前加载模型。
- 正确使用配置中的 Whisper 模型大小，不再固定回退到 tiny。
- 改进模型下载状态、代理配置和 Hugging Face 下载错误提示。
- 完善大模型服务商、模型名称、Base URL 和备用模型配置。

### 5. 笔记阅读体验

- Markdown 笔记增加二级、三级标题悬浮目录。
- 支持滚动同步、当前章节高亮和目录折叠。
- 优化时间戳跳转及长内容的展示体验。

## Docker 部署

### 环境要求

- Docker Desktop，或 Docker Engine + Docker Compose v2。
- 建议至少 4 GB 可用内存；使用 medium 或更大的 Whisper 模型时建议 8 GB 以上。
- Windows 用户建议把项目放在不含中文和特殊字符的路径中。

### 1. 下载项目

```bash
git clone https://github.com/tfsn-xx/bilinote-2.git
cd bilinote-2
```

### 2. 创建环境配置

Linux / macOS：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

常用配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_PORT` | `3015` | Web 访问端口 |
| `TRANSCRIBER_TYPE` | `fast-whisper` | 默认语音转写方式 |
| `WHISPER_MODEL_SIZE` | `tiny` | Whisper 模型，可改为 `base`、`small`、`medium` 等 |
| `HF_ENDPOINT` | 镜像地址 | Hugging Face 下载地址；下载异常时可改为官方地址或配置代理 |

大模型 API Key、模型名称和 Base URL 建议在启动后的网页设置中填写，不需要直接写入 `.env`。

### 3. 启动服务

```bash
docker compose up -d --build
```

启动完成后访问：

```text
http://localhost:3015
```

如果修改了 `APP_PORT`，请使用修改后的端口。首次启动需要构建镜像和下载依赖，耗时会比后续启动更长。

### 4. 完成网页配置

进入网页后：

1. 在模型设置中填写大模型服务商、API Key、模型名称和 Base URL。
2. 如需处理受登录状态限制的视频，在下载器设置中填写有效的 Bilibili Cookie。
3. 创建任务并选择字幕、转写和总结相关选项。

## 常用命令

```bash
# 查看服务状态
docker compose ps

# 查看后端日志
docker compose logs -f backend

# 查看全部日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止并移除容器（不会删除宿主机中的项目数据）
docker compose down

# 重新构建并启动
docker compose up -d --build
```

## 数据保存

Docker Compose 会把宿主机的 `backend/` 目录挂载到后端容器，因此数据库、配置、截图、上传文件、模型和生成的笔记会保存在项目目录中。

迁移或重装前，建议至少备份以下目录（实际目录可能受 `.env` 配置影响）：

- `backend/data/`
- `backend/config/`
- `backend/static/`
- `backend/uploads/`
- `backend/models/`
- `backend/note_results/`

## 更新版本

```bash
git pull
docker compose up -d --build
```

更新前建议先备份 `backend/` 中的重要数据和配置。

## 常见问题

### 后端无法启动

先查看日志：

```bash
docker compose logs --tail=200 backend
```

重点检查端口占用、`.env` 格式、内存不足和模型下载错误。

### Whisper 模型下载失败

- 检查 Docker 是否能够访问外网。
- 根据网络环境修改 `HF_ENDPOINT`，或为 Docker 配置代理。
- Windows Docker Desktop 中，容器访问宿主机代理通常可使用 `host.docker.internal`，不要直接填写 `127.0.0.1`。

### 无法获取 Bilibili 字幕或视频

- 更新 Bilibili Cookie，并确认账号可以正常访问目标视频。
- 视频没有官方字幕时，系统会尝试下载音频并使用 Whisper 转写。
- 部分会员、地区限制、付费或风控视频仍可能无法处理。

### 内存不足或转写过慢

- 优先使用 `tiny`、`base` 或 `small` 模型。
- 为 Docker Desktop 分配更多内存。
- 如果有可用的云端转写服务，可以在网页设置中切换转写方式。

### 端口被占用

修改 `.env` 中的 `APP_PORT`，例如：

```env
APP_PORT=8080
```

然后重新执行：

```bash
docker compose up -d
```

## 许可证与说明

本项目基于 BiliNote 修改并按照 MIT License 开源。使用、修改或再发布时，请保留原项目许可证、版权声明和本项目的修改说明。

BiliNote-2 是非官方社区版本，与 Bilibili 官方无关。使用本项目时，请遵守相关平台服务条款、版权规定及所在地法律法规。
