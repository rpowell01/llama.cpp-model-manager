/**
 * Regression tests for model property dropdown values.
 *
 * Context and YaRN settings are persisted in token counts. The properties panel
 * must keep those units when it reloads a model instead of interpreting the
 * persisted number as kilobytes and falling back to 4 KB.
 *
 * Run with: node tests/test_model_properties.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const uiControlsPath = path.join(
  __dirname,
  "..",
  "static",
  "js",
  "ui-controls.js",
);
const source = fs.readFileSync(uiControlsPath, "utf8");

// Load the browser script with the minimal globals it needs at load time.
const sandbox = {
  console,
  document: {},
  localStorage: {
    getItem: () => null,
    setItem: () => {},
  },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

function selectedValue(options, storedValue, fallbackValue) {
  return sandbox.getSelectedContextOptionValue(options, storedValue, fallbackValue);
}

function testStandardContextValueIsPreserved() {
  const options = sandbox.getContextSizeOptions(131072);
  assert.strictEqual(selectedValue(options, 131072, 4096), 131072);
  assert.ok(options.some((option) => option.value === 131072));
}

function testNonStandardContextValueIsPreserved() {
  const options = sandbox.getContextSizeOptions(128000);
  assert.strictEqual(selectedValue(options, 128000, 4096), 128000);
  assert.ok(options.some((option) => option.value === 128000));
}

function testLegacyContextKilobyteValueIsUpgraded() {
  const options = sandbox.getContextSizeOptions(128);
  assert.strictEqual(selectedValue(options, 128, 4096), 131072);
}

function testYarnAutoValueIsPreserved() {
  const options = sandbox.getYarnOrigCtxOptions(0);
  assert.strictEqual(selectedValue(options, 0, 0), 0);
  assert.strictEqual(options[0].value, 0);
}

function testYarnContextValueIsPreserved() {
  const options = sandbox.getYarnOrigCtxOptions(32768);
  assert.strictEqual(selectedValue(options, 32768, 0), 32768);
  assert.ok(options.some((option) => option.value === 32768));
}

function testLegacyYarnKilobyteValueIsUpgraded() {
  const options = sandbox.getYarnOrigCtxOptions(32);
  assert.strictEqual(selectedValue(options, 32, 0), 32768);
}

const tests = [
  testStandardContextValueIsPreserved,
  testNonStandardContextValueIsPreserved,
  testLegacyContextKilobyteValueIsUpgraded,
  testYarnAutoValueIsPreserved,
  testYarnContextValueIsPreserved,
  testLegacyYarnKilobyteValueIsUpgraded,
];

let failures = 0;
for (const test of tests) {
  try {
    test();
    console.log(`PASS ${test.name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${test.name}`);
    console.error(error.stack);
  }
}

if (failures > 0) {
  process.exitCode = 1;
} else {
  console.log(`\n${tests.length} model property tests passed`);
}
