import { createHelper } from './server.mjs';

export class CookieLifecycle {
  constructor({ browser, port = 18765 }) {
    this.browser = browser;
    this.port = port;
    this.server = null;
  }

  async update(running) {
    if (running && !this.server) {
      const server = createHelper(this.browser);
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(this.port, '127.0.0.1', resolve);
      });
      this.server = server;
    } else if (!running && this.server) {
      const server = this.server;
      this.server = null;
      const closed = new Promise(resolve => server.close(resolve));
      server.closeAllConnections();
      await closed;
    }
  }
}
