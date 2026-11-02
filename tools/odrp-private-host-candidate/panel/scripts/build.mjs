// Build only committed panel bytes. No network, credentials or deployment.
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {mkdir, writeFile, lstat, realpath} from 'node:fs/promises';
import {dirname, resolve, relative, isAbsolute} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

export const PANEL = 'tools/odrp-private-host-candidate/panel';
export const REPOSITORY = 'orphicsystems/Orphic-Decentralized-System-Protocol';
const hash = value => createHash('sha256').update(value).digest('hex');
const contentType = name => ({html: 'text/html; charset=utf-8', js: 'text/javascript; charset=utf-8',
  css: 'text/css; charset=utf-8', json: 'application/json; charset=utf-8'}[name.split('.').at(-1)]);
const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], {maxBuffer: 8 * 1024 * 1024});
const inside = (root, target) => { const p = relative(root, target); return p === '' || (!p.startsWith('..') && !isAbsolute(p)); };

export async function build(root, destination) {
  root = await realpath(resolve(root)); destination = resolve(destination);
  if (await realpath(dirname(destination)) !== dirname(destination))
    throw Error('Panel output parent must be an existing real directory without symlinks');
  if (inside(root, destination)) throw Error('Panel output must be outside the source checkout');
  try { await lstat(destination); throw Error('Panel output must be a new directory'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const commit = git(root, 'rev-parse', 'HEAD').toString().trim();
  if (git(root, 'status', '--porcelain', '--untracked-files=all').length)
    throw Error('Panel build requires a clean committed source checkout');
  const tree = git(root, 'rev-parse', commit + '^{tree}').toString().trim();
  const entries = git(root, 'ls-tree', '-rz', commit, '--', PANEL + '/web', PANEL + '/worker').toString().split('\0').filter(Boolean);
  const inputs = {}, assets = {}, modules = {};
  for (const entry of entries) {
    const [meta, name] = entry.split('\t');
    const [mode, kind, object] = meta.split(' ');
    const local = name.slice(PANEL.length + 1);
    // Navigation metadata is not an application asset or executable module.
    if (['README.md', 'MCP.md'].includes(local.split('/').at(-1))) continue;
    if (mode !== '100644' || kind !== 'blob') throw Error('Only regular committed panel files are supported');
    if (!/^(web|worker)\/[A-Za-z0-9_./-]+$/.test(local) || local.includes('..')) throw Error('Unsafe panel path');
    const raw = git(root, 'cat-file', 'blob', object);
    if (raw.length > 1024 * 1024) throw Error('Panel source file exceeds budget');
    inputs[name] = hash(raw);
    if (local.startsWith('web/')) {
      const route = local === 'web/index.html' ? '/' : '/' + local.slice(4);
      if (!/\.(html|css|js|json)$/.test(local)) throw Error('Unsupported panel asset');
      assets[route] = {body: new TextDecoder('utf-8', {fatal: true}).decode(raw), contentType: contentType(local)};
    } else {
      if (!local.endsWith('.mjs')) throw Error('Unsupported panel executable');
      modules[local] = raw;
    }
  }
  for (const route of ['/', '/app.js', '/styles.css']) if (!(route in assets)) throw Error('Missing panel asset: ' + route);
  if ('/source.json' in assets) throw Error('Panel source.json is reserved for the generated identity');
  if (!modules['worker/index.mjs']) throw Error('Missing authenticated panel entry');
  const source = {schema: 'odsp.private-panel-source.v1', repository: REPOSITORY,
    source_commit: commit, source_tree: tree,
    assets: Object.fromEntries(Object.entries(assets).map(([route, value]) => [route, hash(value.body)]))};
  assets['/source.json'] = {body: JSON.stringify(source, null, 2) + '\n', contentType: contentType('source.json')};
  const entry = "import {createPanel} from './worker/index.mjs';\nimport {assets} from './assets.mjs';\nexport default createPanel({assets});\n";
  const outputs = {'index.mjs': Buffer.from(entry), 'assets.mjs': Buffer.from('export const assets = ' + JSON.stringify(assets) + ';\n'), ...modules};
  if (git(root, 'rev-parse', 'HEAD').toString().trim() !== commit
      || git(root, 'status', '--porcelain', '--untracked-files=all').length)
    throw Error('Panel source changed during build');
  await mkdir(destination);
  for (const [name, raw] of Object.entries(outputs)) {
    const target = resolve(destination, name);
    await mkdir(dirname(target), {recursive: true});
    await writeFile(target, raw, {flag: 'wx'});
  }
  const receipt = {schema: 'odsp.private-panel-build.v1', source,
    inputs, files: Object.fromEntries(Object.entries(outputs).map(([name, value]) => [name, hash(value)])),
    deployment: 'not_performed', authentication: 'host_configuration_required',
    runtime: {node: process.version}, dependency_installation: 'none'};
  await writeFile(resolve(destination, 'build-receipt.json'), JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx'});
  return receipt;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (process.argv.length !== 3) throw Error('Usage: node panel/scripts/build.mjs /absolute/new/output');
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../../');
  const receipt = await build(root, process.argv[2]);
  console.log(JSON.stringify({source: receipt.source.source_commit, tree: receipt.source.source_tree,
    output: resolve(process.argv[2]), files: Object.keys(receipt.files).length, deployment: receipt.deployment}));
}
