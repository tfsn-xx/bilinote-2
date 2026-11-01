const helperUrl = 'http://127.0.0.1:18765'

async function helperIsReady(): Promise<boolean> {
  try {
    const response = await fetch(helperUrl + '/health', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      signal: AbortSignal.timeout(1000),
    })
    return response.ok && (await response.json()).service === 'bilinote-cookie-helper'
  } catch {
    return false
  }
}

export async function startCookieHelper(isCancelled: () => boolean): Promise<void> {
  if (isCancelled()) return
  // Keep the external-protocol launch inside the user's click, before any await.
  window.location.assign('bilinote-cookie://start')
  const deadline = Date.now() + 15000
  while (!isCancelled() && Date.now() < deadline) {
    if (await helperIsReady()) return
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  if (!isCancelled()) {
    throw new Error('未能启动 Cookie 助手。如果浏览器没有弹出“打开应用”提示，请点击下方“打开 Cookie 助手”后重试获取。')
  }
}

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
    throw new Error('Cookie 助手已退出，请重新点击“获取 Cookie”。')
  }
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || '获取 Cookie 失败')
  return result
}
