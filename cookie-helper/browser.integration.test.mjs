import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { LoginBrowser, PLATFORMS } from './browser.mjs';

test('真实 Chrome 能读取四个平台的 HttpOnly Cookie，且隔离登录信息', { skip: process.platform !== 'win32' }, async t => {
  const profile = await mkdtemp(join(tmpdir(), 'bilinote-cookie-test-'));
  const browser = new LoginBrowser({ profile });
  const child = spawn(browser.executable, [
    '--headless=new', `--user-data-dir=${profile}`, '--remote-debugging-port=0',
    '--no-first-run', '--no-default-browser-check', '--disable-background-networking', 'about:blank',
  ], { stdio: 'ignore', windowsHide: true });
  t.after(async () => {
    const exited = new Promise(resolve => child.once('exit', resolve));
    try { await browser.command('Browser.close'); } catch {}
    await exited;
    assert.equal(dirname(profile), tmpdir());
    assert.ok(basename(profile).startsWith('bilinote-cookie-test-'));
    await rm(profile, { recursive: true, force: true });
  });
  let endpoint;
  for (let i = 0; i < 60 && !endpoint; i++) { endpoint = await browser.endpoint(); if (!endpoint) await delay(200); }
  assert.ok(endpoint, 'Chrome debugging endpoint is ready');
  for (const [platform, config] of Object.entries(PLATFORMS)) {
    await browser.open(platform);
    assert.equal(await browser.capture(platform), null);
    await browser.command('Storage.setCookies', { cookies: [{
      name: config.auth[0], value: `test-${platform}`, domain: '.' + config.domains[0],
      path: '/', secure: true, httpOnly: true,
    }] });
    const result = await browser.capture(platform);
    assert.ok(result.includes(`${config.auth[0]}=test-${platform}`));
    for (const other of Object.keys(PLATFORMS)) {
      if (other !== platform) assert.ok(!result.includes(`test-${other}`));
    }
  }
});
