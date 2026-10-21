import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const require = createRequire(path.join(process.argv[2], 'package.json'));
const manifest = require('./package.json');
for (const name of Object.keys({ ...manifest.dependencies, ...manifest.devDependencies })) {
  await import(pathToFileURL(require.resolve(name)).href);
}
// Exercise native packages to detect copied macOS node_modules.
const esbuild = require('esbuild');
await esbuild.transform('const value = 1', { loader: 'js' });
const { rollup } = await import(pathToFileURL(require.resolve('rollup')).href);
const bundle = await rollup({ input: 'probe', plugins: [{ name: 'probe', resolveId: () => 'probe', load: () => 'export default 1' }] });
await bundle.generate({ format: 'es' });
await bundle.close();
