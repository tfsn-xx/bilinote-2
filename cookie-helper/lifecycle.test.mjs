import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CookieLifecycle } from './lifecycle.mjs';

test('Cookie service follows container start stop and restart', async t => {
  const lifecycle = new CookieLifecycle({ browser: {}, port: 0 });
  t.after(() => lifecycle.update(false));
  assert.equal(lifecycle.server, null);
  await lifecycle.update(true);
  const first = lifecycle.server;
  const port = first.address().port;
  const response = await fetch(`http://127.0.0.1:${port}/health`, {
    method: 'POST', headers: { Origin: 'http://localhost:18080', 'Content-Type': 'application/json' }, body: '{}',
  });
  assert.equal((await response.json()).service, 'bilinote-cookie-helper');
  await lifecycle.update(true);
  assert.equal(lifecycle.server, first);
  await lifecycle.update(false);
  assert.equal(lifecycle.server, null);
  await assert.rejects(fetch(`http://127.0.0.1:${port}/health`));
  await lifecycle.update(true);
  assert.ok(lifecycle.server.listening);
});
