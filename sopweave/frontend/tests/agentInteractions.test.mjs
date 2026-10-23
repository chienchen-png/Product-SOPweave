import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeInteractions, validateAnswers, interactionCommand } from '../.test-build/agentInteractions.js';

test('queues distinct approvals and deduplicates replayed interactions', () => {
  const a = { interaction_id: 'a', version: 1 };
  const b = { interaction_id: 'b', version: 1 };
  assert.deepEqual(mergeInteractions([a], [b, a]), [a, b]);
});

test('multiselect preserves option ids containing commas', () => {
  const question = { id: 'q', title: '选择', kind: 'multiple', required: true, options: [{ id: 'a,b', label: 'a,b' }] };
  assert.equal(validateAnswers([question], [{ question_id: 'q', selected: ['a,b'], text: '', confirmed: null }]), null);
  assert.notEqual(validateAnswers([question], [{ question_id: 'q', selected: ['unknown'], text: '', confirmed: null }]), null);
});

test('confirmation needs an explicit choice and accepts false', () => {
  const q = { id: 'q', title: '确认', kind: 'confirm', required: true, options: [] };
  assert.notEqual(validateAnswers([q], [{ question_id: 'q', selected: [], text: '', confirmed: null }]), null);
  assert.equal(validateAnswers([q], [{ question_id: 'q', selected: [], text: '', confirmed: false }]), null);
});

test('submissions bind server ids and keep rejection notes', () => {
  const item = { kind: 'approval', interaction_id: 'real', version: 2, tool_call_id: 'call' };
  assert.deepEqual(interactionCommand(item, { action: 'reject', note: '原因', interaction_id: 'forged' }), {
    action: 'reject', note: '原因', interaction_id: 'real', version: 2, tool_call_id: 'call',
  });
});
