import React from 'react';
import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {MemoryRouter,useLocation} from 'react-router-dom';
import SkillConfigPanel from '../src/components/SkillConfigPanel';

afterEach(cleanup);

function Location(){const location=useLocation();return <output>{location.pathname+location.search}</output>;}

test('SOP detail selection survives a copied URL and remount', async()=>{
  const skills=['first','second'].map(name=>({name,title:`流程${name}`,origin:'builtin',step_count:0}));
  globalThis.fetch=async input=>{
    const url=new URL(String(input),'http://localhost');
    const name=url.pathname.split('/').at(-1);
    const data=url.pathname==='/api/skills'?{skills,user_skills_enabled:true}:
      {...skills.find(item=>item.name===name),description:'说明',body:'步骤正文',toolsets:[],steps:[],capabilities:{models:[],charts:[],stats:[]}};
    return new Response(JSON.stringify({success:true,data}));
  };
  const app=render(<MemoryRouter initialEntries={['/sops?keep=1']}><SkillConfigPanel/><Location/></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:/流程second/}));
  await screen.findByRole('heading',{name:'流程second',exact:true});
  await waitFor(()=>assert.equal(document.querySelector('output')?.textContent,'/sops?keep=1&skill=second'));
  const copied=document.querySelector('output')!.textContent!;
  app.unmount();
  render(<MemoryRouter initialEntries={[copied]}><SkillConfigPanel/></MemoryRouter>);
  assert.ok(await screen.findByRole('heading',{name:'流程second',exact:true}));
});
test('the SOP library restores multiple server drafts without a browser resume key',async()=>{
  window.sessionStorage.clear();
  globalThis.fetch=async input=> {
    const url=String(input);
    const data=url.endsWith('/tools') ? {catalog:[]}
      : url.includes('/drafts/draft-two') ? {draft_id:'draft-two',draft_revision:1,state:'created',spec:null,source:[{kind:'text',text:'凭证审核需求'}],answers:[],questions:[]}
      : url.includes('/drafts') ? {drafts:[{draft_id:'draft-one',draft_revision:1,title:'银行核对草稿',state:'created',updated_at:'2026-10-17T00:00:00Z'},{draft_id:'draft-two',draft_revision:1,title:'凭证检查草稿',state:'created',updated_at:'2026-10-17T00:00:00Z'}],next_cursor:''}
      : {skills:[],user_skills_enabled:true};
    return new Response(JSON.stringify({success:true,data}));
  };
  render(<MemoryRouter><SkillConfigPanel/></MemoryRouter>);
  fireEvent.click(screen.getByRole('button',{name:'用户 SOP'}));
  fireEvent.click(screen.getByRole('button',{name:'草稿'}));
  await screen.findByText('银行核对草稿');
  fireEvent.click(await screen.findByRole('button',{name:/凭证检查草稿/}));
  const field=await screen.findByLabelText('需求描述') as HTMLTextAreaElement;
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(field.value,'凭证审核需求');
});
