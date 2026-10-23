import type { ModelOption } from "./api";

export function chooseModelId(models: ModelOption[], preferred: string | null | undefined): string {
  const chatModels = models.filter((model) => model.type === "chat");
  const ref = String(preferred || "").trim();
  const preferredModel = chatModels.find((model) => model.id === ref)
    || chatModels.find((model) => model.name === ref);
  // Keep an explicit unavailable preference so refreshes cannot select a different provider.
  if (ref) return preferredModel?.id || ref;
  return chatModels.find((model) => model.is_default === 1)?.id
    || chatModels[0]?.id
    || "";
}

export function reconcileModelId(
  models: ModelOption[],
  current: string,
  preferred: string | null | undefined,
  scopeChanged: boolean,
): string {
  if (!scopeChanged && current) {
    return chooseModelId(models, current);
  }
  return chooseModelId(models, preferred);
}
