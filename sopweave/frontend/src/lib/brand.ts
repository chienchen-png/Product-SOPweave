export const APP_NAME = "SOPweave";
export const APP_SLUG = "sopweave";

/** Read a SOPweave browser key, migrating the pre-rename value once. */
export function readMigratedStorage(key: string): string | null {
  try {
    const current = window.localStorage.getItem(key);
    if (current !== null) return current;
    const legacyKey = key.replace(/^sopweave\./, "finance.");
    if (legacyKey === key) return null;
    const legacy = window.localStorage.getItem(legacyKey);
    if (legacy === null) return null;
    window.localStorage.setItem(key, legacy);
    window.localStorage.removeItem(legacyKey);
    return legacy;
  } catch {
    return null;
  }
}
