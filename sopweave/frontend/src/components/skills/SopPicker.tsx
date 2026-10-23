import { useEffect, useRef, useState } from 'react';
import { ChevronDown, CircleAlert, Workflow } from 'lucide-react';
import { fetchSkills, fetchSkillDetail, type SkillSummary, type SkillDetail, type SopSelection } from '../../lib/api';
import { subscribeSkillsChanged } from '../../lib/skillEvents';
import Dialog from '../ui/Dialog';
import UserSkillDetail from './UserSkillDetail';
import BuiltinSkillDetail from './BuiltinSkillDetail';

export default function SopPicker({ value, onChange, disabled = false, onValidationChange, onManage }: {
  value: SopSelection;
  onChange: (value: SopSelection) => void;
  disabled?: boolean;
  onValidationChange?: (error: string) => void;
  onManage?: () => void;
}) {
  const [open,setOpen] = useState(false);
  const [showDetail,setShowDetail] = useState(false);
  const [query,setQuery] = useState('');
  const [skills,setSkills] = useState<SkillSummary[]>([]);
  const [cursor,setCursor] = useState('');
  const [next,setNext] = useState('');
  const [detail,setDetail] = useState<SkillDetail|null>(null);
  const [listError,setListError] = useState('');
  const [detailError,setDetailError] = useState('');
  const [loading,setLoading] = useState(false);
  const [generation,setGeneration] = useState(0);
  const request = useRef(0);

  useEffect(() => subscribeSkillsChanged(() => setGeneration(current => current + 1)), []);
  useEffect(() => {
    if (!open) return;
    const id = ++request.current;
    setLoading(true);
    const timer = window.setTimeout(() => {
      void fetchSkills({query,...(cursor ? {cursor} : {})}).then(page => {
        if (id !== request.current) return;
        setSkills(previous => cursor ? [...previous, ...page.skills.filter(item => !previous.some(p => p.name === item.name))] : page.skills);
        setNext(page.next_cursor || '');
        setListError('');
      }).catch(cause => {
        if (id === request.current) setListError(cause.message);
      }).finally(() => {
        if (id === request.current) setLoading(false);
      });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      request.current++;
    };
  }, [open,query,cursor,generation]);
  useEffect(() => {
    if (value.mode !== 'explicit' || !value.skill_id) {
      setDetail(null);
      setDetailError('');
      return;
    }
    let disposed = false;
    setDetail(null);
    setDetailError('');
    void fetchSkillDetail(value.skill_id).then(result => {
      if (!disposed) setDetail(result);
    }).catch(cause => {
      if (disposed) return;
      setDetailError(cause.message);
      if (!disabled && cause.status === 404) onChange({mode:'explicit',skill_id:'',inputs:{}});
    });
    return () => {
      disposed = true;
    };
  }, [value.mode,value.skill_id,disabled,disabled ? 0 : generation]);

  const currentDetail = detail?.name === value.skill_id ? detail : null;
  const missing = currentDetail?.inputs?.filter(input => input.required && !(value.inputs?.[input.name] ?? input.default ?? '').trim()) || [];
  const validation = value.mode !== 'explicit' ? ''
    : !value.skill_id ? '请重新选择 SOP 后再发送任务。'
    : detailError ? `SOP 暂不可用：${detailError}`
    : !currentDetail ? '正在读取 SOP 配置，请稍后发送。'
    : missing.length ? `请填写 SOP 必填参数：${missing.map(input => input.name).join('、')}`
    : '';
  useEffect(() => {
    onValidationChange?.(validation);
  }, [validation,onValidationChange]);

  const label = value.mode === 'auto'
    ? '自动发现'
    : value.mode === 'none'
      ? '仅内置流程'
      : currentDetail?.title || currentDetail?.name || '指定 SOP';
  const title = value.mode === 'auto'
    ? '本次任务由 AI 自动发现并加载相关 SOP'
    : value.mode === 'none'
      ? '本次任务不加载“我的 SOP”，保留内置能力与 Office 支撑'
      : validation
        ? `已选择 ${label}：${validation}`
        : `本次任务固定使用 ${label}${currentDetail?.version ? `（v${currentDetail.version}）` : ''}`;
  const choose = (mode: SopSelection['mode'], id = '') => {
    onChange({mode,skill_id:id,inputs:{}});
    setShowDetail(false);
    if (mode !== 'explicit') setOpen(false);
  };

  return <>
    <button
      type="button"
      aria-label="选择本次任务的 SOP"
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-invalid={Boolean(validation)}
      disabled={disabled}
      onClick={() => {
        setCursor('');
        setOpen(true);
      }}
      title={title}
      className={`flex h-7 max-w-44 items-center gap-1.5 rounded-md px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        value.mode === 'none' ? 'text-zinc-500 hover:bg-zinc-100' : 'text-blue-700 hover:bg-blue-50'
      }`}
    >
      <Workflow size={13} className="shrink-0" />
      <span className="truncate font-medium">{label}</span>
      {validation ? <CircleAlert size={11} className="shrink-0 text-amber-600" /> : <ChevronDown size={11} className="shrink-0 text-zinc-400" />}
    </button>

    {open && <Dialog title="选择本次任务的 SOP" onClose={() => setOpen(false)} className="max-w-2xl">
      <header className="border-b p-5">
        <h2 className="font-semibold">选择本次任务的 SOP</h2>
        <p className="mt-1 text-sm text-zinc-500">选择会用于下一次发送；运行开始后固定当时的 Skill 版本，执行中不能切换。</p>
      </header>
      <div className="space-y-4 overflow-y-auto p-5">
        <div className="grid gap-2 sm:grid-cols-3">
          <button aria-label="自动发现" className={`rounded-lg border p-3 text-left text-sm ${value.mode === 'auto' ? 'border-blue-300 bg-blue-50' : ''}`} onClick={() => choose('auto')}>
            <span className="block font-medium">自动发现</span>
            <span className="mt-1 block text-xs text-zinc-500">AI 根据任务加载相关 SOP。</span>
          </button>
          <div aria-current={value.mode === 'explicit' ? 'true' : undefined} className={`rounded-lg border p-3 text-left text-sm ${value.mode === 'explicit' ? 'border-blue-300 bg-blue-50' : ''}`}>
            <span className="block font-medium">指定 SOP</span>
            <span className="mt-1 block text-xs text-zinc-500">从下方列表选择一个流程。</span>
          </div>
          <button aria-label="仅内置流程" className={`rounded-lg border p-3 text-left text-sm ${value.mode === 'none' ? 'border-zinc-300 bg-zinc-50' : ''}`} onClick={() => choose('none')}>
            <span className="block font-medium">仅内置流程</span>
            <span className="mt-1 block text-xs text-zinc-500">不加载“我的 SOP”。</span>
          </button>
        </div>

        {value.mode === 'auto' && <p className="rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700">模型会从已授权 SOP 中按任务发现并调用所需流程。</p>}
        {value.mode === 'none' && <p className="rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-600">用户 SOP 不会进入本次任务；内置流程与 Office 支撑仍可用。</p>}
        {value.mode === 'explicit' && <section className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm text-zinc-800">{currentDetail?.title || currentDetail?.name || '正在读取所选 SOP'}</strong>
            {currentDetail?.version && <span className="text-xs text-zinc-500">v{currentDetail.version}</span>}
            {currentDetail && <button className="ml-auto text-xs text-blue-700" onClick={() => setShowDetail(true)}>查看流程</button>}
          </div>
          {detailError && <p role="alert" className="mt-2 text-xs text-red-700">SOP 暂不可用：{detailError}</p>}
          {!detailError && value.skill_id && !currentDetail && <p role="status" className="mt-2 text-xs text-zinc-500">正在读取 SOP 配置…</p>}
          {currentDetail && !!currentDetail.inputs?.length && <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {currentDetail.inputs.map(input => <label key={input.name} className="text-xs text-zinc-600">
              {input.name}{input.required ? ' *' : ''}
              <input
                aria-label={`SOP 参数 ${input.name}`}
                required={input.required}
                disabled={disabled}
                className="mt-1 w-full rounded-lg border border-zinc-300 bg-white px-2 py-2 text-sm"
                placeholder={input.description}
                value={value.inputs?.[input.name] ?? input.default ?? ''}
                onChange={event => onChange({...value,inputs:{...value.inputs,[input.name]:event.target.value}})}
              />
              {input.description && <span className="mt-1 block text-zinc-500">{input.description}</span>}
            </label>)}
          </div>}
          {validation && <p role="status" className="mt-2 text-xs text-amber-700">{validation}</p>}
        </section>}

        <input
          autoFocus
          aria-label="搜索可选 SOP"
          className="w-full rounded-lg border px-3 py-2 text-sm"
          placeholder="搜索名称或功能"
          value={query}
          onChange={event => {
            setCursor('');
            setQuery(event.target.value);
          }}
        />
        {(['builtin','user'] as const).map(origin => <section key={origin}>
          <h3 className="mb-2 text-xs font-medium text-zinc-500">{origin === 'builtin' ? '内置标准流程' : '我的 SOP'}</h3>
          <div className="space-y-2">{skills.filter(skill => (skill.origin || (skill.name.startsWith('user-') ? 'user' : 'builtin')) === origin).map(skill =>
            <button
              key={skill.name}
              type="button"
              aria-label={skill.title || skill.name}
              className={`block w-full rounded-lg border p-3 text-left hover:border-blue-400 hover:bg-blue-50 ${
                value.mode === 'explicit' && value.skill_id === skill.name ? 'border-blue-400 bg-blue-50' : ''
              }`}
              onClick={() => choose('explicit',skill.name)}
            >
              <span className="text-sm font-medium">{skill.title || skill.name}</span>
              <span className="ml-2 text-xs text-zinc-500">{skill.step_count} 步{skill.version ? ` · v${skill.version}` : ''}</span>
              <span className="mt-1 block text-xs text-zinc-500">{skill.description}</span>
            </button>
          )}</div>
        </section>)}
        {loading && <p role="status" className="text-sm text-zinc-500">加载中…</p>}
        {listError && <div role="alert" className="text-sm text-red-700">{listError}<button className="ml-3 underline" onClick={() => setGeneration(current => current + 1)}>重试</button></div>}
        {!loading && !listError && !skills.length && <p className="text-sm text-zinc-500">未找到匹配的 SOP，可换个关键词或创建新流程。</p>}
        {next && <button disabled={loading} onClick={() => setCursor(next)} className="ui-button">加载更多 SOP</button>}
      </div>
      <footer className="flex items-center gap-3 border-t p-4">
        {onManage && <button className="text-sm text-blue-700" onClick={() => {
          setOpen(false);
          onManage();
        }}>管理或创建 SOP</button>}
        <button className="ui-button ml-auto" onClick={() => setOpen(false)}>完成</button>
      </footer>
    </Dialog>}

    {showDetail && currentDetail && <Dialog title="SOP 流程详情" onClose={() => setShowDetail(false)} className="max-w-3xl">
      <div className="overflow-y-auto p-5 pt-12">{currentDetail.origin === 'user' ? <UserSkillDetail detail={currentDetail}/> : <BuiltinSkillDetail detail={currentDetail}/>}</div>
    </Dialog>}
  </>;
}
