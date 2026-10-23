import React from 'react';
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import App from '../src/App';

afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); (globalThis as any).__registryLoading = false; });
test('both integrated apps open on a fresh installation with no visible project', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, data: [] }));
  render(<App />);
  const recon = await screen.findByRole('button', { name: 'app-sales-check' });
  assert.equal((globalThis as any).__loadedPages?.includes('DataCheckAppPage'), false);
  fireEvent.click(recon);
  assert.ok(await screen.findByText('DataCheckAppPage mounted'));
  fireEvent.click(screen.getByRole('button', { name: 'app-finmod' }));
  assert.ok(await screen.findByText('FinModAppPage mounted'));
});

test('a deep link opens its project and browser history restores the prior page', async () => {
  window.history.replaceState({}, '', '/projects/project-two');
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:[{id:'project-two',name:'月末核对',work_dir:'sample'}]}));
  render(<App />);
  assert.ok(await screen.findByText('ChatPage mounted project-two'));
  fireEvent.click(screen.getByRole('button',{name:'app-finmod'}));
  await screen.findByText('FinModAppPage mounted');
  assert.equal(window.location.pathname,'/apps/financial-modeling');
  window.history.back();
  await waitFor(() => assert.ok(screen.getByText('ChatPage mounted project-two')));
});

test('hidden workspaces cannot open as user projects', async () => {
  window.history.replaceState({}, '', '/projects/hidden');
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:[]}));
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:[{id:'hidden',name:'__app_recon__'}]}));
  render(<App />);
  await screen.findByText('找不到该项目');
  assert.equal(screen.queryByText('ChatPage mounted hidden'),null);
});

test('unknown settings routes fall back to model configuration', async () => {
  window.history.replaceState({}, '', '/settings/unknown?keep=1');
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:[]}));
  render(<App />);
  await screen.findByText('AiConfigPage mounted');
  await waitFor(() => assert.equal(window.location.pathname,'/settings/models'));
});

test('registry refresh does not remove the current work page', async () => {
  window.history.replaceState({}, '', '/apps/financial-modeling');
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:[]}));
  const app = render(<App />);
  await screen.findByText('FinModAppPage mounted');
  (globalThis as any).__registryLoading = true;
  app.rerender(<App />);
  assert.ok(screen.getByText('FinModAppPage mounted'));
});
