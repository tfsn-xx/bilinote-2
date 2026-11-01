import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { createHelper } from './server.mjs';

test('未开始获取时自动关闭按需服务', { timeout: 2000 }, async () => {
  const server = createHelper({}, { idleTimeout: 50 });
  const closed = once(server, 'close');
  server.listen(0, '127.0.0.1');
  await closed;
  assert.equal(server.listening, false);
});

for (const result of ['cancel', 'ready']) {
  test(`等待登录时保持服务，${result === 'cancel' ? '取消' : '获取成功'}后自动退出`, { timeout: 3000 }, async t => {
    const server = createHelper({ open: async () => {}, capture: async () => 'SESSDATA=test-token' }, { idleTimeout: 100 });
    t.after(() => server.close());
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    const root = `http://127.0.0.1:${server.address().port}`;
    const post = path => fetch(root + path, {
      method: 'POST', headers: { Origin: 'http://localhost:18080', 'Content-Type': 'application/json' }, body: '{}',
    });
    const session = await (await post('/capture/bilibili')).json();
    await delay(160);
    assert.equal(server.listening, true, '登录会话不能被空闲计时中断');
    const closed = once(server, 'close');
    const response = await post(`/session/${session.id}${result === 'cancel' ? '/cancel' : ''}`);
    assert.equal((await response.json()).state, result === 'cancel' ? 'cancelled' : 'ready');
    await closed;
    assert.equal(server.listening, false);
  });
}
