export interface AgentStreamEvent {
  run_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentRunRequest {
  background?: boolean;
  run_id?: string;
  prompt: string;
  project_id: string;
  conversation_id: string;
  agent_id: string;
  model_id: string;
  work_dir: string;
  work_mode?: "manual" | "auto";
  user_message_id?: string;
  sop_mode?: 'auto'|'none'|'explicit';
  skill_id?: string;
  sop_inputs?: Record<string,string>;
  tool_groups?: ("core" | "office" | "project-data" | "analysis" | "skills" | "interaction")[];
}

export interface AgentResumeCommand {
  action: "approve" | "reject" | "respond";
  tool_call_id?: string;
  interaction_id: string;
  version: number;
  answers?: { question_id: string; selected?: string[]; text?: string; confirmed?: boolean | null }[];
  disposition?: 'accept' | 'skip' | 'cancel';
  note?: string;
}

export interface AgentToolEvent {
  tool_id: string;
  name: string;
  arguments?: unknown;
  output?: unknown;
  duration_ms?: number;
}

export interface AgentStreamHandlers {
  onEvent?: (event: AgentStreamEvent) => void;
  onRunCreated?: (event: AgentStreamEvent) => void;
  onText?: (delta: string) => void;
  onReasoning?: (delta: string) => void;
  onToolStart?: (event: AgentToolEvent) => void;
  onToolEnd?: (event: AgentToolEvent) => void;
  onApprovalRequired?: (payload: Record<string, unknown>) => void;
  onQuestion?: (payload: Record<string, unknown>) => void;
  onStep?: (index: number, total: number, label: string) => void;
  onPlan?: (steps: unknown[], status: string) => void;
  onWaiting?: (reason: string) => void;
  onDone?: (status: "completed" | "cancelled" | "stopped" | "failed") => void;
  onError?: (message: string) => void;
}

export async function streamAgentRun(
  request: AgentRunRequest,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  await postEventStream("/api/agent/runs", request, handlers, signal);
}

export async function resumeAgentRun(
  runId: string,
  command: AgentResumeCommand,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
  options?: {background:boolean;afterSeq?:number},
): Promise<void> {
  let body=command as AgentResumeCommand & {background?:boolean;after_seq?:number};
  if(options?.background){
    const snapshot=await getAgentRun(runId,signal);
    body={...command,background:true,after_seq:Math.max(options.afterSeq||0,snapshot.last_seq)};
  }
  await postEventStream(
    `/api/agent/runs/${encodeURIComponent(runId)}/resume`,
    body,
    handlers,
    signal,
  );
}

export async function cancelAgentRun(runId: string): Promise<void> {
  const response = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(await responseError(response));
}

export async function parseAgentEventStream(
  stream: ReadableStream<Uint8Array>,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
  afterSeq=0,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const seenSeq = new Set<number>();
  let buffer = "";
  let terminal = false;
  try {
    while (!terminal) {
      if (signal?.aborted) throw abortError();
      const { value, done } = await reader.read();
      if(signal?.aborted)throw abortError();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        terminal = dispatchBlock(block, seenSeq, handlers,afterSeq) || terminal;
        if (terminal) break;
      }
      if (done) {
        if (!terminal && buffer.trim()) dispatchBlock(buffer, seenSeq, handlers,afterSeq);
        break;
      }
    }
  } finally {
    if (terminal || signal?.aborted) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

async function postEventStream(
  url: string,
  body: AgentRunRequest | AgentResumeCommand,
  handlers: AgentStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw Object.assign(new Error(await responseError(response)),{status:response.status});
  if (!response.body) throw new Error("服务器未返回事件流");
  await parseAgentEventStream(response.body, handlers, signal);
}

function dispatchBlock(
  block: string,
  seenSeq: Set<number>,
  handlers: AgentStreamHandlers,
  afterSeq=0,
): boolean {
  const lines = block.split(/\r?\n/);
  if (lines.every((line) => !line || line.startsWith(":"))) return false;
  let id = 0;
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("id:")) id = Number(line.slice(3).trim()) || 0;
    else if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return false;
  let event: AgentStreamEvent;
  try {
    const parsed = JSON.parse(dataLines.join("\n")) as Partial<AgentStreamEvent>;
    event = {
      run_id: String(parsed.run_id ?? ""),
      seq: Number(parsed.seq ?? id),
      type: String(parsed.type ?? eventName),
      payload: isRecord(parsed.payload) ? parsed.payload : {},
      created_at: String(parsed.created_at ?? ""),
    };
  } catch {
    handlers.onError?.("收到无法解析的事件数据");
    return false;
  }
  if (event.seq && (event.seq<=afterSeq||seenSeq.has(event.seq))) return false;
  if (event.seq) seenSeq.add(event.seq);
  handlers.onEvent?.(event);
  return dispatchEvent(event, handlers);
}

function dispatchEvent(event: AgentStreamEvent, handlers: AgentStreamHandlers): boolean {
  const payload = event.payload;
  switch (event.type) {
    case "run.created":
      handlers.onRunCreated?.(event);
      break;
    case "message.delta":
      handlers.onText?.(String(payload.delta ?? ""));
      break;
    case "reasoning.delta":
      handlers.onReasoning?.(String(payload.delta ?? ""));
      break;
    case "tool.started":
      handlers.onToolStart?.({
        tool_id: String(payload.tool_id ?? ""),
        name: String(payload.name ?? ""),
        arguments: payload.arguments,
      });
      break;
    case "tool.completed":
      handlers.onToolEnd?.({
        tool_id: String(payload.tool_id ?? ""),
        name: String(payload.name ?? ""),
        output: payload.output,
        duration_ms: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
      });
      break;
    case "approval.required":
      handlers.onApprovalRequired?.(payload);
      break;
    case "input.required":
      handlers.onQuestion?.(payload);
      break;
    case "step.started":
      handlers.onStep?.(
        Number(payload.index ?? 0),
        Number(payload.total ?? 0),
        String(payload.title ?? "执行"),
      );
      break;
    case "plan.proposed":
    case "plan.updated":
      handlers.onPlan?.(
        Array.isArray(payload.steps) ? payload.steps : [],
        String(payload.status ?? event.type.split(".")[1]),
      );
      break;
    case "run.waiting":
      handlers.onWaiting?.(String(payload.reason ?? "input"));
      break;
    case "run.completed":
      handlers.onDone?.("completed");
      return true;
    case "run.cancelled":
      handlers.onDone?.("cancelled");
      return true;
    case "run.stopped":
      handlers.onDone?.("stopped");
      return true;
    case "run.failed":
      handlers.onError?.(String(payload.message ?? "智能体运行失败"));
      handlers.onDone?.("failed");
      return true;
    default:
      break;
  }
  return false;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function getAgentRun(runId:string,signal?:AbortSignal):Promise<{run:{status:string;error_message?:string};last_seq:number}>{
  const response=await fetch(`/api/agent/runs/${encodeURIComponent(runId)}`,{signal,cache:'no-store'});
  if(!response.ok)throw Object.assign(new Error(await responseError(response)),{status:response.status});
  return (await response.json()).data;
}

/** Reconnect to an existing execution; this function never starts a model request. */
export async function watchAgentRun(runId:string,afterSeq:number,handlers:AgentStreamHandlers,signal?:AbortSignal):Promise<void>{
  let sequence=afterSeq,finished=false,attempt=0;
  const wrapped:AgentStreamHandlers={...handlers,
    onEvent:event=>{sequence=Math.max(sequence,event.seq);handlers.onEvent?.(event);},
    onWaiting:reason=>{finished=true;handlers.onWaiting?.(reason);},
    onDone:status=>{finished=true;handlers.onDone?.(status);},
  };
  while(!signal?.aborted){
    try{
      const response=await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/stream?after_seq=${sequence}`,{headers:{Accept:'text/event-stream'},signal,cache:'no-store'});
      if(!response.ok)throw Object.assign(new Error(await responseError(response)),{status:response.status});
      if(!response.body)throw new Error('服务器未返回事件流');
      await parseAgentEventStream(response.body,wrapped,signal,sequence);
      if(finished)return;
      const snapshot=await getAgentRun(runId,signal);
      if(snapshot.run.status==='waiting'){handlers.onWaiting?.('input');return;}
      if(['completed','failed','stopped','cancelled'].includes(snapshot.run.status)){
        if(snapshot.run.status==='failed')handlers.onError?.(snapshot.run.error_message||'任务执行失败');
        handlers.onDone?.(snapshot.run.status as 'completed'|'failed'|'stopped'|'cancelled');return;
      }
    }catch(cause){
      if(signal?.aborted||(cause as Error).name==='AbortError')throw abortError();
      if((cause as Error&{status?:number}).status)throw cause;
    }
    if(++attempt>3)throw new Error('连接已中断，返回原项目后可查看运行状态并继续。');
    await new Promise<void>((resolve,reject)=>{
      if(signal?.aborted){reject(abortError());return;}
      const abort=()=>{clearTimeout(timer);reject(abortError());};
      const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},attempt*400);
      signal?.addEventListener('abort',abort,{once:true});
    });
  }
  throw abortError();
}

function abortError(): DOMException {
  return new DOMException("操作已取消", "AbortError");
}

async function responseError(response: Response): Promise<string> {
  try {
    const value = await response.json() as { detail?: string; message?: string };
    return value.detail || value.message || `请求失败 (${response.status})`;
  } catch {
    return `请求失败 (${response.status})`;
  }
}
