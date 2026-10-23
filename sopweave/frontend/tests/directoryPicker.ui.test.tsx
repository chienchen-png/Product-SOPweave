import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import DirectoryPicker from "../src/components/DirectoryPicker";
import Dialog from "../src/components/ui/Dialog";
import { useState } from "react";

const original = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = original;
});
const response = (data: unknown) =>
  new Response(JSON.stringify({ success: true, data }));
const root = "C:\\root",
  a = root + "\\A",
  b = a + "\\B";
test("the folder dialog stays outside its parent layout and Escape preserves the project form", async () => {
  transport([]);
  let closedParent = 0;
  function ProjectForm() {
    const [picking, setPicking] = useState(true);
    return (
      <Dialog
        title="新建项目"
        onClose={() => {
          closedParent++;
        }}
      >
        <input aria-label="项目名称" defaultValue="待创建项目" />
        {picking && (
          <DirectoryPicker
            onSelect={() => setPicking(false)}
            onCancel={() => setPicking(false)}
          />
        )}
      </Dialog>
    );
  }
  render(<ProjectForm />);
  const picker = await screen.findByRole("dialog", { name: "选择工作目录" });
  assert.equal(picker.parentElement?.closest('[role="dialog"]') === null, true);
  await screen.findByRole("button", { name: "A", exact: true });
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() =>
    assert.equal(
      screen.queryByRole("dialog", { name: "选择工作目录" }) === null,
      true,
    ),
  );
  assert.equal(closedParent, 0);
  assert.equal(
    (screen.getByLabelText("项目名称") as HTMLInputElement).value,
    "待创建项目",
  );
});
function transport(
  requests: string[],
  denied?: (path: string) => Promise<Response>,
) {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.includes("/drives"))
      return response({
        start: root,
        drives: [{ name: "不可访问", path: "C:\\denied", type: "drive" }],
        locations: [],
      });
    const path =
      new URL(url, "http://localhost").searchParams.get("path") || root;
    requests.push(path);
    if (path === "C:\\denied" && denied) return denied(path);
    return response({
      path,
      name: path.split("\\").at(-1),
      parent: path === root ? "C:\\" : path === a ? root : a,
      entries:
        path === root
          ? [{ name: "A", path: a, type: "directory" }]
          : path === a
            ? [{ name: "B", path: b, type: "directory" }]
            : [],
    });
  };
}
test("one physical double-click enters a folder with exactly one request", async () => {
  const requests: string[] = [];
  transport(requests);
  render(<DirectoryPicker onSelect={() => {}} onCancel={() => {}} />);
  const folder = await screen.findByRole("button", { name: "A", exact: true });
  fireEvent.click(folder, { detail: 1 });
  fireEvent.click(folder, { detail: 2 });
  fireEvent.doubleClick(folder);
  await screen.findByRole("button", { name: "B", exact: true });
  assert.equal(requests.filter((path) => path === a).length, 1);
});
test("breadcrumb navigation preserves chronological back and forward history", async () => {
  transport([]);
  render(<DirectoryPicker onSelect={() => {}} onCancel={() => {}} />);
  fireEvent.doubleClick(
    await screen.findByRole("button", { name: "A", exact: true }),
  );
  fireEvent.doubleClick(
    await screen.findByRole("button", { name: "B", exact: true }),
  );
  await screen.findByText("此文件夹为空");
  fireEvent.click(screen.getByRole("button", { name: "root", exact: true }));
  await screen.findByRole("button", { name: "A", exact: true });
  fireEvent.click(screen.getByRole("button", { name: "后退", exact: true }));
  await waitFor(() =>
    assert.equal(
      (
        screen.getByRole("button", {
          name: "前进",
          exact: true,
        }) as HTMLButtonElement
      ).disabled,
      false,
    ),
  );
  await screen.findByText("此文件夹为空");
  fireEvent.click(screen.getByRole("button", { name: "前进", exact: true }));
  await screen.findByRole("button", { name: "A", exact: true });
});
test("failed navigation does not change history or confirm a stale path while loading", async () => {
  let finish!: (response: Response) => void;
  transport(
    [],
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<DirectoryPicker onSelect={() => {}} onCancel={() => {}} />);
  await screen.findByRole("button", { name: "A", exact: true });
  fireEvent.click(
    screen.getByRole("button", { name: "不可访问", exact: true }),
  );
  assert.equal(
    (
      screen.getByRole("button", {
        name: "确定",
        exact: true,
      }) as HTMLButtonElement
    ).disabled,
    true,
  );
  await act(async () =>
    finish(
      new Response(JSON.stringify({ message: "无权访问目录" }), {
        status: 403,
      }),
    ),
  );
  await screen.findByText("无权访问目录");
  assert.equal(
    (
      screen.getByRole("button", {
        name: "后退",
        exact: true,
      }) as HTMLButtonElement
    ).disabled,
    true,
  );
  assert.ok(screen.getByRole("button", { name: "root", exact: true }));
});
