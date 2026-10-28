// Lints the inline <script> in landing/index.html, and this file (pre-commit `eslint` hook).
// CommonJS so require() finds the packages in pre-commit's own Node env (NODE_PATH).
const js = require("@eslint/js");
const html = require("eslint-plugin-html");
const globals = require("globals");

module.exports = [
  js.configs.recommended,
  {
    files: ["**/*.cjs"],
    languageOptions: { sourceType: "commonjs", globals: globals.node },
  },
  {
    files: ["**/*.html"],
    plugins: { html },
    languageOptions: { ecmaVersion: 2024, sourceType: "script", globals: globals.browser },
    rules: {
      complexity: ["error", 10],
      "max-depth": ["error", 3],
      "max-lines-per-function": ["error", { max: 50, skipBlankLines: true, skipComments: true }],
      "no-nested-ternary": "error",
      "no-return-assign": "error",
      "no-param-reassign": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "prefer-const": "error",
      "no-var": "error",
    },
  },
];
