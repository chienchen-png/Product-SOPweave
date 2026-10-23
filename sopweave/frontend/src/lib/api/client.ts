/** client API contracts and requests. */


/** api.ts — 统一后端 API 封装。
 *
 * 领域请求共用的 HTTP 客户端。
 * 所有响应遵循 { success, data } 包裹；本模块统一请求/错误处理。
 */

export const BASE = "";

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store", // 阶段 7.9.2：禁用 HTTP 缓存，操作后刷新必拿最新数据
    ...options,
  });
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok || json.success === false) {
    const err = new Error(json.error || json.message || `HTTP ${resp.status}`);
    (err as Error & { status?: number }).status = resp.status; // 供调用方识别 404 等
    throw err;
  }
  return json.data as T;
}
