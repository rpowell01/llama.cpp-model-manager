/**
 * Regression tests for selecting model tiles whose names need normalization.
 *
 * Run with: node tests/test_model_selection.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const modelManagerPath = path.join(
  __dirname,
  "..",
  "webapp",
  "static",
  "js",
  "model-manager.js",
);
const source = fs.readFileSync(modelManagerPath, "utf8");

function testNormalizedNamesSelectModelAndTile() {
  const model = "Qwen3.5-2B-Q4_K_M";
  const tile = {
    dataset: { model },
    classList: {
      classes: new Set(),
      add(value) {
        this.classes.add(value);
      },
      remove(value) {
        this.classes.delete(value);
      },
    },
  };
  const panel = { style: {}, dataset: {} };
  const contentArea = { classList: { add() {} } };
  const panelModelName = { textContent: "" };
  const elements = {
    "properties-panel": panel,
    "content-area": contentArea,
    "panel-model-name": panelModelName,
  };
  let renderedModel;

  const sandbox = {
    console,
    window: {},
    document: {
      getElementById: (id) => elements[id],
      querySelectorAll: (selector) => (selector === ".tile" ? [tile] : []),
    },
    currentModelRows: [{ model }],
    renderPanelProperties: (modelData, displayName) => {
      renderedModel = { modelData, displayName };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);

  sandbox.selectModel(model);

  assert.strictEqual(renderedModel.modelData.model, model);
  assert.strictEqual(renderedModel.displayName, model);
  assert.strictEqual(panelModelName.textContent, model);
  assert.ok(tile.classList.classes.has("selected"));
}

testNormalizedNamesSelectModelAndTile();
console.log("PASS normalized model selection");
