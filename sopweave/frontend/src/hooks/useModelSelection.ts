import { useEffect, useRef, useState } from "react";
import type { ModelOption } from "../lib/api";
import { chooseModelId, reconcileModelId } from "../lib/modelSelection";

export function useModelSelection(
  models: ModelOption[],
  preferred: string | null | undefined,
  scopeKey: string,
) {
  const [modelId, setModelId] = useState(() => chooseModelId(models, preferred));
  const scopeRef = useRef(scopeKey);

  useEffect(() => {
    const scopeChanged = scopeRef.current !== scopeKey;
    scopeRef.current = scopeKey;
    setModelId((current) => reconcileModelId(models, current, preferred, scopeChanged));
  }, [models, preferred, scopeKey]);

  return [modelId, setModelId] as const;
}
