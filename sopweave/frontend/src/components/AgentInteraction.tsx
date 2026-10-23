import { createContext, useContext, useMemo, useState } from 'react';
import {
  AssistantRuntimeProvider, MessagePrimitive, ThreadPrimitive,
  makeAssistantToolUI, useExternalStoreRuntime, type ThreadMessageLike,
} from '@assistant-ui/react';
import { interactionCommand, validateAnswers, type AgentInteraction, type InteractionAnswer } from '../lib/agentInteractions';
import type { AgentResumeCommand } from '../lib/agentStream';

const SubmissionContext = createContext<{ busy: boolean; items: AgentInteraction[] }>({ busy: false, items: [] });

const HumanInputTool = makeAssistantToolUI<{ interaction_id: string }, Partial<AgentResumeCommand>>({
  toolName: 'human_interaction',
  render: function HumanInput({ args, addResult }) {
    const { busy, items } = useContext(SubmissionContext);
    const item = items.find(value => value.interaction_id === args.interaction_id)!;
    const [answers, setAnswers] = useState<InteractionAnswer[]>(() => (item.questions ?? []).map(question => ({
      question_id: question.id, selected: [], text: '', confirmed: null,
    })));
    const [note, setNote] = useState('');
    const [validation, setValidation] = useState<string | null>(null);
    const update = (id: string, patch: Partial<InteractionAnswer>) => setAnswers(values => values.map(answer =>
      answer.question_id === id ? { ...answer, ...patch } : answer));
    const send = (command: Partial<AgentResumeCommand>) => {
      const error = item.kind === 'input' && !command.disposition ? validateAnswers(item.questions ?? [], answers) : null;
      setValidation(error);
      if (!error) addResult({ ...command, note, answers: item.kind === 'input' ? answers : [] });
    };
    return <form className="ui-panel space-y-4 p-5 shadow-sm" onSubmit={event => {
      event.preventDefault(); send({ action: item.kind === 'approval' ? 'approve' : 'respond' });
    }} aria-label={item.kind === 'approval' ? '操作审批' : '智能体提问'}>
      <p className="text-sm font-semibold">{item.kind === 'approval' ? `是否执行 ${item.name}？` : '需要你补充信息'}</p>
      {item.kind === 'approval' && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-2 text-xs">{JSON.stringify(item.arguments, null, 2)}</pre>}
      {(item.questions ?? []).map(question => {
        const answer = answers.find(value => value.question_id === question.id)!;
        return <fieldset key={question.id} disabled={busy} className="space-y-2">
          <legend className="mb-1 text-sm font-medium">{question.title}{question.required ? ' *' : ''}</legend>
          {question.kind === 'confirm' ? <div className="flex gap-5">{[true, false].map(value => <label key={String(value)} className="flex gap-2 text-sm">
            <input type="radio" name={`${item.interaction_id}-${question.id}`} checked={answer.confirmed === value}
              onChange={() => update(question.id, { confirmed: value })}/>{value ? '是' : '否'}</label>)}</div>
            : question.options.map(option => <label key={option.id} className="flex items-start gap-2 text-sm">
              <input type={question.kind === 'multiple' ? 'checkbox' : 'radio'} name={`${item.interaction_id}-${question.id}`}
                checked={answer.selected.includes(option.id)} onChange={() => update(question.id, { selected: question.kind === 'multiple'
                  ? answer.selected.includes(option.id) ? answer.selected.filter(id => id !== option.id) : [...answer.selected, option.id]
                  : [option.id] })}/>
              <span>{option.label}{option.description && <span className="block text-xs text-zinc-500">{option.description}</span>}</span>
            </label>)}
          {(question.kind === 'text' || (question.kind !== 'confirm' && question.allow_custom)) && <textarea
            aria-label={question.kind === 'text' ? question.title : `${question.title}：自定义补充`} value={answer.text} maxLength={10000}
            onChange={event => update(question.id, { text: event.target.value })} rows={2}
            className="w-full rounded-lg border border-zinc-300 p-2 text-sm"/>}
        </fieldset>;
      })}
      <label className="block text-xs text-zinc-500">备注（可选）<input aria-label="备注" value={note} maxLength={4000}
        disabled={busy} onChange={event => setNote(event.target.value)} className="mt-1 w-full rounded-lg border p-2 text-sm"/></label>
      {validation && <p role="alert" className="text-sm text-red-700">{validation}</p>}
      <div className="flex justify-end gap-3 text-sm">
        {item.kind === 'approval' ? <button type="button" disabled={busy} onClick={() => send({ action: 'reject' })}>拒绝</button>
          : <><button type="button" disabled={busy} onClick={() => send({ action: 'respond', disposition: 'cancel' })}>取消本次任务</button>
            {(item.questions ?? []).every(question => !question.required) && <button type="button" disabled={busy}
              onClick={() => send({ action: 'respond', disposition: 'skip' })}>跳过</button>}</>}
        <button type="submit" disabled={busy} className="ui-button ui-button-primary">
          {busy ? '正在提交…' : item.kind === 'approval' ? '批准执行' : '提交回答'}</button>
      </div>
    </form>;
  },
});

function InteractionMessage() {
  return <MessagePrimitive.Root><MessagePrimitive.Content/></MessagePrimitive.Root>;
}

export default function AgentInteractionPanel({ items, onSubmit, disabled = false }: {
  items: AgentInteraction[];
  onSubmit: (item: AgentInteraction, command: AgentResumeCommand) => Promise<void>;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messages = useMemo<ThreadMessageLike[]>(() => items.map(item => ({
    id: item.interaction_id, role: 'assistant', status: { type: 'requires-action', reason: 'tool-calls' },
    content: [{ type: 'tool-call', toolCallId: item.interaction_id, toolName: 'human_interaction', args: { interaction_id: item.interaction_id } }],
  })), [items]);
  const runtime = useExternalStoreRuntime({ messages, convertMessage: message => message, isRunning: busy,
    onNew: async () => {},
    onAddToolResult: async ({ toolCallId, result }) => {
      const item = items.find(value => value.interaction_id === toolCallId);
      if (!item || busy || disabled) return;
      setBusy(true); setError(null);
      try { await onSubmit(item, interactionCommand(item, result as Partial<AgentResumeCommand>)); }
      catch (caught) { setError((caught as Error).message || '提交失败，请重试'); }
      finally { setBusy(false); }
    },
  });
  if (!items.length) return null;
  return <AssistantRuntimeProvider runtime={runtime}>
    <SubmissionContext.Provider value={{ busy: busy || disabled, items }}>
      <HumanInputTool/>
      <section aria-label="待处理交互" className="mb-3 max-h-[55vh] space-y-3 overflow-auto">
        <p className="text-xs text-zinc-500">待处理 {items.length} 项；全部决定后继续执行。</p>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        <ThreadPrimitive.Messages components={{ Message: InteractionMessage }}/>
      </section>
    </SubmissionContext.Provider>
  </AssistantRuntimeProvider>;
}
