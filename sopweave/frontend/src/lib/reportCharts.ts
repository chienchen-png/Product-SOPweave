import * as echarts from "echarts";
import { buildChartOption, resolveTemplate } from "../components/chart-templates";

type ChartPayload = {
  template?: unknown;
  title?: unknown;
  option?: unknown;
  data?: unknown;
  data_table?: unknown;
};

const STATIC_3D = new Set(["scatter3d", "bar3d", "waterfall3d"]);

function parsePayload(source: string): ChartPayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(source) as unknown;
  } catch (original) {
    // Vditor can append a second copy of a custom fenced block when building
    // export text. Accept the first complete JSON object only.
    const text = source.trim();
    if (!text.startsWith("{")) throw original;
    let depth = 0;
    let inString = false;
    let escaped = false;
    let end = -1;
    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') inString = false;
        continue;
      }
      if (char === '"') {
        inString = true;
      } else if (char === "{") {
        depth += 1;
      } else if (char === "}") {
        depth -= 1;
        if (depth === 0) {
          end = index + 1;
          break;
        }
      }
    }
    if (end < 0) throw original;
    parsed = JSON.parse(text.slice(0, end)) as unknown;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("图表数据必须是 JSON 对象");
  }
  return parsed as ChartPayload;
}

function optionFor(payload: ChartPayload): Record<string, unknown> {
  const template = String(payload.template || "");
  const resolved = resolveTemplate(template);
  if (!resolved) throw new Error(`未知图表模板：${template || "空"}`);
  const base = resolved.tpl.name;
  if (STATIC_3D.has(base)) {
    throw new Error("静态 3D 图不在 Markdown 导出中直出，请在图表预览中查看");
  }
  if (payload.option && typeof payload.option === "object") {
    return payload.option as Record<string, unknown>;
  }
  const data =
    payload.data && typeof payload.data === "object"
      ? (payload.data as Record<string, unknown>)
      : {};
  const built = buildChartOption(
    template,
    data as never,
    String(payload.title || ""),
  );
  if (!built) throw new Error(`无法构建图表模板：${template}`);
  return built as unknown as Record<string, unknown>;
}

function chartOptionForMarkdownFence(source: string): {
  template: string;
  title: string;
  option: Record<string, unknown>;
} {
  const payload = parsePayload(source);
  return {
    template: String(payload.template || ""),
    title: String(payload.title || "图表"),
    option: optionFor(payload),
  };
}

export function renderReportCharts(
  root: HTMLElement,
  instances: Set<echarts.ECharts>,
) {
  instances.forEach((chart) => chart.dispose());
  instances.clear();
  root.querySelectorAll<HTMLElement>("code.language-chart-json").forEach((code) => {
    if (code.closest(".report-chart-block")) return;
    const pre = code.closest("pre");
    if (!pre) return;
    try {
      const payload = parsePayload(code.textContent || "");
      const title = String(payload.title || "交互图表");
      const option = optionFor(payload);
      const wrapper = document.createElement("figure");
      wrapper.className = "report-chart-block";
      const host = document.createElement("div");
      host.className = "report-chart-canvas";
      host.style.height = "380px";
      host.setAttribute("role", "img");
      host.setAttribute("aria-label", title);
      const caption = document.createElement("figcaption");
      caption.textContent = title;
      wrapper.append(host, caption);
      pre.replaceWith(wrapper);
      const chart = echarts.init(host, undefined, {
        renderer: "canvas",
        devicePixelRatio: 2,
      });
      chart.setOption({ ...option, animation: false }, { notMerge: true });
      instances.add(chart);
      requestAnimationFrame(() => chart.resize());
    } catch (cause) {
      const error = document.createElement("p");
      error.className = "report-chart-error";
      error.setAttribute("role", "alert");
      error.textContent = `图表无法渲染：${(cause as Error).message}`;
      pre.replaceWith(error);
    }
  });
}

export async function materializeReportCharts(markdown: string): Promise<string> {
  const pattern = /```chart-json[ \t]*\r?\n([\s\S]*?)```/g;
  const matches = [...markdown.matchAll(pattern)];
  if (!matches.length) return markdown;
  const parts: string[] = [];
  let cursor = 0;
  for (const match of matches) {
    parts.push(markdown.slice(cursor, match.index));
    const { title, option } = chartOptionForMarkdownFence(match[1].trim());
    const payload = parsePayload(match[1].trim());
    const host = document.createElement("div");
    host.style.width = "1200px";
    host.style.height = "640px";
    host.style.position = "absolute";
    host.style.left = "-100000px";
    host.style.top = "0";
    document.body.appendChild(host);
    const chart = echarts.init(host, undefined, {
      renderer: "canvas",
      devicePixelRatio: 2,
    });
    try {
      chart.setOption({ ...option, animation: false }, { notMerge: true });
      await new Promise<void>((resolve) => setTimeout(resolve, 100));
      const image = chart.getDataURL({
        type: "png",
        pixelRatio: 2,
        backgroundColor: "#ffffff",
      });
      const alt = title.replace(/[\]\r\n]/g, " ");
      parts.push(`![${alt}](${image})`);
      const table = Array.isArray(payload.data_table) ? payload.data_table : [];
      if (table.length) {
        const columns = Object.keys(table[0] as Record<string, unknown>);
        parts.push(
          "\n\n" +
            [
              `| ${columns.join(" | ")} |`,
              `| ${columns.map(() => "---").join(" | ")} |`,
              ...table.map(
                (row) =>
                  `| ${columns
                    .map((column) =>
                      String((row as Record<string, unknown>)[column] ?? ""),
                    )
                    .join(" | ")} |`,
              ),
            ].join("\n"),
        );
      }
    } finally {
      chart.dispose();
      host.remove();
    }
    cursor = (match.index || 0) + match[0].length;
  }
  parts.push(markdown.slice(cursor));
  return parts.join("");
}
