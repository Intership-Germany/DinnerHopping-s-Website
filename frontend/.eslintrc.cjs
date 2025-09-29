module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  extends: ["eslint:recommended", "plugin:import/recommended", "prettier"],
  plugins: ["import"],
  parserOptions: { ecmaVersion: 2022, sourceType: "script" },
  rules: {
    "no-unused-vars": ["warn", { args: "none", ignoreRestSiblings: true }],
    "import/no-unresolved": "off" // no module system here (vanilla scripts)
  }
};
