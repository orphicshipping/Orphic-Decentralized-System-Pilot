// Worker ESM starter layout; retain source assets, emit one dependency-free module.
import {readFile, mkdir, writeFile, copyFile} from 'node:fs/promises';
const files = {'/':'index.html','/app.js':'app.js','/styles.css':'styles.css',
  '/explorer.html':'explorer.html','/catalog.json':'catalog.json','/source.json':'source.json',
  '/board.html':'board.html','/board.json':'board.json','/LICENSE':'LICENSE'};
const assets = {};
for (const [route, file] of Object.entries(files)) assets[route] = await readFile(new URL('../web/'+file, import.meta.url),'utf8');
const source = await readFile(new URL('../worker/index.js', import.meta.url), 'utf8');
await mkdir(new URL('../dist/server/', import.meta.url), {recursive:true});
await mkdir(new URL('../dist/.openai/', import.meta.url), {recursive:true});
await writeFile(new URL('../dist/server/index.js', import.meta.url), 'const ASSETS = '+JSON.stringify(assets)+';\n'+source);
await copyFile(new URL('../.openai/hosting.json', import.meta.url), new URL('../dist/.openai/hosting.json', import.meta.url));
console.log('Built Sites Worker ESM with retained repository explorer and private MCP proxy.');
