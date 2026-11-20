/**
 * Regression test for empty Hugging Face search results.
 *
 * Run with: node tests/test_hf_integration.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const hfIntegrationPath = path.join(
  __dirname,
  "..",
  "webapp",
  "static",
  "js",
  "hf-integration.js",
);
const source = fs.readFileSync(hfIntegrationPath, "utf8");

async function testNoResultsAreNotReportedAsAnError() {
  const model = "groot-19b-a3b-261023-q4-k-m";
  const statusElement = { textContent: "" };
  const errors = [];
  const sandbox = {
    console: {
      error: (...args) => errors.push(args),
    },
    document: {
      getElementById: (id) =>
        id === `hf-search-status-${model.replace(/[^a-zA-Z0-9]/g, "_")}`
          ? statusElement
          : null,
    },
    fetch: async () => ({
      ok: true,
      json: async () => ({ status: "ok", results: [] }),
    }),
    safeId: (value) => String(value).replace(/[^a-zA-Z0-9]/g, "_"),
    window: {},
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);

  await sandbox.autoSearchAndSelectRepo(model);

  assert.strictEqual(
    statusElement.textContent,
    "No matching GGUF repository found on Hugging Face.",
  );
  assert.deepStrictEqual(errors, []);
}

testNoResultsAreNotReportedAsAnError()
  .then(() => console.log("PASS empty Hugging Face results"))
  .catch((error) => {
    console.error(error.stack);
    process.exitCode = 1;
  });
