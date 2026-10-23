import React from 'react';
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { render, screen, cleanup, fireEvent, waitFor,act } from '@testing-library/react';
import FileEditor from '../src/components/FileEditor';
import MarkdownEditor from '../src/components/MarkdownEditor';
import PreviewPage from '../src/pages/PreviewPage';

afterEach(()=>{cleanup();window.localStorage.clear();});
test('preview close closes its tab and reopening preserves a draft against newer disk content', async()=>{
  let diskVersion='v1';
  globalThis.fetch=async url=>new Response(JSON.stringify({data:
    String(url).includes('/api/projects')?[{id:'p',name:'验收项目'}]:
    String(url).includes('/meta?')?{file:{viewer_type:'text'}}:
    {content:diskVersion==='v1'?'original':'newer disk',version:diskVersion}
  }));
  const originalClose=window.close;
  let closed=0;
  window.close=()=>{closed++;};
  try{
    const page=render(<PreviewPage projectId="p" filePath="draft.txt"/>);
    fireEvent.change(await screen.findByRole('textbox'),{target:{value:'未保存的编辑'}});
    fireEvent.click(screen.getByRole('button',{name:'关闭'}));
    assert.equal(closed,1,'close must invoke the browser tab close action');
    page.unmount();diskVersion='v2';
    render(<PreviewPage projectId="p" filePath="draft.txt"/>);
    await waitFor(()=>assert.equal((screen.getByRole('textbox') as HTMLTextAreaElement).value,'未保存的编辑'));
    assert.match(screen.getByRole('alert').textContent||'',/磁盘版本已变化/);
    const draft=JSON.parse(window.localStorage.getItem('sopweave.file-draft.v1:p:draft.txt')||'null');
    assert.equal(draft.version,'v1');
    assert.equal(draft.content,'未保存的编辑');
  }finally{window.close=originalClose;}
});
test('file saves advance the version; conflict preserves edited content', async () => {
  const writes: any[] = [];
  globalThis.fetch = async (url, options) => {
    const path = String(url);
    if (path.includes('/meta?')) return new Response(JSON.stringify({data:{file:{viewer_type:'text'}}}));
    if (path.includes('/read?')) return new Response(JSON.stringify({data:{content:'old',version:'v1'}}));
    writes.push(JSON.parse(String(options?.body)));
    if (writes.length === 1) return new Response(JSON.stringify({data:{version:'v2'}}));
    return new Response(JSON.stringify({error:'file changed'}), {status:409});
  };
  let saved = 0;
  render(<FileEditor projectId="p" filePath="report.txt" onClose={()=>{}} onSaved={()=>saved++}/>);
  const editor = await screen.findByRole('textbox');
  fireEvent.change(editor, {target:{value:'first'}});
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await waitFor(()=>assert.equal(saved, 1));
  fireEvent.change(editor, {target:{value:'second'}});
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await screen.findByRole('alert');
  assert.equal(writes[0].expected_version, 'v1');
  assert.equal(writes[1].expected_version, 'v2');
  assert.equal((editor as HTMLTextAreaElement).value, 'second');
  assert.ok(screen.getByText('● 未保存'));
});

test('typing while a save is pending keeps the newer content dirty and uses the new disk version next',async()=>{
  const writes:any[]=[];let finish:(response:Response)=>void=()=>{};
  globalThis.fetch=async(url,options)=>{
    if(String(url).includes('/meta?'))return new Response(JSON.stringify({data:{file:{viewer_type:'text'}}}));
    if(String(url).includes('/read?'))return new Response(JSON.stringify({data:{content:'old',version:'v1'}}));
    writes.push(JSON.parse(String(options?.body)));
    return writes.length===1?new Promise<Response>(resolve=>{finish=resolve;}):new Response(JSON.stringify({data:{version:'v3'}}));
  };
  render(<FileEditor projectId="p" filePath="report.txt" onClose={()=>{}} onSaved={()=>{}}/>);
  const editor=await screen.findByRole('textbox');
  fireEvent.change(editor,{target:{value:'first'}});fireEvent.click(screen.getByRole('button',{name:'保存'}));
  fireEvent.change(editor,{target:{value:'second'}});
  await act(async()=>finish(new Response(JSON.stringify({data:{version:'v2'}}))));
  assert.ok(screen.getByText('● 未保存'));
  fireEvent.click(screen.getByRole('button',{name:'保存'}));
  await waitFor(()=>assert.equal(writes.length,2));
  assert.equal(writes[1].content,'second');assert.equal(writes[1].expected_version,'v2');
});

test('an unsaved report draft is restored when the file editor is reopened',async()=>{
  globalThis.fetch=async url=>new Response(JSON.stringify({data:String(url).includes('/meta?')?{file:{viewer_type:'text'}}:{content:'disk',version:'v1'}}));
  const app=render(<FileEditor projectId="p" filePath="draft.txt" onClose={()=>{}} onSaved={()=>{}}/>);
  fireEvent.change(await screen.findByRole('textbox'),{target:{value:'应当恢复的修改'}});app.unmount();
  render(<FileEditor projectId="p" filePath="draft.txt" onClose={()=>{}} onSaved={()=>{}}/>);
  await waitFor(()=>assert.equal((screen.getByRole('textbox') as HTMLTextAreaElement).value,'应当恢复的修改'));
});

test('Markdown uses the native render base and keeps source/export paths portable', async () => {
  const markdown = '![图](<images/中文 a&b.png>)\n';
  let exported = '';
  render(<MarkdownEditor initialValue={markdown} resourceBaseUrl="/api/files/resource/p/" onExportWord={value=>exported=value}/>);
  const options = (globalThis as any).__vditorOptions;
  assert.equal(options.value, markdown);
  assert.equal("upload" in options, false, "omit absent upload options so Vditor keeps its defaults");
  assert.equal(options.cdn + "/dist/js/i18n/zh_CN.js", "/static/app/vditor/dist/js/i18n/zh_CN.js");
  assert.equal(options.preview.markdown.linkBase, '/api/files/resource/p/');
  assert.ok(options.toolbar.includes('export'));
  await waitFor(()=>assert.ok((globalThis as any).__vditorReady));
  options.toolbar.find((tool: any)=>tool.name==='exportWord').click();
  assert.equal(exported, markdown);
  const require = createRequire(import.meta.url);
  require('../node_modules/vditor/dist/js/lute/lute.min.js');
  const lute = ((globalThis as any).Lute ?? (window as any).Lute).New();
  lute.SetLinkBase(options.preview.markdown.linkBase);
  const html = lute.Md2VditorIRDOM(markdown);
  assert.ok(html.includes('/api/files/resource/p/images/'));
  const restored = lute.VditorIRDOM2Md(html);
  assert.ok(restored.includes('images/'));
  assert.equal(restored.includes('/api/'), false);
});
