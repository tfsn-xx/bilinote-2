import { useState, useEffect, useRef, useMemo, memo, FC } from 'react'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, Download, ArrowRight, Play, ExternalLink } from 'lucide-react'
import { toast } from 'react-hot-toast'
import Error from '@/components/Lottie/error.tsx'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark as codeStyle } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import gfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeSlug from 'rehype-slug'
import 'katex/dist/katex.min.css'
import 'github-markdown-css/github-markdown-light.css'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { useTaskStore } from '@/store/taskStore'
import { noteStyles } from '@/constant/note.ts'
import { MarkdownHeader } from '@/pages/HomePage/components/MarkdownHeader.tsx'
import TranscriptViewer from '@/pages/HomePage/components/transcriptViewer.tsx'
import MarkmapEditor from '@/pages/HomePage/components/MarkmapComponent.tsx'
import ChatPanel from '@/pages/HomePage/components/ChatPanel.tsx'
import VideoBanner from '@/pages/HomePage/components/VideoBanner.tsx'
import MarkdownOutline from '@/pages/HomePage/components/MarkdownOutline.tsx'
import {
  extractMarkdownHeadings,
  normalizeHeadingKey,
  normalizeMarkdownForRendering,
} from '@/pages/HomePage/components/markdownNavigation.ts'

interface VersionNote {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at?: string
}

interface MarkdownViewerProps {
  content: string | VersionNote[]
  status: 'idle' | 'loading' | 'success' | 'failed'
}

const steps = [
  { label: '排队中', key: 'PENDING' },
  { label: '解析链接', key: 'PARSING' },
  { label: '下载音频', key: 'DOWNLOADING' },
  { label: '转写文字', key: 'TRANSCRIBING' },
  { label: '总结内容', key: 'SUMMARIZING' },
  { label: '整理格式', key: 'FORMATTING' },
  { label: '保存完成', key: 'SAVING' },
  { label: '完成', key: 'SUCCESS' },
]

function formatDuration(seconds?: number) {
  if (seconds === undefined || !Number.isFinite(seconds)) return ''
  const total = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(total / 60)
  const remainder = total % 60
  return minutes > 0 ? `${minutes}分${String(remainder).padStart(2, '0')}秒` : `${remainder}秒`
}

const phaseLabels: Record<string, string> = {
  fetching: '获取视频信息',
  video_fetch: '获取视频素材',
  audio_download: '下载音频',
  subtitle_fetch: '获取字幕',
  transcribing: '字幕/音频转写',
  summarize_prepare: '准备总结请求',
  summarize_chunk: 'AI 分段总结',
  summarizing_chunks: 'AI 分段总结',
  merging: '汇总分段结果',
  rendering: '整理或保存笔记',
  completed: '已完成',
  failed: '任务失败',
}

const errorLabels: Record<string, string> = {
  SOURCE_FETCH_FAILED: '视频信息获取失败',
  SUBTITLE_FETCH_FAILED: '字幕获取失败',
  AUDIO_DOWNLOAD_FAILED: '音频下载失败',
  TRANSCRIPTION_FAILED: '音频转写失败',
  TRANSCRIPTION_EMPTY: '转写结果为空或格式错误',
  SUMMARY_PREPARATION_FAILED: '总结请求准备失败',
  MODEL_AUTH_FAILED: 'AI 模型认证失败',
  MODEL_BAD_REQUEST: 'AI 模型请求参数错误',
  MODEL_NOT_AVAILABLE: '所选模型不可用',
  CONTEXT_LENGTH_EXCEEDED: 'AI 模型上下文长度超限',
  MODEL_CONNECTION_ERROR: 'AI 模型连接失败',
  MODEL_TIMEOUT: 'AI 模型超时',
  MODEL_RATE_LIMITED: 'AI 模型限流或余额不足',
  MODEL_UPSTREAM_UNAVAILABLE: '中转站上游暂不可用',
  MODEL_REASONING_EXHAUSTED: 'AI 推理耗尽输出额度',
  MODEL_RESPONSE_INVALID: 'AI 总结响应格式错误',
  MERGE_FAILED: '分段汇总失败',
  PERSISTENCE_FAILED: '结果保存失败',
  CANCELLED: '用户已取消任务',
  UNKNOWN_ERROR: '未知错误',
}

const remarkPlugins = [gfm, remarkMath]
const rehypePlugins = [rehypeKatex, rehypeSlug]

/**
 * 构建 ReactMarkdown components 对象，baseURL 用于修正图片路径。
 * 使用函数 + useMemo 避免每次渲染都创建新的函数实例。
 */
function createMarkdownComponents(baseURL: string, getMarkdownContainer: () => HTMLElement | null) {
  return {
    h1: ({ children, ...props }: any) => (
      <h1
        className="text-primary my-6 scroll-m-20 text-3xl font-extrabold tracking-tight lg:text-4xl"
        {...props}
      >
        {children}
      </h1>
    ),
    h2: ({ children, ...props }: any) => (
      <h2
        className="text-primary mt-10 mb-4 scroll-m-20 border-b pb-2 text-2xl font-semibold tracking-tight first:mt-0"
        {...props}
      >
        {children}
      </h2>
    ),
    h3: ({ children, ...props }: any) => (
      <h3
        className="text-primary mt-8 mb-4 scroll-m-20 text-xl font-semibold tracking-tight"
        {...props}
      >
        {children}
      </h3>
    ),
    h4: ({ children, ...props }: any) => (
      <h4
        className="text-primary mt-6 mb-2 scroll-m-20 text-lg font-semibold tracking-tight"
        {...props}
      >
        {children}
      </h4>
    ),
    p: ({ children, ...props }: any) => (
      <p className="leading-7 [&:not(:first-child)]:mt-6" {...props}>
        {children}
      </p>
    ),
    a: ({ href, children, ...props }: any) => {
      const isOriginLink =
        typeof children[0] === 'string' &&
        (children[0] as string).startsWith('原片 @')

      if (isOriginLink) {
        const timeMatch = (children[0] as string).match(/原片 @ (\d{2}:\d{2})/)
        const timeText = timeMatch ? timeMatch[1] : '原片'

        return (
          <span className="origin-link my-2 inline-flex">
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
              {...props}
            >
              <Play className="h-3.5 w-3.5" />
              <span>原片（{timeText}）</span>
            </a>
          </span>
        )
      }

      // 处理笔记内部锚点链接（如目录跳转）
      if (href?.startsWith('#')) {
        const handleAnchorClick = (e: React.MouseEvent) => {
          e.preventDefault()
          let id = href.slice(1)
          try {
            id = decodeURIComponent(id)
          } catch {
            // Keep the raw anchor when an upstream model emits malformed escaping.
          }

          const container = getMarkdownContainer()
          const headings = container
            ? Array.from(container.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
            : []

          // 1. 优先精确匹配 id
          let target: HTMLElement | undefined = headings.find(heading => heading.id === id)

          // 2. 精确失败时按 heading 文本模糊匹配
          // LLM 生成的目录锚点可能和 heading 实际文本不完全一致
          //（例如 heading 带 *Content-[00:00]* 后缀，目录链接里没有）
          if (!target) {
            const anchorKey = normalizeHeadingKey(id)
            const labelKey = normalizeHeadingKey(e.currentTarget.textContent || '')
            for (const h of headings) {
              const headingKey = normalizeHeadingKey(h.textContent || '')
              if (
                headingKey === labelKey ||
                headingKey === anchorKey ||
                (labelKey.length >= 4 && headingKey.includes(labelKey)) ||
                (anchorKey.length >= 4 && headingKey.includes(anchorKey))
              ) {
                target = h
                break
              }
            }
          }

          if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' })
          } else {
            toast.error('未找到对应章节')
          }
        }

        return (
          <a
            href={href}
            onClick={handleAnchorClick}
            className="text-primary hover:text-primary/80 inline-flex items-center gap-0.5 font-medium underline underline-offset-4"
            {...props}
          >
            {children}
          </a>
        )
      }

      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:text-primary/80 inline-flex items-center gap-0.5 font-medium underline underline-offset-4"
          {...props}
        >
          {children}
          {href?.startsWith('http') && (
            <ExternalLink className="ml-0.5 inline-block h-3 w-3" />
          )}
        </a>
      )
    },
    img: ({ node, ...props }: any) => {
      let src = props.src
      if (src.startsWith('/')) {
        src = baseURL + src
      }
      props.src = src

      return (
        <div className="my-8 flex justify-center">
          <Zoom>
            <img
              {...props}
              className="max-w-full cursor-zoom-in rounded-lg object-cover shadow-md transition-all hover:shadow-lg"
              style={{ maxHeight: '500px' }}
            />
          </Zoom>
        </div>
      )
    },
    strong: ({ children, ...props }: any) => (
      <strong className="text-primary font-bold" {...props}>
        {children}
      </strong>
    ),
    li: ({ children, ...props }: any) => {
      const rawText = String(children)
      const isFakeHeading = /^(\*\*.+\*\*)$/.test(rawText.trim())

      if (isFakeHeading) {
        return (
          <div className="text-primary my-4 text-lg font-bold">{children}</div>
        )
      }

      return (
        <li className="my-1" {...props}>
          {children}
        </li>
      )
    },
    ul: ({ children, ...props }: any) => (
      <ul className="my-6 ml-6 list-disc [&>li]:mt-2" {...props}>
        {children}
      </ul>
    ),
    ol: ({ children, ...props }: any) => (
      <ol className="my-6 ml-6 list-decimal [&>li]:mt-2" {...props}>
        {children}
      </ol>
    ),
    blockquote: ({ children, ...props }: any) => (
      <blockquote
        className="border-primary/20 text-muted-foreground mt-6 border-l-4 pl-4 italic"
        {...props}
      >
        {children}
      </blockquote>
    ),
    code: ({ inline, className, children, ...props }: any) => {
      const match = /language-(\w+)/.exec(className || '')
      const codeContent = String(children).replace(/\n$/, '')

      if (!inline && match) {
        return (
          <div className="group bg-muted relative my-6 overflow-hidden rounded-lg border shadow-sm">
            <div className="bg-muted text-muted-foreground flex items-center justify-between px-4 py-1.5 text-sm font-medium">
              <div>{match[1].toUpperCase()}</div>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(codeContent)
                  toast.success('代码已复制')
                }}
                className="bg-background/80 hover:bg-background flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors"
              >
                <Copy className="h-3.5 w-3.5" />
                复制
              </button>
            </div>
            <SyntaxHighlighter
              style={codeStyle}
              language={match[1]}
              PreTag="div"
              className="!bg-muted !m-0 !p-0"
              customStyle={{
                margin: 0,
                padding: '1rem',
                background: 'transparent',
                fontSize: '0.9rem',
              }}
              {...props}
            >
              {codeContent}
            </SyntaxHighlighter>
          </div>
        )
      }

      return (
        <code
          className="bg-muted relative rounded px-[0.3rem] py-[0.2rem] font-mono text-sm"
          {...props}
        >
          {children}
        </code>
      )
    },
    table: ({ children, ...props }: any) => (
      <div className="my-6 w-full overflow-y-auto">
        <table className="w-full border-collapse text-sm" {...props}>
          {children}
        </table>
      </div>
    ),
    th: ({ children, ...props }: any) => (
      <th
        className="border-muted-foreground/20 border px-4 py-2 text-left font-medium [&[align=center]]:text-center [&[align=right]]:text-right"
        {...props}
      >
        {children}
      </th>
    ),
    td: ({ children, ...props }: any) => (
      <td
        className="border-muted-foreground/20 border px-4 py-2 text-left [&[align=center]]:text-center [&[align=right]]:text-right"
        {...props}
      >
        {children}
      </td>
    ),
    hr: ({ ...props }: any) => (
      <hr className="border-muted-foreground/20 my-8" {...props} />
    ),
  }
}

const MarkdownViewer: FC<MarkdownViewerProps> = memo(({ status }) => {
  const [copied, setCopied] = useState(false)
  const [currentVerId, setCurrentVerId] = useState<string>('')
  const [selectedContent, setSelectedContent] = useState<string>('')
  const [modelName, setModelName] = useState<string>('')
  const [style, setStyle] = useState<string>('')
  const [createTime, setCreateTime] = useState<string>('')
  // 确保baseURL没有尾部斜杠
  const baseURL = (String(import.meta.env.VITE_API_BASE_URL || '').replace('/api','') || '').replace(/\/$/, '')
  const getCurrentTask = useTaskStore.getState().getCurrentTask
  const currentTask = useTaskStore(state => state.getCurrentTask())
  const taskStatus = currentTask?.status || 'PENDING'
  const taskProgress = currentTask?.progress
  const [, setProgressClock] = useState(0)
  const retryTask = useTaskStore.getState().retryTask
  const cancelTask = useTaskStore.getState().cancelTask
  const isMultiVersion = Array.isArray(currentTask?.markdown)
  const [showTranscribe, setShowTranscribe] = useState(false)
  const [showChat, setShowChat] = useState<false | 'half' | 'full'>(false)
  const [viewMode, setViewMode] = useState<'map' | 'preview'>('preview')
  const [outlineExpanded, setOutlineExpanded] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const markdownContentRef = useRef<HTMLDivElement>(null)

  // 后端轮询间隔内也持续刷新已用时间，避免长时间调用模型时计时器停住。
  useEffect(() => {
    if (status !== 'loading' || !taskProgress?.started_at) return
    const timer = window.setInterval(() => setProgressClock(value => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [status, taskProgress?.started_at])

  const liveElapsedSeconds = taskProgress?.started_at
    ? Math.max(
        taskProgress.elapsed_seconds || 0,
        (Date.now() - new Date(taskProgress.started_at).getTime()) / 1000
      )
    : taskProgress?.elapsed_seconds

  // 缓存 ReactMarkdown components，仅在 baseURL 变化时重建
  const markdownComponents = useMemo(
    () => createMarkdownComponents(baseURL, () => markdownContentRef.current),
    [baseURL]
  )

  const renderedContent = useMemo(
    () =>
      normalizeMarkdownForRendering(
        selectedContent.replace(/^>\s*来源链接：[^\n]*\n*/m, '')
      ),
    [selectedContent]
  )
  const outlineHeadings = useMemo(() => extractMarkdownHeadings(renderedContent), [renderedContent])

  // 多版本内容处理
  useEffect(() => {
    if (!currentTask) return

    if (!isMultiVersion) {
      setCurrentVerId('') // 清空旧版本 ID
      setModelName(currentTask.formData.model_name)
      setStyle(currentTask.formData.style)
      setCreateTime(currentTask.createdAt)
      setSelectedContent(currentTask?.markdown)
    } else {
      const latestVersion = [...currentTask.markdown].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )[0]

      if (latestVersion) {
        setCurrentVerId(latestVersion.ver_id)
      }
    }
  }, [currentTask?.id, taskStatus])
  useEffect(() => {
    if (!currentTask || !isMultiVersion) return

    const currentVer = currentTask.markdown.find(v => v.ver_id === currentVerId)
    if (currentVer) {
      setModelName(currentVer.model_name)
      setStyle(currentVer.style)
      setCreateTime(currentVer.created_at || '')
      setSelectedContent(currentVer.content)
    }
  }, [currentVerId, currentTask?.id])
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(selectedContent)
      setCopied(true)
      toast.success('已复制到剪贴板')
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      toast.error('复制失败')
    }
  }
  const alertButton = {
    id: 'alert',
    title: '测试警告',
    content: '⚠️',
    onClick: () => alert('你点击了自定义按钮！'),
  }
  const exportButton = {
    id: 'export',
    title: '导出思维导图',
    content: '⤓',
    onClick: () => {
      const svgEl = svgRef.current
      if (!svgEl) return
      // 同上面的序列化逻辑
      const serializer = new XMLSerializer()
      const source = serializer.serializeToString(svgEl)
      const blob = new Blob(['<?xml version="1.0" encoding="UTF-8"?>', source], {
        type: 'image/svg+xml;charset=utf-8',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'mindmap.svg'
      a.click()
      URL.revokeObjectURL(url)
    },
  }
  const handleDownload = () => {
    const task = getCurrentTask()
    const name = task?.audioMeta.title || 'note'
    const blob = new Blob([selectedContent], { type: 'text/markdown;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${name}.md`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  if (status === 'loading') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center space-y-4 text-neutral-500">
        <StepBar steps={steps} currentStep={taskStatus} />
        <Loading className="h-5 w-5" />
        <div className="text-center text-sm">
          <p className="text-lg font-bold">正在生成笔记，请稍候…</p>
          <p className="mt-2 text-sm text-neutral-600">
            {taskProgress?.message || '正在准备任务'}
          </p>
          {liveElapsedSeconds !== undefined && (
            <p className="mt-1 text-xs text-neutral-500">
              本次已用时：{formatDuration(liveElapsedSeconds)}
            </p>
          )}
          <Button variant="outline" size="sm" onClick={() => currentTask?.id && cancelTask(currentTask.id)}>
            取消任务
          </Button>
        </div>
      </div>
    )
  }

  if (status === 'idle') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center space-y-3 text-neutral-500">
        <Idle />
        <div className="text-center">
          <p className="text-lg font-bold">输入视频链接并点击"生成笔记"</p>
          <p className="mt-2 text-xs text-neutral-500">支持哔哩哔哩、YouTube 、抖音等视频平台</p>
        </div>
      </div>
    )
  }

  const stitchedFallback = currentTask?.progress?.merge_mode === 'stitched_fallback'

  if (status === 'failed' && !isMultiVersion) {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center gap-4 space-y-3">
        <Error />
        <div className="text-center">
          <p className="text-lg font-bold text-red-500">笔记生成失败</p>
          {(() => {
            const progress = currentTask?.progress || {}
            const code = progress.error_code
            const phase = progress.phase || 'failed'
            const reason = errorLabels[code || ''] || progress.message || '任务失败'
            return (
              <div className="mt-2 mb-3 max-w-lg space-y-1 text-left text-sm text-red-700">
                <p><span className="font-medium">失败阶段：</span>{phaseLabels[phase] || phase}</p>
                <p><span className="font-medium">具体原因：</span>{reason}</p>
                {progress.chunk_index && progress.chunk_total && (
                  <p><span className="font-medium">失败分段：</span>第 {progress.chunk_index}/{progress.chunk_total} 段</p>
                )}
                {progress.upstream_summary && (
                  <p className="break-words text-xs text-red-500">
                    上游摘要：{progress.upstream_summary}
                  </p>
                )}
                <p className="text-xs text-neutral-500">
                  {progress.retryable === false ? '不建议自动重试，请先修正配置或输入。' : '可以重试；若持续失败，请检查模型供应商状态。'}
                </p>
              </div>
            )
          })()}

          <Button onClick={() => retryTask(currentTask.id)} size="lg">
            重试
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden">
      {stitchedFallback && (
        <div className="mx-4 mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          AI 汇总失败，已按分段顺序拼接保留结果；内容完整但未经过最终统一润色。
        </div>
      )}
      <MarkdownHeader
        currentTask={currentTask}
        isMultiVersion={isMultiVersion}
        currentVerId={currentVerId}
        setCurrentVerId={setCurrentVerId}
        modelName={modelName}
        style={style}
        noteStyles={noteStyles}
        onCopy={handleCopy}
        onDownload={handleDownload}
        createAt={createTime}
        showTranscribe={showTranscribe}
        setShowTranscribe={setShowTranscribe}
        showChat={showChat}
        setShowChat={setShowChat}
        viewMode={viewMode}
        setViewMode={setViewMode}
      />

      {viewMode === 'map' ? (
        <div className="flex w-full flex-1 overflow-hidden bg-white">
          <div className={'w-full'}>
            <MarkmapEditor
              value={selectedContent}
              onChange={() => {}}
              height="100%" // 根据需求可以设定百分比或固定高度
              title={currentTask?.audioMeta?.title || '思维导图'}
            />
          </div>
        </div>
      ) : (
        <div className="relative flex flex-1 overflow-hidden bg-white py-2">
          {selectedContent && selectedContent !== 'loading' && selectedContent !== 'empty' ? (
            <>
              {showChat === 'full' && currentTask ? (
                <div className="h-full w-full">
                  <ChatPanel taskId={currentTask.id} mode="full" onModeChange={setShowChat} />
                </div>
              ) : (
              <>
              <ScrollArea className="min-w-0 flex-1">
                <div className="px-2">
                  <VideoBanner
                    audioMeta={currentTask?.audioMeta}
                    videoUrl={currentTask?.formData?.video_url}
                  />
                </div>
                <div
                  ref={markdownContentRef}
                  className={`markdown-body w-full px-2 transition-[padding] duration-200 ${outlineExpanded ? 'pr-[18.5rem]' : 'pr-14'}`}
                >
                  <ReactMarkdown
                    remarkPlugins={remarkPlugins}
                    rehypePlugins={rehypePlugins}
                    components={markdownComponents}
                  >
                    {renderedContent}
                  </ReactMarkdown>
                </div>
              </ScrollArea>
              {!showTranscribe && showChat !== 'half' && (
                <MarkdownOutline
                  containerRef={markdownContentRef}
                  headings={outlineHeadings}
                  onExpandedChange={setOutlineExpanded}
                />
              )}
              {showTranscribe && (
                <div className={'ml-2 w-2/4'}>
                  <TranscriptViewer />
                </div>
              )}
              {/* 侧边问答模式：markdown + ChatPanel 各占一半 */}
              {showChat === 'half' && currentTask && (
                <div className="ml-2 h-full w-1/2 shrink-0">
                  <ChatPanel taskId={currentTask.id} mode="half" onModeChange={setShowChat} />
                </div>
              )}
              </>
              )}
            </>
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <div className="w-[300px] flex-col justify-items-center">
                <div className="bg-primary-light mb-4 flex h-16 w-16 items-center justify-center rounded-full">
                  <ArrowRight className="text-primary h-8 w-8" />
                </div>
                <p className="mb-2 text-neutral-600">输入视频链接并点击"生成笔记"按钮</p>
                <p className="text-xs text-neutral-500">支持哔哩哔哩、YouTube等视频网站</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
})

MarkdownViewer.displayName = 'MarkdownViewer'

export default MarkdownViewer
