import {useEffect,useState} from 'react';
import {listSkillDrafts,type SkillDraftSummary} from '../../lib/api';

const STATES: Record<string,string> = {created:'待整理',ready:'待审阅',generating:'生成中',needs_input:'待补充',failed:'待处理'};
export default function SkillDraftList({generation,onOpen}: {generation:number;onOpen:(id:string)=>void}) {
  const [items,setItems]=useState<SkillDraftSummary[]>([]), [next,setNext]=useState('');
  const [cursor,setCursor]=useState(''), [loading,setLoading]=useState(true), [error,setError]=useState('');
  const [retry,setRetry]=useState(0);
  useEffect(() => {setCursor('');},[generation]);
  useEffect(() => {
    let disposed=false; setLoading(true);
    void listSkillDrafts(cursor).then(page => {
      if(disposed) return;
      setItems(previous => cursor ? [...previous,...page.drafts.filter(item => !previous.some(p=>p.draft_id===item.draft_id))] : page.drafts);
      setNext(page.next_cursor);setError('');
    }).catch(cause=>{if(!disposed)setError(cause.message);}).finally(()=>{if(!disposed)setLoading(false);});
    return()=>{disposed=true;};
  },[cursor,generation,retry]);
  return <section aria-label="SOP 草稿" className="rounded-xl border bg-white p-5">
    <h2 className="font-semibold">草稿</h2><p className="mb-4 mt-1 text-sm text-zinc-500">草稿保存在本机服务中，发布后进入“我的 SOP”。</p>
    {error && <p role="alert" className="text-sm text-red-700">{error}<button className="ml-3 underline" onClick={()=>setRetry(v=>v+1)}>重试</button></p>}
    <div className="space-y-2">{items.map(item=><button key={item.draft_id} className="flex w-full items-center gap-3 rounded-lg border p-3 text-left hover:bg-blue-50" onClick={()=>onOpen(item.draft_id)}>
      <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{item.title || '未命名草稿'}</span><span className="text-xs text-zinc-500">{new Date(item.updated_at).toLocaleString()} · 修订 {item.draft_revision}</span></span>
      <span className="text-xs text-blue-700">{STATES[item.state] || '待完善'} · 继续编辑</span>
    </button>)}</div>
    {loading && <p role="status" className="mt-3 text-sm text-zinc-500">加载草稿…</p>}
    {!loading&&!error&&!items.length&&<p className="text-sm text-zinc-500">暂无草稿。点击“新增 SOP”描述你的流程。</p>}
    {next&&<button disabled={loading} className="mt-3 rounded-lg border px-3 py-2 text-sm" onClick={()=>setCursor(next)}>加载更多草稿</button>}
  </section>;
}
