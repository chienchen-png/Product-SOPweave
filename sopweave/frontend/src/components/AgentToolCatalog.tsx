import type { ToolCategory, ToolEntry } from '../lib/api';

export const TOOL_GROUP_LABELS: Record<string, string> = {
  core: '文件与程序执行', skills: 'Skill 读取', interaction: '用户询问',
  office: 'Excel 与 Word', 'project-data': '项目数据', analysis: '财务分析',
};

const RISK_LABELS: Record<ToolEntry['risk'], string> = {
  read: '读取与计算', write: '文件写入', data_change: '数据修改', command: '程序执行',
};

export default function AgentToolCatalog({ catalog }: { catalog: ToolCategory[] }) {
  const tools = catalog.flatMap(group => group.tools);
  const initial = tools.filter(tool => tool.loading === 'default').length;
  return <section aria-label="智能体工具清单" className="rounded-lg border border-zinc-200 bg-white p-4">
    <h3 className="text-sm font-semibold text-zinc-700">工具清单</h3>
    <p className="mb-3 mt-1 text-xs text-zinc-500">可用 {tools.length} 个 · 默认加载 {initial} 个 · 按需加载 {tools.length - initial} 个</p>
    <p className="mb-3 text-xs text-zinc-500">清单按当前智能体的授权配置生成。按需工具通过授权 Skill 或任务配置启用。</p>
    {!tools.length && <p className="text-xs text-zinc-400">暂无工具数据</p>}
    {catalog.map(group => <div key={group.category} className="mb-3 last:mb-0">
      <h4 className="mb-1 text-xs font-medium text-zinc-600">{TOOL_GROUP_LABELS[group.category] || group.category}（{group.tools.length}）</h4>
      <ul className="space-y-1">
        {group.tools.map(tool => <li key={tool.name} className="rounded bg-zinc-50 p-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <code className="text-xs font-medium text-blue-700">{tool.name}</code>
            <span className={tool.loading === 'default' ? 'text-green-700' : 'text-zinc-500'}>{tool.loading === 'default' ? '默认加载' : '按需加载'}</span>
            <span className="text-zinc-500">{RISK_LABELS[tool.risk]}</span>
          </div>
          <p className="mt-1 text-xs leading-snug text-zinc-500">{tool.description}</p>
        </li>)}
      </ul>
    </div>)}
  </section>;
}
