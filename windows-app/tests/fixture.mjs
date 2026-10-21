import http from 'node:http';
import { spawn } from 'node:child_process';

const [mode, ...args] = process.argv.slice(2);
if (mode === 'args') console.log(JSON.stringify(args));
else if (mode === 'exit') process.exit(Number(args[0]));
else if (mode === 'env') {
  console.log(JSON.stringify({ hasKey: Boolean(process.env.MIMO_API_KEY), hasOther: Boolean(process.env.MIMO_TEST_TOKEN), hasViteSecret: Boolean(process.env.VITE_TEST_API_KEY), backend: process.env.VITE_BACKEND_URL }));
  console.log(process.env.MIMO_API_KEY || 'no-key');
  console.error(process.env.MIMO_TEST_TOKEN || 'no-token');
} else if (mode === 'tree') {
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
  console.log(JSON.stringify({ pid: process.pid, child: child.pid }));
  setInterval(() => {}, 1000);
} else if (mode === 'server') {
  const [port, identity = 'wrong', origin = 'http://127.0.0.1:5173'] = args;
  http.createServer((req, res) => {
    if (identity === 'frontend') {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(req.url === '/@vite/client' ? 'vite client fixture' : '<title>MiMo Workflow New Title</title><script src="/@vite/client"></script>');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': origin });
    res.end(JSON.stringify({ ok: true, service: identity === 'valid' ? 'mimo-workflow-backend' : 'not-mimo' }));
  }).listen(Number(port), '127.0.0.1', () => console.log('READY'));
}
