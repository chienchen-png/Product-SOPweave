import React from "react";
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
import FileDrawer from "../src/components/FileDrawer";
import { FeedbackProvider } from "../src/components/ui/Feedback";

afterEach(cleanup);
const entry = (path: string, type = "file") => ({
  path,
  name: path.split("/").pop(),
  type,
});
const response = (data: unknown) => new Response(JSON.stringify({ data }));
const drawer = (projectId = "p") => (
  <FeedbackProvider>
    <FileDrawer
      projectId={projectId}
      workDir="test"
      onClose={() => {}}
      onPick={() => {}}
      onRefer={() => {}}
      onImported={() => {}}
    />
  </FeedbackProvider>
);
const menu = async (name: string, action: string) => {
  fireEvent.contextMenu(
    await screen.findByRole("button", { name, exact: true }),
  );
  fireEvent.click(screen.getByRole("button", { name: action, exact: true }));
};

test("cut pastes with one move request and never copies then deletes", async () => {
  const mutations: { url: string; body: any }[] = [];
  globalThis.fetch = async (url, options) => {
    if (!options?.method)
      return response({
        entries: [entry("source.txt"), entry("target", "directory")],
      });
    mutations.push({
      url: String(url),
      body: JSON.parse(String(options.body)),
    });
    return response({});
  };
  render(drawer());
  await menu("source.txt", "剪切");
  await menu("target", "粘贴到此处");
  await waitFor(() => assert.equal(mutations.length > 0, true));
  assert.deepEqual(mutations, [
    {
      url: "/api/files/rename",
      body: {
        project_id: "p",
        path: "source.txt",
        new_path: "target/source.txt",
      },
    },
  ]);
});

test("nested deletion identifies the complete relative path and cancellation makes no request", async () => {
  const deletes: string[] = [];
  globalThis.fetch = async (url, options) => {
    if (options?.method === "DELETE") {
      deletes.push(JSON.parse(String(options.body)).path);
      return response({});
    }
    return response({
      entries: String(url).includes("path=target")
        ? [entry("target/same", "directory")]
        : [entry("target", "directory")],
    });
  };
  render(drawer());
  fireEvent.click(
    await screen.findByRole("button", { name: "target", exact: true }),
  );
  await menu("same", "删除");
  assert.ok(screen.getByText(/确定删除「target\/same」/));
  fireEvent.click(screen.getByRole("button", { name: "取消", exact: true }));
  assert.equal(deletes.length, 0);
  await menu("same", "删除");
  fireEvent.click(screen.getByRole("button", { name: "确认", exact: true }));
  await waitFor(() => assert.equal(deletes.length, 1));
  assert.equal(deletes[0], "target/same");
});

test("refresh reloads an expanded directory after its content changes externally", async () => {
  let changed = false;
  globalThis.fetch = async (url) =>
    response({
      entries: String(url).includes("path=folder")
        ? [entry(changed ? "folder/new.txt" : "folder/old.txt")]
        : [entry("folder", "directory")],
    });
  render(drawer());
  fireEvent.click(
    await screen.findByRole("button", { name: "folder", exact: true }),
  );
  await screen.findByRole("button", { name: "old.txt", exact: true });
  changed = true;
  fireEvent.click(screen.getByRole("button", { name: "刷新", exact: true }));
  await screen.findByRole("button", { name: "new.txt", exact: true });
  assert.equal(
    screen.queryByRole("button", { name: "old.txt", exact: true }) !== null,
    false,
  );
});

test("large directories load the first page and append more on demand", async () => {
  const urls: string[] = [];
  globalThis.fetch = async (url) => {
    const value = String(url);
    urls.push(value);
    if (!value.includes("path=folder")) {
      return response({
        entries: [entry("folder", "directory")],
        total: 1,
        next_cursor: "",
      });
    }
    const offset = new URL(value, "http://local").searchParams.get("offset") || "0";
    const start = Number(offset);
    const entries = Array.from(
      { length: 200 },
      (_, index) => entry(`folder/file-${start + index}.txt`),
    );
    return response({
      entries,
      total: 400,
      next_cursor: start === 0 ? "200" : "",
    });
  };

  render(drawer());
  fireEvent.click(
    await screen.findByRole("button", { name: "folder", exact: true }),
  );
  await screen.findByRole("button", { name: "file-199.txt", exact: true });
  assert.equal(
    screen.queryByRole("button", { name: "file-200.txt", exact: true }),
    null,
  );
  fireEvent.click(screen.getByRole("button", { name: /加载更多（200\/400）/ }));
  await screen.findByRole("button", { name: "file-399.txt", exact: true });
  assert.ok(urls.some((url) => url.includes("path=folder&limit=200&offset=200")));
});

test("a late root response cannot replace a newer refresh", async () => {
  let requests = 0;
  let finish!: (value: Response) => void;
  globalThis.fetch = async () =>
    ++requests === 1
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : response({ entries: [entry("new.txt")] });
  render(drawer());
  fireEvent.click(screen.getByRole("button", { name: "刷新", exact: true }));
  await screen.findByRole("button", { name: "new.txt", exact: true });
  await act(async () => finish(response({ entries: [entry("old.txt")] })));
  assert.equal(
    screen.queryByRole("button", { name: "old.txt", exact: true }) !== null,
    false,
  );
  assert.equal(
    screen.queryByRole("button", { name: "new.txt", exact: true }) !== null,
    true,
  );
});

test("changing project clears clipboard and ignores the old project response", async () => {
  let finish!: (value: Response) => void;
  let oldRequests = 0;
  globalThis.fetch = async (url) => {
    if (String(url).includes("project_id=p&")) {
      if (++oldRequests > 1)
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return response({ entries: [entry("source.txt")] });
    }
    return response({ entries: [entry("target", "directory")] });
  };
  const view = render(drawer());
  await menu("source.txt", "复制");
  fireEvent.click(screen.getByRole("button", { name: "刷新", exact: true }));
  view.rerender(drawer("q"));
  fireEvent.contextMenu(
    await screen.findByRole("button", { name: "target", exact: true }),
  );
  assert.equal(
    (screen.getByRole("button", { name: "粘贴到此处" }) as HTMLButtonElement)
      .disabled,
    true,
  );
  await act(async () =>
    finish(response({ entries: [entry("private-p.txt")] })),
  );
  assert.equal(
    screen.queryByRole("button", { name: "private-p.txt", exact: true }) !==
      null,
    false,
  );
});

test("new file uses the selected folder and keeps the name on a failed save for retry", async () => {
  const writes: any[] = [];
  globalThis.fetch = async (url, options) => {
    if (!options?.method)
      return response({
        entries: String(url).includes("path=target")
          ? []
          : [entry("target", "directory")],
      });
    writes.push(JSON.parse(String(options.body)));
    if (writes.length === 1)
      return new Response(JSON.stringify({ message: "同名文件已存在" }), {
        status: 409,
      });
    return response({});
  };
  render(drawer());
  fireEvent.click(
    await screen.findByRole("button", { name: "target", exact: true }),
  );
  fireEvent.click(screen.getByRole("button", { name: "新建", exact: true }));
  fireEvent.click(
    screen.getByRole("button", { name: "新建文件", exact: true }),
  );
  const input = screen.getByPlaceholderText("文件名（如 readme.md）");
  fireEvent.change(input, { target: { value: " retry.txt " } });
  fireEvent.click(screen.getByRole("button", { name: "确定", exact: true }));
  await screen.findByRole("alert");
  assert.equal(
    (screen.getByPlaceholderText("文件名（如 readme.md）") as HTMLInputElement)
      .value,
    " retry.txt ",
  );
  assert.equal(writes[0].path, "target/retry.txt");
  fireEvent.click(screen.getByRole("button", { name: "确定", exact: true }));
  await waitFor(() =>
    assert.equal(screen.queryByRole("dialog") !== null, false),
  );
  assert.equal(writes.length, 2);
});

test("new file rejects blank input and submits a pending request once", async () => {
  let writes = 0;
  let finish!: (response: Response) => void;
  globalThis.fetch = async (url, options) => {
    if (!options?.method) return response({ entries: [] });
    writes++;
    return new Promise<Response>((resolve) => {
      finish = resolve;
    });
  };
  render(drawer());
  await screen.findByText("目录为空（右键或顶部 + 新建）");
  fireEvent.click(screen.getByRole("button", { name: "新建", exact: true }));
  fireEvent.click(
    screen.getByRole("button", { name: "新建文件", exact: true }),
  );
  fireEvent.click(screen.getByRole("button", { name: "确定", exact: true }));
  assert.equal(writes, 0);
  fireEvent.change(screen.getByPlaceholderText("文件名（如 readme.md）"), {
    target: { value: "one.txt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确定", exact: true }));
  fireEvent.click(screen.getByRole("button", { name: /确定|创建中/ }));
  assert.equal(writes, 1);
  await act(async () => finish(response({})));
});

test("deleting the selected folder resets the new-file destination to its parent", async () => {
  let deleted = false;
  const creates: any[] = [];
  globalThis.fetch = async (url, options) => {
    if (!options?.method)
      return response({
        entries: deleted ? [] : [entry("removed", "directory")],
      });
    if (options.method === "DELETE") deleted = true;
    else creates.push(JSON.parse(String(options.body)));
    return response({});
  };
  render(drawer());
  fireEvent.click(
    await screen.findByRole("button", { name: "removed", exact: true }),
  );
  await menu("removed", "删除");
  fireEvent.click(screen.getByRole("button", { name: "确认", exact: true }));
  await waitFor(() =>
    assert.equal(
      screen.queryByRole("button", { name: "removed", exact: true }) !== null,
      false,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "新建", exact: true }));
  fireEvent.click(
    screen.getByRole("button", { name: "新建文件", exact: true }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "新建文件", exact: true }),
    { target: { value: "new.txt" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "确定", exact: true }));
  await waitFor(() => assert.equal(creates.length, 1));
  assert.equal(creates[0].path, "new.txt");
});

test("partial uploads identify each failed filename and reason and keep successful files", async () => {
  const uploaded: string[] = [];
  globalThis.fetch = async (url, options) => {
    if (!options?.method)
      return response({ entries: uploaded.map((name) => entry(name)) });
    const file = (options.body as FormData).get("file") as File;
    if (file.name === "exists.txt")
      return new Response(JSON.stringify({ message: "同名文件已存在" }), {
        status: 409,
      });
    uploaded.push(file.name);
    return response({});
  };
  const view = render(drawer());
  await screen.findByText("目录为空（右键或顶部 + 新建）");
  const input = view.container.querySelector('input[type="file"]')!;
  fireEvent.change(input, {
    target: {
      files: [new File(["one"], "good.txt"), new File(["two"], "exists.txt")],
    },
  });
  await screen.findByRole("button", { name: "good.txt", exact: true });
  await waitFor(() =>
    assert.equal(
      screen
        .getAllByRole("status")
        .some((element) =>
          element.textContent?.includes("exists.txt：同名文件已存在"),
        ),
      true,
    ),
  );
  assert.deepEqual(uploaded, ["good.txt"]);
});

test("folder upload preserves webkitRelativePath for nested files", async () => {
  const posted: { relative: string; path: string }[] = [];
  globalThis.fetch = async (_url, options) => {
    if (!options?.method) return response({ entries: [] });
    const form = options.body as FormData;
    posted.push({
      relative: String(form.get("relative_path")),
      path: String(form.get("path")),
    });
    return response({});
  };
  const view = render(drawer());
  await screen.findByText("目录为空（右键或顶部 + 新建）");
  const folderInput =
    view.container.querySelectorAll<HTMLInputElement>('input[type="file"]')[1];
  const root = new File(["root"], "root.txt");
  const deep = new File(["deep"], "deep.txt");
  Object.defineProperty(root, "webkitRelativePath", {
    value: "directory-upload/root.txt",
  });
  Object.defineProperty(deep, "webkitRelativePath", {
    value: "directory-upload/level/deep.txt",
  });
  fireEvent.change(folderInput, { target: { files: [root, deep] } });
  await waitFor(() => assert.equal(posted.length, 2));
  assert.deepEqual(posted, [
    { relative: "directory-upload/root.txt", path: "" },
    { relative: "directory-upload/level/deep.txt", path: "" },
  ]);
});
