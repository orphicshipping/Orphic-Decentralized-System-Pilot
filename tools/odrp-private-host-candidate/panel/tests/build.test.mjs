import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, rm, symlink} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {build, PANEL} from '../scripts/build.mjs';

const git = (root, ...args) => execFileSync('git', ['-C', root, ...args], {stdio: 'pipe'}).toString().trim();
async function fixture(t) {
  const base = await mkdtemp(join(tmpdir(), 'odsp-panel-build-'));
  t.after(() => rm(base, {recursive: true, force: true}));
  const root = join(base, 'source'); await mkdir(root);
  git(root, 'init', '-q'); git(root, 'config', 'user.name', 'Synthetic fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid');
  for (const [name, text] of Object.entries({'web/index.html':'<h1>Operator console</h1>', 'web/app.js':'// source', 'web/styles.css':':root{}',
    'worker/index.mjs':'export function createPanel(){return {fetch(){return new Response("fixture")}}}'})) {
    const path = join(root, PANEL, name); await mkdir(join(path, '..'), {recursive: true}); await writeFile(path, text);
  }
  git(root, 'add', '.'); git(root, 'commit', '-qm', 'Synthetic fixture');
  return {base, root};
}

test('clean committed build is deterministic and all served bytes bind to source', async t => {
  const {base, root} = await fixture(t);
  const first = await build(root, join(base, 'one')), second = await build(root, join(base, 'two'));
  assert.deepEqual(first, second);
  assert.equal(first.source.source_commit, git(root, 'rev-parse', 'HEAD'));
  assert.equal(first.source.source_tree, git(root, 'rev-parse', 'HEAD^{tree}'));
  for (const [name, digest] of Object.entries(first.files)) {
    const raw = await readFile(join(base, 'one', name));
    assert.equal(createHash('sha256').update(raw).digest('hex'), digest);
    assert.deepEqual(raw, await readFile(join(base, 'two', name)));
  }
  assert.equal(first.deployment, 'not_performed');
});
test('dirty, existing and in-checkout destinations refuse before output', async t => {
  const {base, root} = await fixture(t);
  await assert.rejects(build(root, root), /outside/);
  await assert.rejects(build(root, base), /new directory/);
  await writeFile(join(root, PANEL, 'web/app.js'), 'changed');
  await assert.rejects(build(root, join(base, 'dirty')), /clean committed/);
});
test('source symlinks refuse instead of following outside bytes', async t => {
  const {base, root} = await fixture(t);
  const path = join(root, PANEL, 'web/app.js'); await rm(path); await symlink('/outside/not-read', path);
  git(root, 'add', '.'); git(root, 'commit', '-qm', 'Synthetic symlink');
  await assert.rejects(build(root, join(base, 'output')), /regular committed/);
});
test('symlink output parents and supplied source identity refuse', async t => {
  const {base, root} = await fixture(t);
  await symlink(root, join(base, 'linked'));
  await assert.rejects(build(root, join(base, 'linked', 'output')), /without symlinks/);
  await writeFile(join(root, PANEL, 'web/source.json'), '{}');
  git(root, 'add', '.'); git(root, 'commit', '-qm', 'Synthetic identity collision');
  await assert.rejects(build(root, join(base, 'output')), /reserved/);
});
