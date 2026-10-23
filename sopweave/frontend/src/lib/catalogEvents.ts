/** Cross-tab invalidation only; consumers reload the authoritative catalog. */
export function createCatalogEvents(name: string) {
  const eventName = `finance:${name}-changed`;
  let channel: BroadcastChannel | null = null;
  let subscribers = 0;
  function connect() {
    if (!channel && typeof window !== "undefined" && typeof window.BroadcastChannel === "function") {
      channel = new window.BroadcastChannel(`finance-${name}`);
      channel.addEventListener("message", () => window.dispatchEvent(new window.Event(eventName)));
    }
    return channel;
  }
  return {
    notify(includeCurrentTab = true) {
      if (typeof window === "undefined") return;
      if (includeCurrentTab) window.dispatchEvent(new window.Event(eventName));
      connect()?.postMessage({ changed: true });
    },
    subscribe(callback: () => void) {
      if (typeof window === "undefined") return () => {};
      subscribers++;
      connect();
      const visible = () => { if (document.visibilityState === "visible") callback(); };
      window.addEventListener(eventName, callback);
      window.addEventListener("focus", callback);
      document.addEventListener("visibilitychange", visible);
      return () => {
        window.removeEventListener(eventName, callback);
        window.removeEventListener("focus", callback);
        document.removeEventListener("visibilitychange", visible);
        if (--subscribers === 0) { channel?.close(); channel = null; }
      };
    },
  };
}

export const aiRegistryEvents = createCatalogEvents("ai-registry");
