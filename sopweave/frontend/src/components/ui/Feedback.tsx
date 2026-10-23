import {createContext,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import Dialog from './Dialog';
import {X} from 'lucide-react';

type Notice = {id:number; text:string};
type Confirmation = {text:string; resolve:(accepted:boolean)=>void};
const Context = createContext<{notify:(text:string)=>void; confirm:(text:string)=>Promise<boolean>} | null>(null);

export function FeedbackProvider({children}: {children:ReactNode}) {
  const [notices,setNotices] = useState<Notice[]>([]);
  const [confirmation,setConfirmation] = useState<Confirmation|null>(null);
  const current = useRef<Confirmation|null>(null);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => { timers.current.forEach(clearTimeout); current.current?.resolve(false); }, []);
  const finish = (accepted:boolean) => {current.current?.resolve(accepted);current.current=null;setConfirmation(null);};
  const notify = (text:string) => {
    const id=Date.now()+Math.random();
    setNotices(previous => [...previous.slice(-2),{id,text}]);
    timers.current.push(setTimeout(() => setNotices(previous => previous.filter(item => item.id!==id)),7000));
  };
  const confirm = (text:string) => new Promise<boolean>(resolve => {
    current.current?.resolve(false);
    current.current={text,resolve}; setConfirmation(current.current);
  });
  return <Context.Provider value={{notify,confirm}}>{children}
    <div aria-live="polite" className="fixed bottom-4 right-4 z-[70] flex max-w-md flex-col gap-2">
      {notices.map(item => <div key={item.id} role="status" className="toast-notice flex items-start gap-3 rounded-xl border bg-white p-4 text-sm shadow-lg"><span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{item.text}</span><button aria-label="关闭通知" className="ui-icon-button -my-1 -mr-1 shrink-0" onClick={() => setNotices(previous => previous.filter(value => value.id!==item.id))}><X size={16}/></button></div>)}
    </div>
    {confirmation && <Dialog title="确认操作" onClose={() => finish(false)} className="max-w-md" showClose={false}>
      <div className="space-y-4 p-6"><h2 className="font-semibold">确认操作</h2><p className="whitespace-pre-wrap text-sm leading-6 text-zinc-600">{confirmation.text}</p>
        <div className="flex justify-end gap-3"><button autoFocus className="ui-button" onClick={() => finish(false)}>取消</button><button className="rounded-lg bg-red-600 px-4 py-2 text-sm text-white" onClick={() => finish(true)}>确认</button></div>
      </div>
    </Dialog>}
  </Context.Provider>;
}

export function useFeedback() {
  const value=useContext(Context);
  if(!value) throw new Error('交互组件必须位于 FeedbackProvider 内');
  return value;
}
