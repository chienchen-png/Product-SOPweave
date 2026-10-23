import {useEffect,useState} from 'react';
import {importLegacyHistory} from '../lib/api';

export default function LegacyHistoryNotice({projectId,kind,onImported}:{projectId:string;kind:'recon'|'finmod';onImported?:()=>void}){
  const key=kind==='recon'?'app_recon_run_history':'app_finmod_report_history';
  const [records,setRecords]=useState<Record<string,unknown>[]>([]),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  useEffect(()=>{try{const saved=JSON.parse(window.localStorage.getItem(key)||'[]');if(Array.isArray(saved))setRecords(saved.filter(item=>item&&typeof item==='object'&&!Array.isArray(item)).slice(0,50));}catch{}},[key]);
  if(!records.length&&!message)return null;
  return <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-900">
    {records.length>0&&<><p>发现 {records.length} 条旧浏览器记录。导入后由服务端保存；历史说明标为待复核，不作为新的程序执行证据。</p><button disabled={busy} className="mt-2 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm" onClick={()=>{
      setBusy(true);setMessage('');
      const values=records.map(record=>({id:record.id||record.runId,run_id:record.runId,params:record.params||{},report:record.report||'',rel_path:record.rel_path||'',filename:record.filename||'',goal:record.goal||'',created_at:record.createdAt||record.time||''}));
      void importLegacyHistory(projectId,kind,values).then(result=>{setMessage(`已导入 ${result.imported} 条。${result.skipped.join('；')}`);if(!result.skipped.length){window.localStorage.removeItem(key);setRecords([]);}onImported?.();}).catch(cause=>setMessage(cause.message)).finally(()=>setBusy(false));
    }}>{busy?'正在导入…':'导入旧记录'}</button></>}
    {message&&<p role="status" className="mt-2">{message}</p>}
  </div>;
}
