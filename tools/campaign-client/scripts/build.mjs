// Local compiler/build only. No install, network, credential, endpoint or deployment.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, readdir, realpath, stat, writeFile } from 'node:fs/promises';
import { resolve, dirname, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const compiler = resolve(process.argv[2] ?? 'node_modules/typescript/lib/tsc.js');
const destination = resolve(process.argv[3] ?? '');
if (process.argv.length !== 4) throw Error('Usage: node scripts/build.mjs /absolute/tsc.js /absolute/new/output');
if (!isAbsolute(process.argv[2]) || !isAbsolute(process.argv[3])) throw Error('Absolute trusted host paths required');
const rel = relative(root, destination);
if (rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))) throw Error('Build output must be outside the source directory');
if (await realpath(dirname(destination)) !== dirname(destination)) throw Error('Output parent must be a real directory');
try { await stat(destination); throw Error('Output must not exist'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const packageRoot = resolve(dirname(compiler), '..');
const packageData = JSON.parse(await readFile(resolve(packageRoot, 'package.json'), 'utf8'));
const lock = JSON.parse(await readFile(resolve(root, 'compiler-lock.json'), 'utf8'));
if (packageData.name !== 'typescript' || packageData.version !== lock.version) throw Error('Pinned TypeScript compiler required');
const digest = raw => createHash('sha256').update(raw).digest('hex');
const inputs = {};
for (const directory of ['src', 'web']) for (const name of (await readdir(resolve(root,directory))).sort()) {
  const local=directory+'/'+name;
  if (!(await stat(resolve(root,local))).isFile()) throw Error('Only regular client source files are supported');
  inputs[local]=digest(await readFile(resolve(root,local)));
}
for (const name of ['tsconfig.json','compiler-lock.json','package.json','package-lock.json'])
  inputs[name]=digest(await readFile(resolve(root,name)));
execFileSync(process.execPath, [compiler, '--project', resolve(root, 'tsconfig.json'), '--outDir', destination],
  { cwd: root, stdio: 'inherit', timeout: 60000 });
for (const name of ['index.html', 'theme.css', 'viewer.css'])
  await writeFile(resolve(destination, name), await readFile(resolve(root, 'web', name)), { flag: 'wx' });
for (const [name,expected] of Object.entries(inputs))
  if (digest(await readFile(resolve(root,name)))!==expected) throw Error('Client source changed during build');
const files = {};
for (const name of await readdir(destination)) {
  const raw = await readFile(resolve(destination, name));
  files[name] = { sha256: createHash('sha256').update(raw).digest('hex'), bytes: raw.length };
}
const sourceCommit=execFileSync('git',['-C',root,'rev-parse','HEAD'],{encoding:'utf8'}).trim();
const receipt = { schema: 'odsp.read-client-build.v1', node: process.version, compiler: lock,
  compiler_entry_sha256:digest(await readFile(compiler)),
  files, source_inputs:inputs, base_commit:sourceCommit,
  source_checkout:'source_inputs_are_exact; base_commit_may_precede_local_changes', deployment: 'not_performed',
  authentication: 'host_supplied_transport_required' };
await writeFile(resolve(destination, 'build-receipt.json'), JSON.stringify(receipt, null, 2)+'\n', { flag: 'wx' });
console.log(JSON.stringify({ output: destination, files: Object.keys(files).length, compiler: lock.version }));
