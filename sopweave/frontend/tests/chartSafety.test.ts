import test from 'node:test';
import assert from 'node:assert/strict';
import { buildChartOption, resolveTemplate } from '../src/components/chart-templates';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import MarkdownBlock from '../src/components/MarkdownBlock';

test('updated KaTeX renders report formulas through the actual Markdown component', () => {
  const html = renderToStaticMarkup(createElement(MarkdownBlock, { text: '$$\\frac{10+20-5}{365}$$' }));
  assert.ok(html.includes('katex'));
  assert.ok(html.includes('<math'));
  assert.equal(html.includes('katex-error'), false);
});

test('compound template identities stay intact', () => {
  for (const [name, base] of [['error-bar-range', 'error-bar'], ['dual-axis', 'dual-axis'], ['histogram-4grid', 'histogram-4grid']]) {
    assert.equal(resolveTemplate(name)?.tpl.name, base);
  }
});

test('spreadsheet labels cannot inject HTML into tooltip formatters', () => {
  const attack = '<img src=x onerror=alert(1)>&"';
  for (const name of ['treemap', 'treemap-drilldown', 'calendar', 'calendar-year', 'heatmap-calendar']) {
    const option: any = buildChartOption(name, { categories: ['2025-01-01'], series: [{ name: 'data', data: [] }] });
    const rendered = option.tooltip.formatter({ name: attack, value: 10, data: [attack, 10] });
    assert.equal(rendered.includes('<img'), false, name);
    assert.ok(rendered.includes('&lt;img'), name);
  }
});

test('chart overrides cannot change object prototypes', () => {
  const override = JSON.parse('{"__proto__":{"polluted":true},"tooltip":{"constructor":{"prototype":{"polluted":true}}}}');
  const option: any = buildChartOption('bar', { categories: [], series: [] }, '', override);
  assert.equal(Object.getPrototypeOf(option), Object.prototype);
  assert.equal(({} as any).polluted, undefined);
  assert.equal(Object.hasOwn(option.tooltip, 'constructor'), false);
});
