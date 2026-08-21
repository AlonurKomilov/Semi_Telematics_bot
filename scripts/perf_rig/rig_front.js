/**
 * Perf-rig frontend: serves the PRODUCTION build (dist/) read-only on
 * 127.0.0.1:8020, proxying /api → the rig backend on :8010.  No
 * dependencies, no writes — measurements run against the same bytes
 * production serves, but with rig data.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const DIST = '/home/abcdev/projects/Semi_Telematics_bot/interfaces/dashboard/dist';
const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2',
  '.json': 'application/json', '.ico': 'image/x-icon', '.webp': 'image/webp',
};

http.createServer((req, res) => {
  if (req.url.startsWith('/api')) {
    const proxy = http.request(
      { host: '127.0.0.1', port: 8010, path: req.url, method: req.method,
        headers: { ...req.headers, host: '127.0.0.1:8010' } },
      (up) => { res.writeHead(up.statusCode, up.headers); up.pipe(res); });
    proxy.on('error', () => { res.writeHead(502); res.end('rig api down'); });
    req.pipe(proxy);
    return;
  }
  let p = path.normalize(path.join(DIST, req.url.split('?')[0]));
  if (!p.startsWith(DIST)) { res.writeHead(403); res.end(); return; }
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) {
    p = path.join(DIST, 'index.html');   // SPA fallback
  }
  res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
}).listen(8020, '127.0.0.1', () => console.log('RIG FRONT READY on 127.0.0.1:8020'));
