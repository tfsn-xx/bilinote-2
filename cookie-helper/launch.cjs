const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const log = message => fs.appendFileSync(path.join(__dirname, 'monitor.log'), `${new Date().toISOString()} ${message}\n`);
log('Docker monitor launched');
import(pathToFileURL(path.join(__dirname, 'docker-monitor.mjs')).href).catch(error => { log(error.stack); process.exit(1); });
