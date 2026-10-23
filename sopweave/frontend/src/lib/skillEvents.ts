import { createCatalogEvents } from "./catalogEvents";

const events = createCatalogEvents("skills");
export const notifySkillsChanged = () => events.notify();
export const subscribeSkillsChanged = (callback: () => void) => events.subscribe(callback);
