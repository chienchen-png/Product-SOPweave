export const CHAT_BOTTOM_THRESHOLD = 72;

type ScrollMetrics = {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
};

export function isNearScrollBottom(metrics: ScrollMetrics): boolean {
  return (
    metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <=
    CHAT_BOTTOM_THRESHOLD
  );
}
