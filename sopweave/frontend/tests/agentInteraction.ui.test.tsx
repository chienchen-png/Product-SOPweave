import React from 'react';
import test from 'node:test';
import assert from 'node:assert/strict';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import AgentInteractionPanel from '../src/components/AgentInteraction';

test('assistant-ui renders multiple approvals and forwards a decision with its note', async () => {
  const submitted: unknown[] = [];
  const items = ['first','second'].map((id, index) => ({ interaction_id: id, run_id: 'run', tool_call_id: id,
    version: 1, kind: 'approval' as const, status: 'pending', name: `write_${index}`, arguments: { path: `${id}.txt` } }));
  render(<AgentInteractionPanel items={items} onSubmit={async (item, command) => { submitted.push({item, command}); }}/>);
  await screen.findByText('是否执行 write_0？');
  assert.ok(screen.getByText(/first.txt/));
  assert.equal(screen.getAllByRole('form').length, 2);
  fireEvent.change(screen.getAllByLabelText('备注')[0], { target: { value: 'reviewed' } });
  fireEvent.click(screen.getAllByRole('button', { name: '批准执行' })[0]);
  await waitFor(() => assert.equal(submitted.length, 1));
  assert.equal((submitted[0] as any).command.interaction_id, 'first');
  assert.equal((submitted[0] as any).command.note, 'reviewed');
  cleanup();
});

test('multiselect stays open until submit and preserves structured selected ids', async () => {
  let result: any;
  const item = { interaction_id: 'q', run_id: 'run', tool_call_id: 'ask', version: 1,
    kind: 'input' as const, status: 'pending', questions: [{ id: 'models', title: '选择模型', kind: 'multiple' as const,
      required: true, allow_custom: true, options: [{id:'a',label:'模型 A',description:''},{id:'b',label:'模型 B',description:''}] }] };
  render(<AgentInteractionPanel items={[item]} onSubmit={async (_, command) => { result = command; }}/>);
  fireEvent.click(await screen.findByLabelText('模型 A'));
  fireEvent.click(screen.getByLabelText('模型 B'));
  assert.equal(result, undefined);
  fireEvent.click(screen.getByRole('button', {name:'提交回答'}));
  await waitFor(() => assert.deepEqual(result.answers[0].selected, ['a','b']));
  cleanup();
});
