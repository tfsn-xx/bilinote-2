const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const log = error => fs.appendFileSync(path.join(__dirname, 'helper.log'), `${new Date().toISOString()} ${error.stack || error}\n`);
process.on('uncaughtException', error => { log(error); process.exit(1); });
import(pathToFileURL(path.join(__dirname, 'on-demand.mjs')).href).catch(error => { log(error); process.exit(1); });
