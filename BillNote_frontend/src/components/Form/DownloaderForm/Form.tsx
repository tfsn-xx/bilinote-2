import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { getDownloaderCookie, updateDownloaderCookie } from '@/services/downloader'
import {
  cookieHelperRequest,
  cookieLoginUrl,
  type CookieCaptureSession,
  type CookieCaptureResult,
} from '@/services/cookieCapture'
import { useParams } from 'react-router-dom'
import { videoPlatforms } from '@/constant/note.ts'

const CookieSchema = z.object({
  cookie: z.string().min(10, '请填写有效 Cookie'),
})

type CaptureOperation = {
  id?: string
  cancelled: boolean
  timer?: ReturnType<typeof setTimeout>
  deadline: number
}

function stopCapture(operation: CaptureOperation | null) {
  if (!operation) return
  operation.cancelled = true
  clearTimeout(operation.timer)
  if (operation.id) {
    void cookieHelperRequest(`/session/${operation.id}/cancel`).catch(() => {})
  }
}

const DownloaderForm = () => {
  const form = useForm({
    resolver: zodResolver(CookieSchema),
    defaultValues: { cookie: '' },
  })
  const platform = useParams().id!
  const { reset } = form

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [message, setMessage] = useState('')
  const operation = useRef<CaptureOperation | null>(null)

  useEffect(() => {
    let active = true
    setMessage('')
    setCapturing(false)
    const loadCookie = async () => {
      setLoading(true)
      try {
        const res = await getDownloaderCookie(platform)
        if (active) reset({ cookie: res?.cookie || '' })
      } catch {
        if (active) {
          toast.error('加载 Cookie 失败')
          reset({ cookie: '' })
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    void loadCookie()
    return () => {
      active = false
      stopCapture(operation.current)
      operation.current = null
    }
  }, [platform, reset])

  const onSubmit = async (values: z.infer<typeof CookieSchema>) => {
    setSaving(true)
    try {
      await updateDownloaderCookie({
        platform,
        cookie: values.cookie,
      })
      toast.success('保存成功')
    } catch {
      toast.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const cancelCapture = () => {
    stopCapture(operation.current)
    operation.current = null
    setCapturing(false)
  }

  const captureCookie = async () => {
    // Open synchronously from the click so the browser treats this as a user action.
    window.open(cookieLoginUrl, 'bilinote-cookie-login')
    const current: CaptureOperation = {
      cancelled: false,
      deadline: Date.now() + 5 * 60 * 1000,
    }
    operation.current = current
    setCapturing(true)
    setMessage('正在打开平台登录窗口…')
    const fail = (error: unknown) => {
      if (current.cancelled) return
      cancelCapture()
      const text = error instanceof Error ? error.message : '获取 Cookie 失败'
      setMessage(text)
      toast.error(text)
    }

    try {
      const session = await cookieHelperRequest<CookieCaptureSession>('/capture/' + platform)
      current.id = session.id
      if (current.cancelled) {
        void cookieHelperRequest(`/session/${session.id}/cancel`).catch(() => {})
        return
      }
      setMessage('请在新打开的窗口中登录，完成后会自动获取并保存 Cookie。')
      const poll = async () => {
        if (current.cancelled) return
        if (Date.now() > current.deadline) {
          fail(new Error('等待登录超时，请重新获取。'))
          return
        }
        try {
          const result = await cookieHelperRequest<CookieCaptureResult>(`/session/${session.id}`)
          if (current.cancelled) return
          if (result.state === 'waiting') {
            current.timer = setTimeout(poll, 1500)
            return
          }
          if (result.platform !== platform) throw new Error('平台不匹配，请重新获取。')
          // 保存失败时保留输入值，用户可以用原有“保存”按钮重试。
          form.setValue('cookie', result.cookie, { shouldDirty: true, shouldValidate: true })
          setMessage('Cookie 已获取，正在保存…')
          try {
            await updateDownloaderCookie({ platform, cookie: result.cookie })
          } catch {
            throw new Error('Cookie 已填入，但保存失败，请点击“保存”重试。')
          }
          if (current.cancelled) return
          operation.current = null
          setCapturing(false)
          setMessage('Cookie 已获取并保存。')
          toast.success('Cookie 已获取并保存')
        } catch (error) {
          fail(error)
        }
      }
      current.timer = setTimeout(poll, 500)
    } catch (error) {
      fail(error)
    }
  }

  if (loading) return <div className="p-4">加载中...</div>

  return (
    <div className="max-w-xl p-4">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <div className="text-lg font-bold">
            设置{videoPlatforms.find(item => item.value === platform)?.label}下载器 Cookie
          </div>

          <FormField
            control={form.control}
            name="cookie"
            render={({ field }) => (
              <FormItem className="flex flex-col gap-2">
                <FormLabel>Cookie</FormLabel>
                <FormControl>
                  <Input {...field} placeholder="输入 Cookie" disabled={capturing} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              className="flex-1"
              disabled={saving}
              onClick={
                capturing
                  ? () => {
                      cancelCapture()
                      setMessage('已取消获取。')
                    }
                  : captureCookie
              }
            >
              {capturing ? '取消获取' : '获取 Cookie'}
            </Button>
            <Button type="submit" className="flex-1" disabled={capturing || saving}>
              {saving ? '保存中…' : '保存'}
            </Button>
          </div>
          <p role="status" aria-live="polite" className="text-sm text-gray-500">
            {message || '首次获取需在弹出的浏览器窗口登录；后续会保留登录状态，自动获取并保存。'}
          </p>
          {capturing && (
            <a
              href={cookieLoginUrl}
              target="bilinote-cookie-login"
              className="text-sm text-blue-600 underline"
            >
              打开登录窗口
            </a>
          )}
        </form>
      </Form>
    </div>
  )
}

export default DownloaderForm
