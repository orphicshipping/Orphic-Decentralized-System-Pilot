import assert from 'node:assert/strict';
import test from 'node:test';
import {expectedRelease, releaseMatches, validateRelease} from './release.mjs';
const release = {schema:'odsp.private-release-identity.v1',repository:'orphicsystems/Orphic-Decentralized-Systems-Protocol',source_commit:'a'.repeat(40),source_tree:'b'.repeat(40),wheel_sha256:'c'.repeat(64),package_digest:'d'.repeat(64)};

test('launcher identity is mandatory and exact', () => {
  assert.deepEqual(expectedRelease({ODSP_RELEASE_IDENTITY:JSON.stringify(release, null, 2)}), release);
  assert.ok(Object.isFrozen(validateRelease(release)));
  for (const raw of [undefined, '', '{}', '[]', JSON.stringify({...release, legacy:true}), JSON.stringify({...release, source_commit:'old'})]) {
    assert.throws(() => expectedRelease({ODSP_RELEASE_IDENTITY:raw}));
  }
});
test('duplicate and escaped duplicate JSON fields are refused', () => {
  const rest = JSON.stringify(release).slice(1);
  for (const key of ['schema', '\\u0073chema']) {
    assert.throws(() => expectedRelease({ODSP_RELEASE_IDENTITY:`{"${key}":"wrong",${rest}`}));
  }
});
test('release match refuses missing, extra, malformed and different fields', () => {
  assert.equal(releaseMatches({...release}, release), true);
  assert.equal(releaseMatches(null, release), false);
  assert.equal(releaseMatches({...release, extra:true}, release), false);
  for (const key of Object.keys(release)) {
    const changed = {...release, [key]:key === 'repository' ? 'other/repository' : 'e'.repeat(release[key].length)};
    assert.equal(releaseMatches(changed, release), false);
    const absent = {...release}; delete absent[key];
    assert.equal(releaseMatches(absent, release), false);
  }
});
