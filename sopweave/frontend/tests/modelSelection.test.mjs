import test from "node:test";
import assert from "node:assert/strict";
import { chooseModelId, reconcileModelId } from "../.test-build/modelSelection.js";

const models = [
  { id: "m1", name: "first", type: "chat", is_default: 0 },
  { id: "m2", name: "default", type: "chat", is_default: 1 },
  { id: "e1", name: "embedding", type: "embedding", is_default: 0 },
];

test("preferred id or legacy name resolves to a stable chat model id", () => {
  assert.equal(chooseModelId(models, "m1"), "m1");
  assert.equal(chooseModelId(models, "first"), "m1");
});

test("an invalid explicit preference remains unavailable until the user reselects", () => {
  assert.equal(chooseModelId(models, "deleted"), "deleted");
  assert.equal(chooseModelId([], "deleted"), "deleted");
  assert.equal(chooseModelId(models, ""), "m2");
});

test("registry refresh keeps a valid current selection", () => {
  assert.equal(reconcileModelId(models, "m1", "m2", false), "m1");
});

test("deleted selection is retained and hidden-project scope change restores its own preference", () => {
  assert.equal(reconcileModelId(models, "deleted", "m2", false), "deleted");
  assert.equal(reconcileModelId(models, "m1", "m2", true), "m2");
});
