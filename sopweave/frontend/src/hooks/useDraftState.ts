import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { readMigratedStorage } from '../lib/brand';

/** Immediate input backup; durable business drafts are saved by the workspace API. */
export function useDraftState<T>(scope: string, initial: T): [T, Dispatch<SetStateAction<T>>, string] {
  const initialRef = useRef(initial);
  const read = (key: string): T => {
    try {
      const raw = readMigratedStorage(`sopweave.draft.v1:${key}`);
      return raw ? JSON.parse(raw) as T : initialRef.current;
    } catch { return initialRef.current; }
  };
  const [state, setState] = useState(() => ({ scope, value: read(scope) }));
  const [error, setError] = useState('');
  const current = useRef(state);
  if (current.current.scope !== scope) current.current = { scope, value: read(scope) };
  else if (state.scope === scope) current.current = state;
  const setValue = useCallback<Dispatch<SetStateAction<T>>>((action) => {
    const value = typeof action === 'function' ? (action as (previous: T) => T)(current.current.value) : action;
    const next = { scope, value };
    current.current = next;
    try {
      window.localStorage.setItem(`sopweave.draft.v1:${scope}`, JSON.stringify(value));
      setError('');
    } catch { setError('浏览器未能保存输入，请保留页面并检查存储空间。'); }
    setState(next);
  }, [scope]);
  return [current.current.value, setValue, error];
}
