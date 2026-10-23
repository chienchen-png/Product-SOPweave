import test from 'node:test';
import assert from 'node:assert/strict';
import { readMigratedStorage } from '../.test-build/brand.js';

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: key => values.has(key) ? values.get(key) : null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    values,
  };
}

test('legacy finance browser keys migrate once to SOPweave keys', () => {
  const localStorage = storage({ 'finance.workspace.v1:demo': '{"value":1}' });
  globalThis.window = { localStorage };
  assert.equal(readMigratedStorage('sopweave.workspace.v1:demo'), '{"value":1}');
  assert.equal(localStorage.getItem('sopweave.workspace.v1:demo'), '{"value":1}');
  assert.equal(localStorage.getItem('finance.workspace.v1:demo'), null);
});

test('existing SOPweave keys take precedence over legacy values', () => {
  const localStorage = storage({
    'sopweave.draft.v1:demo': 'new',
    'finance.draft.v1:demo': 'old',
  });
  globalThis.window = { localStorage };
  assert.equal(readMigratedStorage('sopweave.draft.v1:demo'), 'new');
  assert.equal(localStorage.getItem('finance.draft.v1:demo'), 'old');
});
