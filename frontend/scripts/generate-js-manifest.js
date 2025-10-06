#!/usr/bin/env node
/*
 Scans frontend/public/js/** for JS files and extracts function names and exports.
 Outputs:
  - public/js-manifest.json
  - public/js/js-manifest.js (window.__JS_MANIFEST__ = {...})

 This is a heuristic scanner; it doesn’t execute code.
*/
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PUBLIC = path.join(ROOT, 'public');
const JS_ROOT = path.join(PUBLIC, 'js');
const OUT_JSON = path.join(PUBLIC, 'js-manifest.json');
const OUT_JS = path.join(JS_ROOT, 'js-manifest.js');

/** Simple recursive glob for .js under JS_ROOT */
function listJsFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files = [];
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) files.push(...listJsFiles(full));
    else if (e.isFile() && e.name.endsWith('.js')) files.push(full);
  }
  return files;
}

/** Extract function-like identifiers with basic regex heuristics */
function extractFunctions(source) {
  const names = new Set();
  const patterns = [
    /function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(/g, // function foo(
    /const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\([^)]*\)\s*=>/g, // const foo = (...) =>
    /const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*async\s*\([^)]*\)\s*=>/g, // const foo = async (...) =>
    /export\s+function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(/g, // export function foo(
    /export\s+default\s*function\s*([A-Za-z_$][A-Za-z0-9_$]*)?\s*\(/g, // export default function foo(
    /([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*function\s*\(/g, // obj: { foo: function(
    /([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{/g, // obj method shorthand foo() {
  ];
  for (const re of patterns) {
    let m;
    while ((m = re.exec(source))) {
      const candidate = m[1];
      if (!candidate) continue;
      // filter out common keywords or likely false positives
      if (['if', 'for', 'while', 'switch', 'catch', 'try', 'function'].includes(candidate)) continue;
      names.add(candidate);
    }
  }
  return Array.from(names).sort();
}

/** Attempt to find exported names (CommonJS/ESM heuristics) */
function extractExports(source) {
  const exportsSet = new Set();
  const patterns = [
    /export\s+\{([^}]+)\}/g, // export { a, b as c }
    /export\s+default\s+([A-Za-z_$][A-Za-z0-9_$]*)/g, // export default Foo
    /module\.exports\s*=\s*\{([^}]+)\}/g, // module.exports = { a, b }
    /exports\.([A-Za-z_$][A-Za-z0-9_$]*)/g, // exports.foo =
  ];
  let m;
  for (const re of patterns) {
    while ((m = re.exec(source))) {
      if (re === patterns[0] || re === patterns[2]) {
        // group list
        const list = m[1].split(',').map(s => s.trim());
        for (const item of list) {
          const name = item.split(/\s+as\s+/i)[1] || item.split(/\s+as\s+/i)[0];
          if (name) exportsSet.add(name.trim());
        }
      } else {
        const name = m[1];
        if (name) exportsSet.add(name);
      }
    }
  }
  return Array.from(exportsSet).sort();
}

function main() {
  if (!fs.existsSync(JS_ROOT)) {
    console.error('Cannot find js folder at', JS_ROOT);
    process.exit(1);
  }
  const files = listJsFiles(JS_ROOT);
  const entries = files.map((abs) => {
    const relFromPublic = path.relative(PUBLIC, abs).replace(/\\/g, '/');
    const source = fs.readFileSync(abs, 'utf8');
    const functions = extractFunctions(source);
    const exported = extractExports(source);
    return { file: relFromPublic, functions, exported };
  });
  const manifest = {
    root: 'public/js',
    generatedAt: new Date().toISOString(),
    files: entries,
  };
  // Write JSON
  fs.writeFileSync(OUT_JSON, JSON.stringify(manifest, null, 2));
  // Write JS for in-browser consumption
  const js = `// Auto-generated; do not edit\nwindow.__JS_MANIFEST__ = ${JSON.stringify(manifest)};`;
  fs.writeFileSync(OUT_JS, js);
  console.log(`Wrote ${OUT_JSON} and ${OUT_JS}`);
}

main();
