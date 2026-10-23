import React from "react";
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";
import { useHiddenProject } from "../src/hooks/useHiddenProject";
import { useWorkflow } from "../src/hooks/useWorkflow";
import { fireEvent } from "@testing-library/react";
import AppDataPanel from "../src/components/AppDataPanel";
import DataPanel, {IndexView} from "../src/components/DataPanel";
import ImportTasks from "../src/components/ImportTasks";
import { FeedbackProvider } from "../src/components/ui/Feedback";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

test('import review and index explain skipped sheets and hidden source content',async()=>{
  const info=[{name:'数据',state:'visible',hidden_rows:[3],hidden_row_count:1,hidden_columns:['C'],hidden_column_count:1},{name:'隐藏表',state:'hidden',hidden_rows:[],hidden_columns:[]}];
  const skipped=[{name:'说明',classification:'notes'},{name:'空白',classification:'empty'}];
  globalThis.fetch=async input=>new Response(JSON.stringify({data:String(input).includes('/tasks?')?{tasks:[{id:'visibility',kind:'import',status:'needs_anchor',path:'data.xlsx'}]}:{status:'needs_anchor',anchor:{sheets:[{name:'数据',header_row:0,columns:['编号','金额'],start_col:0,end_col:1},{name:'说明',classification:'notes',columns:[]},{name:'空白',classification:'empty',columns:[]}],previews:{数据:[['编号','金额'],['001',10]]},sheet_info:info,skipped_sheets:skipped}}}));
  const page=render(<ImportTasks projectId="p" onImported={()=>{}}/>);
  const summary=await screen.findByRole('region',{name:'工作簿导入说明'});
  assert.match(summary.textContent||'',/说明.*跳过.*说明表/);
  assert.match(summary.textContent||'',/空白.*跳过.*空表/);
  assert.match(summary.textContent||'',/隐藏行.*3/);
  assert.match(summary.textContent||'',/隐藏列.*C/);
  assert.match(summary.textContent||'',/隐藏表.*隐藏工作表/);
  assert.equal(!!screen.queryByLabelText('说明 表头行'),false);
  page.unmount();
  render(<IndexView file={{id:'f',meta:{workbook_manifest:{sheets:info},validation:{skipped_sheets:skipped}}} as any} sheets={[]} onBack={()=>{}}/>);
  assert.match(screen.getByRole('region',{name:'工作簿导入说明'}).textContent||'',/说明.*跳过/);
});

test('data index shows exact excluded-cell coordinates and total count',()=>{
  render(<IndexView file={{id:'f',meta:{validation:{sheets:{数据:{issues:[{code:'excluded_non_empty_cells',cells:['A4','B4'],count:90}]}}}}} as any} sheets={[{sheet_name:'数据',sheet_index:0,row_count:1,col_count:2,headers:[]} as any]} onBack={()=>{}}/>);
  assert.ok(screen.getByText(/A4.*B4/));
  assert.ok(screen.getByText(/90/));
});

test("import region edits survive defer and remount, stay scoped, and clear only after successful confirmation", async () => {
  let identifier = "draft-review";
  let reject = true;
  const submitted: any[] = [];
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    if (url.includes("/import/anchor")) {
      submitted.push(JSON.parse(String(options?.body)));
      if (reject)
        return new Response(JSON.stringify({ message: "源文件已变化" }), {
          status: 409,
        });
      return new Response(JSON.stringify({ success: true, data: {} }));
    }
    const data = url.includes("/tasks?")
      ? {
          tasks: [
            {
              id: identifier,
              kind: "import",
              status: "needs_anchor",
              path: "draft.xlsx",
            },
          ],
        }
      : {
          status: "needs_anchor",
          anchor: {
            sheets: [
              {
                name: "Sheet",
                header_row: 0,
                data_start_row: 1,
                data_end_row: 3,
                start_col: 0,
                end_col: 3,
                columns: ["A", "B", "C", "D"],
              },
            ],
            previews: {},
          },
        };
    return new Response(JSON.stringify({ success: true, data }));
  };
  let view = render(<ImportTasks projectId="p" onImported={() => {}} />);
  const values: Record<string, string> = {
    表头行: "2",
    数据起始行: "3",
    数据结束行: "4",
    起始列: "2",
    结束列: "4",
    排除列: "3",
  };
  await screen.findByLabelText("Sheet 排除列");
  for (const [label, value] of Object.entries(values))
    fireEvent.change(screen.getByLabelText(`Sheet ${label}`), {
      target: { value },
    });
  fireEvent.click(screen.getByRole("button", { name: "稍后确认" }));
  fireEvent.click(screen.getByRole("button", { name: "确认导入区域" }));
  await waitFor(() =>
    assert.equal(
      (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
      "3",
    ),
  );
  view.unmount();
  view = render(<ImportTasks projectId="p" onImported={() => {}} />);
  await waitFor(() => {
    for (const [label, value] of Object.entries(values))
      assert.equal(
        (screen.getByLabelText(`Sheet ${label}`) as HTMLInputElement).value,
        value,
      );
  });
  view.rerender(<ImportTasks projectId="other" onImported={() => {}} />);
  await waitFor(() =>
    assert.equal(
      (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
      "",
    ),
  );
  view.rerender(<ImportTasks projectId="p" onImported={() => {}} />);
  await waitFor(() =>
    assert.equal(
      (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
      "3",
    ),
  );
  view.unmount();
  identifier = "new-review";
  view = render(<ImportTasks projectId="p" onImported={() => {}} />);
  await waitFor(() =>
    assert.equal(
      (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
      "",
    ),
  );
  view.unmount();
  identifier = "draft-review";
  view = render(<ImportTasks projectId="p" onImported={() => {}} />);
  await screen.findByLabelText("Sheet 排除列");
  fireEvent.click(screen.getByRole("button", { name: "确认并继续导入" }));
  await screen.findByText(/提交失败/);
  assert.equal(
    (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
    "3",
  );
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "确认并继续导入" }));
  await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true));
  assert.deepEqual(submitted.at(-1).anchors.Sheet, {
    header_row: 1,
    data_start_row: 2,
    data_end_row: 3,
    start_col: 1,
    end_col: 3,
    excluded_columns: [2],
  });
  view.unmount();
  render(<ImportTasks projectId="p" onImported={() => {}} />);
  await waitFor(() =>
    assert.equal(
      (screen.getByLabelText("Sheet 排除列") as HTMLInputElement).value,
      "",
    ),
  );
});

function dataPanelTransport(writes: unknown[]) {
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    let data: unknown = {
      files: [
        {
          id: "left",
          source_path: "left/data.xlsx",
          file_name: "data.xlsx",
          status: "done",
          total_rows: 2,
          sheet_count: 1,
        },
      ],
    };
    if (url.includes("/tasks?")) data = { tasks: [] };
    else if (url.includes("/changes"))
      data = { summary: { total: 1, update: 1, insert: 0, delete: 0 } };
    else if (url.includes("/export/status"))
      data = { status: "done", progress: 100, message: "done" };
    else if (options?.method === "POST") {
      writes.push(JSON.parse(String(options.body)));
      data = { export_id: "export" };
    }
    return new Response(JSON.stringify({ data }));
  };
}

test("database references keep the selected source path instead of an ambiguous basename", async () => {
  dataPanelTransport([]);
  let reference = "";
  render(
    <FeedbackProvider>
      <DataPanel
        projectId="p"
        onClose={() => {}}
        onRefer={(value) => {
          reference = value;
        }}
      />
    </FeedbackProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "引用", exact: true }),
  );
  assert.equal(reference, "@db:left/data.xlsx");
});

test("writeback submits the selected source path instead of an ambiguous basename", async () => {
  const writes: unknown[] = [];
  dataPanelTransport(writes);
  render(
    <FeedbackProvider>
      <DataPanel projectId="p" onClose={() => {}} />
    </FeedbackProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "写回", exact: true }),
  );
  assert.ok(
    (await screen.findByRole("dialog")).textContent?.includes("left/data.xlsx"),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "确认", exact: true }),
  );
  await waitFor(() => assert.equal(writes.length, 1));
  assert.equal((writes[0] as any).file, "left/data.xlsx");
  await screen.findByText("写回完成：done");
});
function Panel() {
  const { project, error } = useHiddenProject("finmod", "model");
  return <div>{error || project?.id || "loading"}</div>;
}

test("retained audit history is not offered as another pending writeback", async () => {
  const writes: unknown[] = [];
  dataPanelTransport(writes);
  const previous = globalThis.fetch;
  globalThis.fetch = async (input, options) =>
    String(input).includes("/changes")
      ? new Response(
          JSON.stringify({
            data: {
              changes: [],
              summary: { total: 1, update: 1, insert: 0, delete: 0 },
              pending_summary: { total: 0, update: 0, insert: 0, delete: 0 },
            },
          }),
        )
      : previous(input, options);
  render(
    <FeedbackProvider>
      <DataPanel projectId="p" onClose={() => {}} />
    </FeedbackProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "写回", exact: true }),
  );
  await screen.findByText(/没有待同步变更/);
  assert.equal(screen.queryByRole("dialog"), null);
  assert.equal(writes.length, 0);
});

test("two simultaneous mounts create only one hidden workspace", async () => {
  let created = 0;
  globalThis.fetch = async (url, options) => {
    if (options?.method === "POST") {
      created++;
      const input = JSON.parse(String(options.body));
      assert.equal(input.name, "__app_finmod__");
      assert.equal(input.default_model, "model");
      return new Response(
        JSON.stringify({
          success: true,
          data: { id: "hidden", name: input.name, work_dir: "local" },
        }),
      );
    }
    return new Response(JSON.stringify({ success: true, data: [] }));
  };
  render(
    <>
      <Panel />
      <Panel />
    </>,
  );
  assert.equal((await screen.findAllByText("hidden")).length, 2);
  assert.equal(created, 1);
});

function Workflow() {
  const flow = useWorkflow(6, 1);
  return (
    <>
      <output>
        {flow.step}/{flow.reached}
      </output>
      <button onClick={() => flow.advance(3)}>advance</button>
      <button onClick={() => flow.select(2)}>back</button>
      <button onClick={() => flow.reset()}>reset</button>
    </>
  );
}
test("workflow advances atomically, keeps unlocked steps when returning, and resets", () => {
  render(<Workflow />);
  fireEvent.click(screen.getByText("advance"));
  assert.ok(screen.getByText("3/3"));
  fireEvent.click(screen.getByText("back"));
  assert.ok(screen.getByText("2/3"));
  fireEvent.click(screen.getByText("reset"));
  assert.ok(screen.getByText("1/1"));
});

test("reopening the data drawer restores persisted header review tasks", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    const data = url.includes("/tasks?")
      ? {
          tasks: [
            {
              id: "review",
              kind: "import",
              project_id: "p",
              path: "pending.xlsx",
              status: "needs_anchor",
              progress: 0,
            },
          ],
        }
      : url.includes("/import/status")
        ? {
            status: "needs_anchor",
            anchor: {
              sheets: [
                {
                  name: "Sheet",
                  header_row: 0,
                  confidence: 1,
                  columns: ["编号"],
                },
              ],
              previews: {},
            },
          }
        : { files: [] };
    return new Response(JSON.stringify({ success: true, data }));
  };
  render(<AppDataPanel projectId="p" onClose={() => {}} />);
  assert.ok(await screen.findByText("pending.xlsx"));
  assert.ok(await screen.findByText("Sheet", {}, { timeout: 2000 }));
});

test("ordinary projects restore review, preserve zero previews and submit one-based ranges as zero-based coordinates", async () => {
  const submitted: any[] = [];
  let done = false;
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    let data: unknown = { files: [] };
    if (url.includes("/tasks?"))
      data = {
        tasks: [
          {
            id: "review",
            kind: "import",
            project_id: "p",
            path: "pending.xlsx",
            status: done ? "done" : "needs_anchor",
            progress: 0,
          },
        ],
      };
    else if (url.includes("/import/status"))
      data = {
        status: "needs_anchor",
        anchor: {
          sheets: [
            {
              name: "Sheet",
              header_row: 2,
              data_start_row: 3,
              data_end_row: 4,
              start_col: 1,
              end_col: 3,
              columns: ["code", "amount"],
              confidence: 0.5,
            },
          ],
          previews: {
            Sheet: [
              ["code", "amount"],
              ["001", 0],
            ],
          },
        },
      };
    else if (url.includes("/import/anchor")) {
      submitted.push(JSON.parse(String(options?.body)));
      done = true;
      data = {};
    }
    return new Response(JSON.stringify({ success: true, data }));
  };
  render(
    <FeedbackProvider>
      <DataPanel projectId="p" onClose={() => {}} />
    </FeedbackProvider>,
  );
  await screen.findByRole("button", { name: "确认并继续导入" });
  assert.ok(screen.getByRole("cell", { name: "0", exact: true }));
  fireEvent.change(screen.getByLabelText("Sheet 排除列"), {
    target: { value: "3" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认并继续导入" }));
  await waitFor(() => assert.equal(submitted.length, 1));
  assert.deepEqual(submitted[0], {
    import_id: "review",
    anchors: {
      Sheet: {
        header_row: 2,
        data_start_row: 3,
        data_end_row: 4,
        start_col: 1,
        end_col: 3,
        excluded_columns: [2],
      },
    },
  });
  await waitFor(() =>
    assert.equal(screen.queryByRole("dialog") !== null, false),
  );
});

test("failed application uploads never import an old file with the same name", async () => {
  let imports = 0;
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    if (url.includes("/files/upload"))
      return new Response(JSON.stringify({ message: "同名文件已存在" }), {
        status: 409,
      });
    if (url.includes("/project-data/import") && options?.method === "POST") {
      imports++;
      return new Response(JSON.stringify({ data: { import_id: "wrong" } }));
    }
    return new Response(
      JSON.stringify({
        data: url.includes("/tasks?") ? { tasks: [] } : { files: [] },
      }),
    );
  };
  const view = render(<AppDataPanel projectId="p" onClose={() => {}} />);
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: { files: [new File(["new bytes"], "same.xlsx")] },
  });
  await screen.findByText(/上传失败|同名文件已存在/);
  await waitFor(() =>
    assert.equal(
      screen
        .getByRole("button", { name: "上传 Excel…" })
        .hasAttribute("disabled"),
      false,
    ),
  );
  assert.equal(imports, 0);
});

for (const [name, Component] of [
  ["project", DataPanel],
  ["application", AppDataPanel],
] as const) {
  test(`${name} data drawer ignores the previous project's delayed file response`, async () => {
    let finish!: (response: Response) => void;
    globalThis.fetch = async (input) => {
      const url = String(input);
      if (url.includes("/tasks?"))
        return new Response(JSON.stringify({ data: { tasks: [] } }));
      if (url.includes("project_id=p"))
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return new Response(
        JSON.stringify({
          data: {
            files: [
              {
                id: "new",
                file_name: "new.xlsx",
                status: "done",
                sheet_count: 1,
                total_rows: 2,
              },
            ],
          },
        }),
      );
    };
    const panel = (id: string) => (
      <FeedbackProvider>
        <Component projectId={id} onClose={() => {}} />
      </FeedbackProvider>
    );
    const view = render(panel("p"));
    view.rerender(panel("q"));
    await screen.findByText("new.xlsx");
    await act(async () =>
      finish(
        new Response(
          JSON.stringify({
            data: {
              files: [
                {
                  id: "old",
                  file_name: "private-old.xlsx",
                  status: "done",
                  sheet_count: 1,
                  total_rows: 2,
                },
              ],
            },
          }),
        ),
      ),
    );
    assert.equal(screen.queryByText("private-old.xlsx") !== null, false);
    assert.equal(screen.queryByText("new.xlsx") !== null, true);
  });
}

test("region validation rejects a negative end row and retains edits after a rejected submission", async () => {
  let posts = 0;
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    if (options?.method === "POST") {
      posts++;
      return new Response(JSON.stringify({ message: "任务状态冲突" }), {
        status: 409,
      });
    }
    const data = url.includes("/tasks?")
      ? {
          tasks: [
            {
              id: "review",
              kind: "import",
              project_id: "p",
              path: "pending.xlsx",
              status: "needs_anchor",
            },
          ],
        }
      : url.includes("/import/status")
        ? {
            status: "needs_anchor",
            anchor: {
              sheets: [
                {
                  name: "Sheet",
                  header_row: 0,
                  columns: ["code", "amount"],
                  start_col: 0,
                  end_col: 1,
                },
              ],
              previews: {},
            },
          }
        : { files: [] };
    return new Response(JSON.stringify({ data }));
  };
  render(
    <FeedbackProvider>
      <DataPanel projectId="p" onClose={() => {}} />
    </FeedbackProvider>,
  );
  await screen.findByLabelText("Sheet 数据结束行");
  fireEvent.change(screen.getByLabelText("Sheet 数据结束行"), {
    target: { value: "-1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认并继续导入" }));
  await screen.findByRole("alert");
  assert.equal(posts, 0);
  fireEvent.change(screen.getByLabelText("Sheet 数据结束行"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认并继续导入" }));
  await screen.findByText("提交失败：任务状态冲突");
  assert.equal(
    (screen.getByLabelText("Sheet 数据结束行") as HTMLInputElement).value,
    "5",
  );
  assert.equal(posts, 1);
});

test("formula review exposes an explicit cached-or-recalculate choice", async () => {
  const submitted: any[] = [];
  globalThis.fetch = async (input, options) => {
    const url = String(input);
    if (url.includes("/import/calculation")) {
      submitted.push(JSON.parse(String(options?.body)));
      return new Response(
        JSON.stringify({ success: true, data: { resumed: true } }),
      );
    }
    const data = url.includes("/tasks?")
      ? {
          tasks: [
            {
              id: "formula",
              kind: "import",
              project_id: "p",
              path: "stale.xlsx",
              status: "needs_review",
              error: "公式缓存可能过期",
              progress: 0,
            },
          ],
        }
      : url.includes("/import/status")
        ? {
            status: "needs_review",
            error: "公式缓存可能过期",
            anchor: {
              reason: "formula_calculation_required",
              calculation: {
                stale_cache: true,
                missing_after: 0,
                errors_after: 0,
              },
            },
          }
        : { files: [] };
    return new Response(JSON.stringify({ success: true, data }));
  };
  render(<ImportTasks projectId="p" onImported={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "处理公式" }));
  await screen.findByRole("button", { name: "重新执行受控重算" });
  fireEvent.click(
    screen.getByRole("button", { name: "使用现有缓存并标记未验证" }),
  );
  await waitFor(() => assert.equal(submitted.length, 1));
  assert.deepEqual(submitted[0], {
    import_id: "formula",
    strategy: "cached",
  });
});
