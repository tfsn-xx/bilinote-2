import request from '@/utils/request'

export const cookieLoginUrl =
  '/cookie-login/vnc.html?autoconnect=1&resize=scale&reconnect=1&path=cookie-login/websockify'

export type CookieCaptureSession = {
  id: string
  platform: string
  state: 'waiting'
}

export type CookieCaptureResult =
  | { platform: string; state: 'waiting' }
  | { platform: string; state: 'ready'; cookie: string }

export async function cookieHelperRequest<T>(path: string): Promise<T> {
  return request.post<never, T>('/cookie_capture' + path, {}, { timeout: 20000 })
}
