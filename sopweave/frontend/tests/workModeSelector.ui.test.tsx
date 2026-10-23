import React from "react";
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import WorkModeSelector from "../src/components/WorkModeSelector";

afterEach(cleanup);

test("work mode copy keeps local command and SOP approval boundaries visible", () => {
  let mode: "manual" | "auto" = "manual";
  const view = render(
    <WorkModeSelector workMode={mode} onChange={(next) => { mode = next; }} />,
  );
  const current = screen.getByRole("button", { name: /人工审批/ });
  assert.match(current.getAttribute("title") || "", /本地命令需确认/);
  fireEvent.click(current);
  assert.match(screen.getByText(/写入、数据修改与本地命令需确认/).textContent || "", /确认/);
  fireEvent.click(screen.getByRole("button", { name: /全自动/ }));
  view.rerender(
    <WorkModeSelector workMode={mode} onChange={(next) => { mode = next; }} />,
  );
  const automatic = screen.getByRole("button", { name: /全自动/ });
  assert.match(automatic.getAttribute("title") || "", /SOP 强制审批仍需确认/);
  assert.doesNotMatch(automatic.getAttribute("title") || "", /无审批|沙箱已解除/);
});
