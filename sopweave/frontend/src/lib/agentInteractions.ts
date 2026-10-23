import type { AgentResumeCommand } from './agentStream';

export interface InteractionOption { id: string; label: string; description: string }
export interface InteractionQuestion {
  id: string; title: string; kind: 'text' | 'single' | 'multiple' | 'confirm';
  options: InteractionOption[]; required: boolean; allow_custom: boolean;
}
export interface InteractionAnswer { question_id: string; selected: string[]; text: string; confirmed: boolean | null }
export interface AgentInteraction {
  interaction_id: string; run_id: string; tool_call_id: string; version: number;
  kind: 'input' | 'approval'; status: string; name?: string;
  arguments?: unknown; questions?: InteractionQuestion[];
}

export function mergeInteractions(current: AgentInteraction[], incoming: AgentInteraction[]): AgentInteraction[] {
  const items = new Map(current.map(item => [item.interaction_id, item]));
  for (const item of incoming) items.set(item.interaction_id, item);
  return [...items.values()];
}

export function validateAnswers(questions: InteractionQuestion[], answers: InteractionAnswer[]): string | null {
  for (const question of questions) {
    const answer = answers.find(item => item.question_id === question.id);
    if (!answer) { if (question.required) return `请填写：${question.title}`; continue; }
    if (question.kind === 'confirm') {
      if (question.required && answer.confirmed === null) return `请选择：${question.title}`;
    } else if (question.required && !answer.text.trim() && !answer.selected.length) return `请填写：${question.title}`;
    if (answer.selected.some(id => !question.options.some(option => option.id === id))) return '选择了无效选项';
    if (question.kind === 'single' && answer.selected.length > 1) return '单选题只能选择一项';
  }
  return null;
}

export function interactionCommand(item: AgentInteraction, command: Partial<AgentResumeCommand>): AgentResumeCommand {
  return { action: item.kind === 'approval' ? 'approve' : 'respond', ...command,
    interaction_id: item.interaction_id, version: item.version, tool_call_id: item.tool_call_id };
}

export async function fetchPendingInteractions(projectId: string): Promise<AgentInteraction[]> {
  const response = await fetch(`/api/agent/interactions?project_id=${encodeURIComponent(projectId)}`);
  if (!response.ok) throw new Error('无法恢复待处理交互');
  const result = await response.json() as { data: { interactions: AgentInteraction[] } };
  return result.data.interactions;
}
