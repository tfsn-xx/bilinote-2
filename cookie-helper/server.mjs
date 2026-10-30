import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { LoginBrowser } from './browser.mjs';

const ORIGINS = new Set([
  ...[3015, 18080].flatMap(port => [
    `http://localhost:${port}`, `http://127.0.0.1:${port}`, `http://[::1]:${port}`,
  ]),
  ...(process.env.BILINOTE_COOKIE_ORIGINS || '').split(',').map(origin => origin.trim()).filter(Boolean),
]);
const SESSION_TTL = 5 * 60 * 1000;

export function createHelper(browser = new LoginBrowser()) {
  const sessions = new Map();
  return createServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Vary', 'Origin');
    const send = (status, data) => {
      response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(data));
    };
    if (!ORIGINS.has(request.headers.origin)) return send(403, { error: '仅允许本机 BiliNote 页面调用。' });
    response.setHeader('Access-Control-Allow-Origin', request.headers.origin);
    response.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    response.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    response.setHeader('Access-Control-Allow-Private-Network', 'true');
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return; }
    if (request.method !== 'POST' || !request.headers['content-type']?.startsWith('application/json')) {
      return send(405, { error: '请使用 JSON POST 请求。' });
    }
    for (const [id, session] of sessions) if (session.expires < Date.now()) sessions.delete(id);
    try {
      const start = /^\/capture\/(bilibili|youtube|douyin|kuaishou)$/.exec(request.url);
      if (start) {
        const platform = start[1];
        await browser.open(platform);
        const id = randomUUID();
        sessions.set(id, { platform, expires: Date.now() + SESSION_TTL });
        return send(200, { id, platform, state: 'waiting' });
      }
      const poll = /^\/session\/([\da-f-]+)(\/cancel)?$/.exec(request.url);
      if (!poll) return send(404, { error: '不支持的平台或操作。' });
      const session = sessions.get(poll[1]);
      if (!session) return send(410, { error: '获取已超时或结束，请重新获取。' });
      if (poll[2]) {
        sessions.delete(poll[1]);
        return send(200, { state: 'cancelled' });
      }
      const cookie = await browser.capture(session.platform);
      if (!cookie) return send(200, { platform: session.platform, state: 'waiting' });
      sessions.delete(poll[1]);
      return send(200, { platform: session.platform, state: 'ready', cookie });
    } catch (error) {
      send(500, { error: error.message });
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createHelper();
  server.on('error', error => {
    if (error.code !== 'EADDRINUSE') console.error('Cookie helper:', error.message);
    process.exit(error.code === 'EADDRINUSE' ? 0 : 1);
  });
  server.listen(18765, '127.0.0.1', () => console.log('BiliNote Cookie helper ready on 127.0.0.1:18765'));
}
