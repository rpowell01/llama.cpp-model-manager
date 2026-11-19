/**
 * Regression tests for the he() HTML escaper in core.js.
 *
 * he() is used throughout the app to interpolate untrusted values into HTML,
 * frequently inside double-quoted attributes. It must therefore escape double
 * quotes; otherwise a value containing `"` can break out of the attribute and
 * inject markup/handlers.
 *
 * Run with: node tests/test_core_escaping.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const corePath = path.join(
  __dirname,
  "..",
  "webapp",
  "static",
  "js",
  "core.js",
);
const source = fs.readFileSync(corePath, "utf8");

// Load the browser script with the minimal globals it needs at load time.
const sandbox = {
  console,
  document: {
    addEventListener: () => {},
  },
  window: {},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const he = sandbox.he;

function testDoubleQuoteIsEscaped() {
  assert.strictEqual(he('"'), "&quot;");
}

function testAttributeBreakoutIsNeutralized() {
  const escaped = he('a" onmouseover="alert(1)');
  assert.ok(
    !escaped.includes('"'),
    `double quote was not escaped: ${escaped}`,
  );
  assert.strictEqual(escaped, "a&quot; onmouseover=&quot;alert(1)");
}

function testAllSpecialCharactersAreEscaped() {
  assert.strictEqual(he('a"b<c>&d'), "a&quot;b&lt;c&gt;&amp;d");
  assert.strictEqual(he("'"), "&#039;");
}

function testNonStringInputIsCoerced() {
  assert.strictEqual(he(42), "42");
}

const tests = [
  testDoubleQuoteIsEscaped,
  testAttributeBreakoutIsNeutralized,
  testAllSpecialCharactersAreEscaped,
  testNonStringInputIsCoerced,
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
  console.log(`\n${tests.length} core escaping tests passed`);
}
