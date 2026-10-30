import { test } from 'node:test';
import assert from 'node:assert/strict';
import { platformCookies } from './browser.mjs';
import { createHelper } from './server.mjs';

const cookie = (name, value, domain, expires = -1) => ({ name, value, domain, expires, path: '/' });

test('只导出当前平台的有效 Cookie，并包含 HttpOnly 登录 Cookie', () => {
  const result = platformCookies([
    { ...cookie('SESSDATA', 'login-token', '.bilibili.com'), httpOnly: true },
    cookie('SESSDATA', 'nested-token', 'www.bilibili.com'),
    cookie('bili_jct', 'csrf', '.bilibili.com'),
    cookie('expired', 'old', '.bilibili.com', 1),
    cookie('SID', 'google-secret', '.youtube.com'),
    cookie('SESSDATA', 'unrelated', '.fakebilibili.com'),
    cookie('SESSDATA', 'unrelated', '.bilibili.com.example.org'),
  ], 'bilibili');
  assert.equal(result, 'SESSDATA=login-token; bili_jct=csrf');
});

test('没有有效登录标记时继续等待，不把访客 Cookie 保存成登录 Cookie', () => {
  assert.equal(platformCookies([cookie('buvid3', 'guest', '.bilibili.com')], 'bilibili'), null);
  assert.equal(platformCookies([cookie('SESSDATA', 'expired', '.bilibili.com', 1)], 'bilibili'), null);
  assert.equal(platformCookies([cookie('SAPISID', 'yt', '.youtube.com')], 'youtube'), 'SAPISID=yt');
  assert.equal(platformCookies([cookie('sessionid', 'dy', '.douyin.com')], 'douyin'), 'sessionid=dy');
  assert.equal(platformCookies([cookie('userId', 'guest', '.kuaishou.com')], 'kuaishou'), null);
  assert.equal(platformCookies([cookie('kuaishou.server.web_st', 'ks', '.kuaishou.com')], 'kuaishou'), 'kuaishou.server.web_st=ks');
});

test('登录、轮询、取消和域名隔离的 HTTP 流程', async t => {
  let captured = null;
  const opened = [];
  const server = createHelper({
    open: async platform => { opened.push(platform); },
    capture: async () => captured,
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const root = `http://127.0.0.1:${server.address().port}`;
  const post = (path, origin = 'http://localhost:18080') => fetch(root + path, {
    method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: '{}',
  });

  assert.equal((await post('/capture/bilibili', 'https://untrusted.example')).status, 403);
  assert.equal((await fetch(root + '/capture/bilibili', { method: 'POST' })).status, 403);
  assert.equal((await fetch(root + '/capture/bilibili', { headers: { Origin: 'http://localhost:18080' } })).status, 405);
  assert.equal((await post('/capture/unsupported')).status, 404);
  assert.equal(opened.length, 0);
  const defaultPortSession = await post('/capture/youtube', 'http://localhost:3015');
  assert.equal(defaultPortSession.status, 200);
  const defaultPortData = await defaultPortSession.json();
  await post(`/session/${defaultPortData.id}/cancel`, 'http://localhost:3015');
  opened.length = 0;
  const preflight = await fetch(root + '/capture/bilibili', { method: 'OPTIONS', headers: { Origin: 'http://localhost:18080' } });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('access-control-allow-origin'), 'http://localhost:18080');

  const session = await (await post('/capture/bilibili')).json();
  assert.equal(session.platform, 'bilibili');
  assert.deepEqual(opened, ['bilibili']);
  const waiting = await (await post(`/session/${session.id}`)).json();
  assert.equal(waiting.state, 'waiting');
  assert.equal(waiting.cookie, undefined);
  captured = 'SESSDATA=example-test-value';
  const readyResponse = await post(`/session/${session.id}`);
  assert.equal(readyResponse.headers.get('cache-control'), 'no-store');
  const ready = await readyResponse.json();
  assert.deepEqual(ready, { platform: 'bilibili', state: 'ready', cookie: captured });
  assert.equal((await post(`/session/${session.id}`)).status, 410);
  const cancelled = await (await post('/capture/youtube')).json();
  assert.equal((await (await post(`/session/${cancelled.id}/cancel`)).json()).state, 'cancelled');
  assert.equal((await post(`/session/${cancelled.id}`)).status, 410);
});

test('用户关闭登录窗口时返回可操作的错误', async t => {
  const server = createHelper({ open: async () => {}, capture: async () => { throw new Error('登录窗口已关闭'); } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const post = path => fetch(`http://127.0.0.1:${server.address().port}` + path, {
    method: 'POST', headers: { Origin: 'http://127.0.0.1:18080', 'Content-Type': 'application/json' }, body: '{}',
  });
  const session = await (await post('/capture/douyin')).json();
  const response = await post(`/session/${session.id}`);
  assert.equal(response.status, 500);
  assert.equal((await response.json()).error, '登录窗口已关闭');
});
