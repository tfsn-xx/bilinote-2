import { useEffect, useRef } from 'react'
import { useTaskStore } from '@/store/taskStore'
import type { AudioMeta, Markdown, TaskProgress, TaskStatus, Transcript } from '@/store/taskStore'
import { get_task_status } from '@/services/note.ts'
import toast from 'react-hot-toast'

type TaskStatusResponse = {
  status?: string
  message?: string
  result?: {
    markdown?: string | Markdown[]
    transcript?: Transcript
    audio_meta?: AudioMeta
    duration_seconds?: number
  }
  duration_seconds?: number
  phase?: string
  error_code?: string
  retryable?: boolean
  chunk_index?: number
  chunk_total?: number
  attempt?: number
  upstream_summary?: string
  merge_mode?: string
  merge_warning?: string
  merge_error_code?: string
  merge_attempt?: number
  merge_upstream_summary?: string
  completed_chunks?: number
  started_at?: string
  phase_started_at?: string
  finished_at?: string
  elapsed_seconds?: number
}

export const useTaskPolling = (interval = 3000) => {
  const tasks = useTaskStore(state => state.tasks)
  const updateTaskContent = useTaskStore(state => state.updateTaskContent)

  const tasksRef = useRef(tasks)
  const completedMetaRequested = useRef(new Set<string>())

  // 每次 tasks 更新，把最新的 tasks 同步进去
  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    const timer = setInterval(async () => {
      const pendingTasks = tasksRef.current.filter(task => {
        if (task.status !== 'SUCCESS' && task.status !== 'FAILED') return true
        // 对没有耗时元数据的旧成功任务补拉一次状态，保证刷新后也能显示生成用时。
        return (
          task.status === 'SUCCESS' &&
          task.progress?.duration_seconds === undefined &&
          task.generation_duration_seconds === undefined &&
          !completedMetaRequested.current.has(task.id)
        )
      })

      // 无活跃任务时跳过轮询
      if (pendingTasks.length === 0) return

      for (const task of pendingTasks) {
        try {
          if (task.status === 'SUCCESS') completedMetaRequested.current.add(task.id)
          const res = await get_task_status(task.id) as unknown as TaskStatusResponse
          const status = res.status as TaskStatus | undefined
          const result = res.result || {}
          const durationSeconds = res.duration_seconds ?? result.duration_seconds
          const progress: TaskProgress = {
            message: res.message,
            phase: res.phase,
            error_code: res.error_code,
            retryable: res.retryable,
            chunk_index: res.chunk_index,
            chunk_total: res.chunk_total,
            attempt: res.attempt,
            upstream_summary: res.upstream_summary,
            merge_mode: res.merge_mode,
            merge_warning: res.merge_warning,
            merge_error_code: res.merge_error_code,
            merge_attempt: res.merge_attempt,
            merge_upstream_summary: res.merge_upstream_summary,
            completed_chunks: res.completed_chunks,
            started_at: res.started_at,
            phase_started_at: res.phase_started_at,
            finished_at: res.finished_at,
            elapsed_seconds: res.elapsed_seconds,
            duration_seconds: durationSeconds,
          }

          // Keep the message/timer fresh even when the coarse status has not
          // changed. This is what makes the waiting screen informative instead
          // of looking frozen between phase transitions.
          if (status === 'SUCCESS') {
            const { markdown, transcript, audio_meta } = result
            if (task.status !== 'SUCCESS') toast.success('笔记生成成功')
            updateTaskContent(task.id, {
              status,
              markdown,
              transcript,
              audioMeta: audio_meta,
              progress,
              generation_duration_seconds: durationSeconds,
            })
          } else if (status === 'FAILED') {
            updateTaskContent(task.id, { status, progress })
            if (task.status !== 'FAILED') console.warn(`⚠️ 任务 ${task.id} 失败`)
          } else if (status) {
            updateTaskContent(task.id, { status, progress })
          }
        } catch (e) {
          console.error('❌ 任务轮询失败：', e)
          const candidate = e as { data?: unknown }
          const failure = (
            candidate && typeof candidate.data === 'object' && candidate.data !== null
              ? candidate.data
              : e
          ) as TaskStatusResponse & { msg?: string }
          if (failure?.status === 'FAILED' || failure?.error_code) {
            updateTaskContent(task.id, {
              status: 'FAILED',
              progress: {
                message: failure.message || failure.msg || '任务失败',
                phase: failure.phase || 'failed',
                error_code: failure.error_code,
                retryable: failure.retryable,
                chunk_index: failure.chunk_index,
                chunk_total: failure.chunk_total,
                attempt: failure.attempt,
                upstream_summary: failure.upstream_summary,
                merge_mode: failure.merge_mode,
                merge_warning: failure.merge_warning,
                merge_error_code: failure.merge_error_code,
                merge_attempt: failure.merge_attempt,
                merge_upstream_summary: failure.merge_upstream_summary,
              },
            })
          } else {
            updateTaskContent(task.id, {
              progress: {
                ...(task.progress || {}),
                message: '任务状态暂时无法获取，正在等待后端恢复',
              },
            })
          }
        }
      }
    }, interval)

    return () => clearInterval(timer)
  }, [interval])
}
