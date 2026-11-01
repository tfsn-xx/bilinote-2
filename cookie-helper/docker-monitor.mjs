import { createServer } from 'node:net';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, appendFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { LoginBrowser } from './browser.mjs';
import { CookieLifecycle } from './lifecycle.mjs';

// Only the lightweight Docker monitor remains idle when BiliNote is stopped.
// The Cookie HTTP service is created and closed with the container's state.
const folder = dirname(fileURLToPath(import.meta.url));
process.on('uncaughtException', async error => {
  await appendFile(join(folder, 'monitor.log'), `${new Date().toISOString()} ${error.stack}\n`);
  process.exit(1);
});
process.on('unhandledRejection', error => { throw error; });
const configuration = JSON.parse(await readFile(join(folder, 'runtime.json'), 'utf8'));
const execute = promisify(execFile);
const singleton = createServer();
singleton.on('error', error => {
  if (error.code === 'EADDRINUSE') process.exit(0);
  throw error;
});
singleton.listen('\\\\.\\pipe\\bilinote-cookie-monitor', async () => {
  const lifecycle = new CookieLifecycle({ browser: new LoginBrowser({ profile: configuration.profile }) });
  let previous;
  for (;;) {
    let running = false;
    try {
      const { stdout } = await execute(configuration.dockerPath, [
        'inspect', '--format', '{{.State.Running}}', configuration.container,
      ], { windowsHide: true, timeout: 5000 });
      running = stdout.trim() === 'true';
    } catch { /* Docker Desktop or the selected container is stopped. */ }
    try {
      await lifecycle.update(running);
      if (previous !== running) {
        await appendFile(join(folder, 'monitor.log'), `${new Date().toISOString()} Cookie service ${running ? 'started' : 'stopped'}\n`);
        previous = running;
      }
    } catch (error) {
      await appendFile(join(folder, 'monitor.log'), `${new Date().toISOString()} ${error.message}\n`);
    }
    await delay(1000);
  }
});
