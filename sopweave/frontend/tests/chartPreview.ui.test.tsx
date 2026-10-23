import React from 'react';
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { render, screen, cleanup } from '@testing-library/react';
import ChartPreview from '../src/components/ChartPreview';

afterEach(cleanup);
test('switching a preview between 2D and Python 3D keeps React hooks consistent', async () => {
  (globalThis as any).ResizeObserver = class { observe(){} disconnect(){} };
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:{success:true,b64:'cG5n'}}));
  const view = render(<ChartPreview template="bar" title="预览"/>);
  view.rerender(<ChartPreview template="bar3d" title="三维"/>);
  assert.ok(await screen.findByRole('img'));
  view.rerender(<ChartPreview template="bar" title="二维"/>);
  assert.equal(screen.queryByRole('img'), null);
});
