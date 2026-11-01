const helperUrl = 'http://127.0.0.1:18765'

export type CookieCaptureSession = {
  id: string
  platform: string
  state: 'waiting'
}

export type CookieCaptureResult =
  | { platform: string; state: 'waiting' }
  | { platform: string; state: 'ready'; cookie: string }

export async function cookieHelperRequest<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(helperUrl + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal: AbortSignal.timeout(20000),
    })
  } catch {
    throw new Error('Cookie 获取服务未连接，请运行项目 cookie-helper 目录中的 start.ps1 后重试。')
  }
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '获取 Cookie 失败')
  return result
}
