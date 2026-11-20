import { cp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { gunzipSync } from 'node:zlib';

const root = fileURLToPath(new URL('../', import.meta.url));
const destination = path.join(root, 'dist');
const required = ['index.html', 'styles.css', 'app.js', 'lib.mjs', 'raven.svg'];
const optional = ['assets', 'favicon.svg', 'manifest.webmanifest'];

// Validate the inputs before replacing the previous output.
for (const name of required) await stat(path.join(root, name));
let snapshotBytes;
try {
  snapshotBytes = gunzipSync(await readFile(path.join(root, 'data/releases.json.gz')));
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  snapshotBytes = await readFile(path.join(root, 'data/releases.json'));
}
const snapshot = JSON.parse(snapshotBytes.toString('utf8'));
if (snapshot.schemaVersion !== 1 || !Array.isArray(snapshot.products) || !Array.isArray(snapshot.sources)) {
  throw new Error('data/releases.json has an unsupported schema. Run the collector first.');
}

await rm(destination, { recursive: true, force: true });
await mkdir(destination, { recursive: true });
for (const name of required) {
  const output = path.join(destination, name);
  await mkdir(path.dirname(output), { recursive: true });
  await cp(path.join(root, name), output);
}
await mkdir(path.join(destination, 'data'), { recursive: true });
await writeFile(path.join(destination, 'data/releases.json'), JSON.stringify(snapshot));
for (const name of optional) {
  const source = path.join(root, name);
  try {
    await stat(source);
  } catch (error) {
    if (error.code === 'ENOENT') continue;
    throw error;
  }
  await cp(source, path.join(destination, name), { recursive: true });
}
await writeFile(path.join(destination, '.nojekyll'), '');
console.log(`Built Raven Eye: ${snapshot.products.length} listings in dist/`);
