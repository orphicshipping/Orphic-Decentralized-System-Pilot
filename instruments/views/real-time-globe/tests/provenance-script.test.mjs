import assert from 'node:assert/strict';
import test from 'node:test';
import { copyFile, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

for (const present of [true, false]) {
  test(`provenance validator resolves encoded paths and ${present ? 'accepts' : 'rejects'} source`, async () => {
    const root = await mkdtemp(join(tmpdir(), 'gsv provenance #% -'));
    try {
      await mkdir(join(root, 'scripts'));
      await mkdir(join(root, 'src/data/synthetic'), { recursive: true });
      await mkdir(join(root, 'unrelated-cwd'));
      await copyFile(new URL('../scripts/validate-provenance.mjs', import.meta.url),
        join(root, 'scripts/validate-provenance.mjs'));
      const snapshot = { nodes: [{ id: 'node:test', provenance: present ? { source: 'synthetic:test' } : {} }],
        routes: [], flows: [], commodities: [], events: [], constraints: [] };
      await writeFile(join(root, 'src/data/synthetic/world.ts'),
        `export function buildWorldSnapshot() { return ${JSON.stringify(snapshot)}; }\n`);
      const result = spawnSync(process.execPath, [join(root, 'scripts/validate-provenance.mjs')],
        { cwd: join(root, 'unrelated-cwd'), encoding: 'utf8' });
      assert.equal(result.error, undefined);
      assert.equal(result.status, present ? 0 : 1, result.stderr);
      if (present) assert.match(result.stdout, /provenance check ok.*1 records/);
      else assert.match(result.stderr, /PROVENANCE CHECK FAILED.*records without provenance.source:[\s\S]*node:node:test/);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}
