import { readFile } from 'node:fs/promises';
import { createHelper } from './server.mjs';
import { LoginBrowser } from './browser.mjs';

const configuration = JSON.parse(await readFile(new URL('./runtime.json', import.meta.url), 'utf8'));
const server = createHelper(new LoginBrowser({ profile: configuration.profile }), { idleTimeout: 30000 });
server.on('error', error => {
  // Repeated button presses reuse the existing loopback service.
  if (error.code === 'EADDRINUSE') process.exit(0);
  throw error;
});
server.listen(18765, '127.0.0.1');
