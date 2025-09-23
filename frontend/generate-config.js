// Script to generate public/config.js from .env for DinnerHopping frontend
const fs = require('fs');
const path = require('path');

const envPath = path.join(__dirname, '.env');
const outputPath = path.join(__dirname, 'public', 'js', 'config.js');

function parseEnv(content) {
  const lines = content.split(/\r?\n/);
  const env = {};
  for (const line of lines) {
    if (!line || line.trim().startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    env[key] = value;
  }
  return env;
}

if (!fs.existsSync(envPath)) {
  console.error('.env file not found. Please copy .env.example to .env and edit as needed.');
  process.exit(1);
}

const envVars = parseEnv(fs.readFileSync(envPath, 'utf8'));
let js = '';
// Dynamically export all env variables
// 1) Keys ending with _BASE -> window.<KEY>_URL
// 2) DEBUG_BANNER -> window.DEBUG_BANNER (boolean)
for (const [key, value] of Object.entries(envVars)) {
  if (key.endsWith('_BASE')) {
    const windowVar = `window.${key.replace(/_BASE$/, '_BASE_URL')}`;
    js += `${windowVar} = "${value}";\n`;
  } else if (key === 'DEBUG_BANNER') {
    // Accept true/false/1/0/yes/no (case-insensitive)
    const v = String(value).trim().toLowerCase();
    const truthy = ['1','true','yes','on'].includes(v);
    js += `window.DEBUG_BANNER = ${truthy};\n`;
  }
}
// Always ensure FRONTEND_BASE_URL defaults to the visitor's origin when not provided via .env
js += 'if (typeof window !== "undefined") { window.FRONTEND_BASE_URL = window.FRONTEND_BASE_URL || window.location.origin; }\n';
fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, js, 'utf8');
console.log('Generated public/js/config.js from .env with variables:', Object.keys(envVars).filter(k => k.endsWith('_BASE')).map(k => k.replace(/_BASE$/, '_BASE_URL')).join(', '));