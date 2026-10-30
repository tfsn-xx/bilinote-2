import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

export const PLATFORMS = {
  bilibili: { url: 'https://www.bilibili.com/', domains: ['bilibili.com'], auth: ['SESSDATA'] },
  youtube: { url: 'https://www.youtube.com/', domains: ['youtube.com'], auth: ['SAPISID', '__Secure-3PAPISID', 'LOGIN_INFO'] },
  douyin: { url: 'https://www.douyin.com/', domains: ['douyin.com'], auth: ['sessionid', 'sessionid_ss'] },
  kuaishou: { url: 'https://www.kuaishou.com/', domains: ['kuaishou.com'], auth: ['kuaishou.server.web_st'] },
};

export function platformCookies(cookies, platform, now = Date.now() / 1000) {
  const config = PLATFORMS[platform];
  const selected = cookies.filter(cookie => {
    const domain = cookie.domain.replace(/^\./, '').toLowerCase();
    return config.domains.some(allowed => domain === allowed || domain.endsWith('.' + allowed))
      && (!cookie.expires || cookie.expires < 0 || cookie.expires > now)
      && cookie.value;
  });
  // Prefer cookies on the parent domain when the same name exists on several paths.
  selected.sort((a, b) => a.domain.length - b.domain.length || a.path.length - b.path.length);
  const values = new Map();
  for (const cookie of selected) if (!values.has(cookie.name)) values.set(cookie.name, cookie.value);
  if (!config.auth.some(name => values.has(name))) return null;
  return [...values].map(([name, value]) => `${name}=${value}`).join('; ');
}

export class LoginBrowser {
  constructor({ profile, executable } = {}) {
    this.profile = profile || join(process.env.LOCALAPPDATA, 'BiliNote', 'cookie-browser');
    this.executable = executable || [
      join(process.env.PROGRAMFILES || 'C:\\Program Files', 'Google', 'Chrome', 'Application', 'chrome.exe'),
      join(process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ].find(existsSync);
    this.starting = null;
    this.targets = new Map();
  }

  async endpoint() {
    try {
      const [port, path] = (await readFile(join(this.profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);
      const response = await fetch(`http://127.0.0.1:${Number(port)}/json/version`, { signal: AbortSignal.timeout(1000) });
      const info = await response.json();
      return info.webSocketDebuggerUrl.endsWith(path) ? info.webSocketDebuggerUrl : null;
    } catch {
      return null;
    }
  }

  async ensureRunning() {
    const current = await this.endpoint();
    if (current) return current;
    if (!this.starting) {
      this.starting = this.launch().finally(() => { this.starting = null; });
    }
    return this.starting;
  }

  async launch() {
    if (!this.executable) throw new Error('未找到 Chrome 或 Edge，请先安装其中一个浏览器。');
    await mkdir(this.profile, { recursive: true });
    await rm(join(this.profile, 'DevToolsActivePort'), { force: true });
    const child = spawn(this.executable, [
      `--user-data-dir=${this.profile}`, '--remote-debugging-port=0',
      '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check',
      '--disable-background-mode', 'about:blank',
    ], { detached: true, stdio: 'ignore', windowsHide: false });
    let launchError;
    child.on('error', error => { launchError = error; });
    child.unref();
    for (let attempt = 0; attempt < 60; attempt++) {
      if (launchError) throw new Error('无法打开登录浏览器：' + launchError.message);
      const endpoint = await this.endpoint();
      if (endpoint) return endpoint;
      await delay(200);
    }
    throw new Error('登录窗口启动超时，请关闭 BiliNote 登录浏览器后重新获取。');
  }

  async command(method, params = {}, endpoint) {
    endpoint ||= await this.ensureRunning();
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(endpoint);
      const timer = setTimeout(() => finish(new Error('登录浏览器响应超时，请重新获取。')), 5000);
      const finish = (error, result) => {
        clearTimeout(timer);
        socket.close();
        error ? reject(error) : resolve(result);
      };
      socket.addEventListener('open', () => socket.send(JSON.stringify({ id: 1, method, params })));
      socket.addEventListener('error', () => finish(new Error('无法连接登录窗口，请重新获取。')));
      socket.addEventListener('message', event => {
        const message = JSON.parse(event.data);
        if (message.id === 1) finish(message.error ? new Error(message.error.message) : null, message.result);
      });
    });
  }

  async open(platform) {
    const { targetInfos } = await this.command('Target.getTargets');
    let target = targetInfos.find(item => item.targetId === this.targets.get(platform));
    if (!target) {
      const result = await this.command('Target.createTarget', { url: PLATFORMS[platform].url });
      this.targets.set(platform, result.targetId);
      target = { targetId: result.targetId };
    }
    await this.command('Target.activateTarget', { targetId: target.targetId });
  }

  async capture(platform) {
    const endpoint = await this.endpoint();
    if (!endpoint) throw new Error('登录窗口已关闭，请重新点击“获取 Cookie”。');
    const { targetInfos } = await this.command('Target.getTargets', {}, endpoint);
    if (!targetInfos.some(target => target.targetId === this.targets.get(platform))) {
      throw new Error('平台登录标签页已关闭，请重新获取。');
    }
    const { cookies } = await this.command('Storage.getCookies', {}, endpoint);
    return platformCookies(cookies, platform);
  }
}
