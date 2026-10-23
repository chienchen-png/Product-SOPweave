import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { aiRegistryEvents } from "../lib/catalogEvents";
import {
  fetchOptions,
  listProviderTemplates,
  listProviders,
  type AgentOption,
  type AgentDefinitionError,
  type ModelOption,
  type Provider,
  type ProviderTemplate,
} from "../lib/api";

interface AiRegistryValue {
  models: ModelOption[];
  providers: Provider[];
  providerTemplates: ProviderTemplate[];
  agents: AgentOption[];
  agentDefinitionErrors: AgentDefinitionError[];
  loading: boolean;
  refreshing: boolean;
  error: string;
  refresh: () => Promise<void>;
}

const AiRegistryContext = createContext<AiRegistryValue | null>(null);

export function AiRegistryProvider({ children }: { children: ReactNode }) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerTemplates, setProviderTemplates] = useState<ProviderTemplate[]>([]);
  const [agents, setAgents] = useState<AgentOption[]>([]);
  const [agentDefinitionErrors, setAgentDefinitionErrors] = useState<AgentDefinitionError[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const initialized = useRef(false);
  const [error, setError] = useState("");
  const requestIdRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    setLoading(!initialized.current);
    setRefreshing(initialized.current);
    setError("");
    try {
      const [options, nextProviders, nextTemplates] = await Promise.all([
        fetchOptions(),
        listProviders(),
        listProviderTemplates(),
      ]);
      if (requestId !== requestIdRef.current) return;
      setModels(options.models);
      setAgents(options.agents);
      setAgentDefinitionErrors(options.agent_definition_errors || []);
      setProviders(nextProviders);
      setProviderTemplates(nextTemplates);
    } catch (cause) {
      if (requestId !== requestIdRef.current) return;
      const message = (cause as Error).message || "AI 配置加载失败";
      setError(message);
      throw cause;
    } finally {
      if (requestId === requestIdRef.current) {
        initialized.current = true;
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh().catch(() => undefined);
    return aiRegistryEvents.subscribe(() => { void refresh().catch(() => undefined); });
  }, [refresh]);

  const value = useMemo(
    () => ({ models, providers, providerTemplates, agents, agentDefinitionErrors, loading, refreshing, error, refresh }),
    [models, providers, providerTemplates, agents, agentDefinitionErrors, loading, refreshing, error, refresh],
  );

  return <AiRegistryContext.Provider value={value}>{children}</AiRegistryContext.Provider>;
}

export function useAiRegistry(): AiRegistryValue {
  const value = useContext(AiRegistryContext);
  if (!value) throw new Error("useAiRegistry 必须在 AiRegistryProvider 内使用");
  return value;
}
