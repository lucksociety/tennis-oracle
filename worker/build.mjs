// Bundles docs/engine.js + docs/markets.js (classic UMD scripts) ahead of the Worker module -> worker/dist/index.js
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
const root = new URL('..', import.meta.url).pathname;
const src = ['docs/engine.js', 'docs/markets.js'].map(f => `// ---- ${f} ----\n` + readFileSync(root + f, 'utf8')).join('\n');
mkdirSync(root + 'worker/dist', { recursive: true });
writeFileSync(root + 'worker/dist/index.js', src + '\n// ---- worker/src/index.js ----\n' + readFileSync(root + 'worker/src/index.js', 'utf8'));
console.log('built worker/dist/index.js');
