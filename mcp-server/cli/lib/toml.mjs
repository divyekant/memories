export const tomlEscape = (v) => String(v).replaceAll('\\', '\\\\').replaceAll('"', '\\"');

export function appendMarkedBlock(text, marker, body) {
  const start = `# BEGIN ${marker}`;
  if (text.split('\n').some((l) => l === start)) return text;
  return `${text}\n${start}\n${body}\n# END ${marker}\n`;
}

export function removeMarkedBlock(text, marker) {
  const start = `# BEGIN ${marker}`, end = `# END ${marker}`;
  if (!text.split('\n').includes(start)) return text;
  const out = [];
  let skip = false;
  for (const line of text.split('\n')) {
    if (line === start) { skip = true; continue; }
    if (line === end) { skip = false; continue; }
    if (!skip) out.push(line);
  }
  return out.join('\n');
}

const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export const hasTomlSection = (text, section) => new RegExp(`^\\s*\\[${escRe(section)}\\]\\s*$`, 'm').test(text);
export const hasTomlKey = (text, key) => new RegExp(`^\\s*${escRe(key)}\\s*=`, 'm').test(text);

export function ensureTomlStringKey(text, section, key, value) {
  if (!hasTomlSection(text, section)) return `${text}\n[${section}]\n${key} = "${value}"\n`;
  const lines = text.split('\n');
  const out = [];
  let inSection = false, done = false;
  const keyRe = new RegExp(`^\\s*${escRe(key)}\\s*=`);
  const sectionLine = `[${section}]`;
  for (const line of lines) {
    if (line.trim() === sectionLine) { inSection = true; out.push(line); continue; }
    if (inSection && /^\[[^\]]+\]\s*$/.test(line.trim())) {
      if (!done) { out.push(`${key} = "${value}"`); done = true; }
      inSection = false;
    }
    if (inSection && keyRe.test(line)) done = true;
    out.push(line);
  }
  if (inSection && !done) out.push(`${key} = "${value}"`);
  return out.join('\n');
}
