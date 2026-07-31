import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const frontendRoot = path.resolve(__dirname, '..');
const publicDir = path.join(frontendRoot, 'public');
const pdfjsDistDir = path.dirname(require.resolve('pdfjs-dist/package.json'));

const syncTargets = [
    {
        from: path.join(pdfjsDistDir, 'build', 'pdf.worker.min.mjs'),
        to: path.join(publicDir, 'pdf.worker.min.mjs'),
        isDirectory: false,
    },
    {
        from: path.join(pdfjsDistDir, 'cmaps'),
        to: path.join(publicDir, 'cmaps'),
        isDirectory: true,
    },
    {
        from: path.join(pdfjsDistDir, 'standard_fonts'),
        to: path.join(publicDir, 'standard_fonts'),
        isDirectory: true,
    },
];

fs.mkdirSync(publicDir, { recursive: true });

for (const target of syncTargets) {
    const { from, to, isDirectory } = target;
    if (!fs.existsSync(from)) {
        throw new Error(`[sync-pdf-assets] Source not found: ${from}`);
    }

    if (isDirectory) {
        fs.cpSync(from, to, { recursive: true, force: true });
    } else {
        fs.mkdirSync(path.dirname(to), { recursive: true });
        fs.copyFileSync(from, to);
    }

    console.log(`[sync-pdf-assets] Synced ${from} -> ${to}`);
}

