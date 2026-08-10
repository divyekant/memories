import readline from 'node:readline/promises';

export async function ask(question, { def = '' } = {}) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    const a = (await rl.question(def ? `${question} [${def}]: ` : `${question}: `)).trim();
    return a || def;
  } finally { rl.close(); }
}

export async function askChoice(question, choices, { def } = {}) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    console.log(question);
    choices.forEach((c, i) => console.log(`  ${i + 1}. ${c.label}`));
    const a = (await rl.question(`  > `)).trim();
    const idx = Number.parseInt(a, 10) - 1;
    return choices[idx]?.value ?? def;
  } finally { rl.close(); }
}
