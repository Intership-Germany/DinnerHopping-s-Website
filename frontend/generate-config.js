// Script to generate public/config.js from .env for DinnerHopping frontend
const fs = require('fs');
const path = require('path');

const envPath = path.join(__dirname, '.env');
const outputPath = path.join(__dirname, 'public', 'config.js');

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
// Dynamically export all env variables as window.VAR_NAME_URL if they end with _BASE
for (const [key, value] of Object.entries(envVars)) {
  if (key.endsWith('_BASE')) {
    const windowVar = `window.${key.replace(/_BASE$/, '_BASE_URL')}`;
    js += `${windowVar} = "${value}";\n`;
  }
}
fs.writeFileSync(outputPath, js, 'utf8');
console.log('Generated public/config.js from .env with variables:', Object.keys(envVars).filter(k => k.endsWith('_BASE')).map(k => k.replace(/_BASE$/, '_BASE_URL')).join(', '));