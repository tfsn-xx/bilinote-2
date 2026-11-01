const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const ts = require('typescript');

const source = ts.transpileModule(readFileSync(join(__dirname, '../src/services/cookieCapture.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;

function helper(overrides = {}) {
  const events = [];
  const exports = {};
  runInNewContext(source, {
    exports,
    window: { location: { assign: url => events.push(url) } },
    fetch: async () => { events.push('health'); return { ok: true, json: async () => ({ service: 'bilinote-cookie-helper' }) }; },
    AbortSignal,
    setTimeout,
    Date,
    ...overrides,
  });
  return { events, start: exports.startCookieHelper };
}

test('点击时立即唤起助手，不能等网络请求后才调用系统协议', async () => {
  const { events, start } = helper();
  const pending = start(() => false);
  assert.equal(events[0], 'bilinote-cookie://start');
  await pending;
});

test('已取消的操作不能重新唤起助手', async () => {
  const { events, start } = helper();
  await start(() => true);
  assert.deepEqual(events, []);
});

test('助手不可用时在 15 秒后结束等待并提示直接打开链接', async () => {
  let now = 0;
  const { start } = helper({
    Date: { now: () => now },
    fetch: async () => { throw new Error('connection refused'); },
    setTimeout: resolve => { now += 500; resolve(); },
  });
  await assert.rejects(start(() => false), /打开 Cookie 助手/);
  assert.equal(now, 15000);
});
