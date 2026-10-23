import { useCallback, useReducer, type Dispatch, type SetStateAction } from 'react';

export type WorkflowState = { step: number; reached: number };
type State = WorkflowState;
type Action = { type: 'select' | 'advance' | 'unlock'; step: number } | { type: 'reset' };

export function useWorkflow(total: number, initiallyUnlocked = 1, external?: {value:State;setValue:Dispatch<SetStateAction<State>>}) {
  const reduce = (current: State, action: Action): State => {
    if (action.type === 'reset') return { step: 1, reached: initiallyUnlocked };
    if (!Number.isInteger(action.step) || action.step < 1 || action.step > total) return current;
    if (action.type === 'select') return action.step<=current.reached ? { ...current, step: action.step } : current;
    return { step: action.type === 'advance' ? action.step : current.step, reached: Math.max(current.reached, action.step) };
  };
  const [local, localDispatch] = useReducer(reduce, { step: 1, reached: initiallyUnlocked });
  const state=external?.value || local;
  const setExternal=external?.setValue;
  const dispatch=useCallback((action:Action)=>{if(setExternal)setExternal(previous=>reduce(previous,action));else localDispatch(action);},[setExternal,total,initiallyUnlocked]);
  const select = useCallback((step: number) => dispatch({ type: 'select', step }), [dispatch]);
  const advance = useCallback((step: number) => dispatch({ type: 'advance', step }), [dispatch]);
  const unlock = useCallback((step: number) => dispatch({ type: 'unlock', step }), [dispatch]);
  const reset = useCallback(() => dispatch({ type: 'reset' }), [dispatch]);
  return { ...state, select, advance, unlock, reset };
}
