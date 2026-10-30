import request from '@/utils/request.ts'

export const getDownloaderCookie = async (id: string) => {
  return await request.get<never, { cookie: string; platform: string } | null>(
    '/get_downloader_cookie/' + id
  )
}

export const updateDownloaderCookie = async (data: { cookie: string; platform: string }) => {
  return await request.post('/update_downloader_cookie', data)
}
