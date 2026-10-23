import type {WorkbookSheetInfo} from '../lib/api';

/** One source of disclosure for both review and the persisted data index. */
export default function WorkbookImportSummary({sheets=[],skippedSheets=[]}:{
  sheets?:WorkbookSheetInfo[];
  skippedSheets?:{name:string;classification:string}[];
}) {
  const visible=sheets.filter(sheet=>
    (sheet.state&&sheet.state!=='visible') || (sheet.hidden_row_count??sheet.hidden_rows?.length??0)>0 ||
    (sheet.hidden_column_count??sheet.hidden_columns?.length??0)>0);
  if(!visible.length&&!skippedSheets.length)return null;
  const coordinates=(values:(string|number)[],count:number)=>
    `${values.slice(0,20).join('、')}${count>20?`（前20项，共${count}项）`:''}`;
  return <section aria-label="工作簿导入说明" className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900">
    <p className="font-medium">工作簿导入说明</p>
    {!!visible.length&&<p>隐藏内容不会自动跳过，是否导入取决于确认的数据区域。</p>}
    <ul>{skippedSheets.map(sheet=><li key={`skip:${sheet.name}`}>{sheet.name}：跳过（{sheet.classification==='empty'?'空表':'说明表'}）</li>)}
      {visible.map(sheet=>{
        const rows=sheet.hidden_row_count??sheet.hidden_rows?.length??0;
        const cols=sheet.hidden_column_count??sheet.hidden_columns?.length??0;
        return <li key={sheet.name}>{sheet.name}：{sheet.state&&sheet.state!=='visible'?'隐藏工作表；':''}
          {rows>0?`隐藏行 ${coordinates(sheet.hidden_rows||[],rows)}；`:''}
          {cols>0?`隐藏列 ${coordinates(sheet.hidden_columns||[],cols)}`:''}
        </li>;
      })}
    </ul>
  </section>;
}
