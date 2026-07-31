/**
 * Centralized PDF.js runtime configuration for React-PDF.
 *
 * Assets are copied from pdfjs-dist to /public by scripts/sync-pdf-assets.mjs:
 * - /pdf.worker.min.mjs
 * - /cmaps/
 * - /standard_fonts/
 */
import { pdfjs } from 'react-pdf';

const normalizeBaseUrl = (rawBaseUrl: string): string => {
    const trimmed = String(rawBaseUrl || '/').trim();
    if (!trimmed || trimmed === '/') {
        return '/';
    }
    return trimmed.endsWith('/') ? trimmed : `${trimmed}/`;
};

const pdfAssetsBaseUrl = normalizeBaseUrl(
    import.meta.env.VITE_PDF_ASSETS_BASE_URL || '/',
);

const workerPath = `${pdfAssetsBaseUrl}pdf.worker.min.mjs`;

pdfjs.GlobalWorkerOptions.workerSrc = workerPath;

export const pdfDocumentOptions = Object.freeze({
    cMapUrl: `${pdfAssetsBaseUrl}cmaps/`,
    cMapPacked: true,
    standardFontDataUrl: `${pdfAssetsBaseUrl}standard_fonts/`,
});

console.log(
    `[PDF Worker] worker=${workerPath}, cMap=${pdfDocumentOptions.cMapUrl}, fonts=${pdfDocumentOptions.standardFontDataUrl}, pdfjs v${pdfjs.version}`,
);

export { pdfjs };
