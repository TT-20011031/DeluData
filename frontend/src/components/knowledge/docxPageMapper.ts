import JSZip from 'jszip';

export interface DocxSourcePageMap {
    pageCount: number;
    snippets: Map<number, string>;
}

export function collectDocxPageElements(container: HTMLDivElement): HTMLElement[] {
    const wrappers = Array.from(
        container.querySelectorAll<HTMLElement>('.docx-wrapper, .docx-preview-wrapper-wrapper'),
    );
    const scope = wrappers.length > 0 ? wrappers : [container];

    for (const wrapper of scope) {
        const pages = Array.from(wrapper.children).filter(
            (child): child is HTMLElement =>
                child instanceof HTMLElement && child.tagName.toLowerCase() === 'section',
        );
        if (pages.length > 0) {
            return pages;
        }
    }

    return Array.from(container.querySelectorAll<HTMLElement>('section')).filter((section) => {
        const cls = section.className || '';
        return cls.includes('docx') || cls.includes('preview-wrapper');
    });
}

function normalizeText(raw: string): string {
    return String(raw || '')
        .toLowerCase()
        .replace(/[\u3000\s]+/g, '')
        .replace(/[^\p{L}\p{N}\u4e00-\u9fff]+/gu, '');
}

function buildFragments(snippet: string): string[] {
    const normalized = normalizeText(snippet);
    if (!normalized) return [];
    if (normalized.length <= 10) return [normalized];

    const fragments = new Set<string>();
    const windowSize = 8;
    const step = Math.max(4, Math.floor(normalized.length / 3));

    for (let start = 0; start < normalized.length && fragments.size < 4; start += step) {
        const fragment = normalized.slice(start, start + windowSize);
        if (fragment.length >= 6) {
            fragments.add(fragment);
        }
    }

    const tail = normalized.slice(-windowSize);
    if (tail.length >= 6) {
        fragments.add(tail);
    }

    return Array.from(fragments);
}

export function findDocxPageBySnippet(pages: HTMLElement[], snippet: string): number | null {
    const normalizedSnippet = normalizeText(snippet);
    if (!normalizedSnippet || normalizedSnippet.length < 4) {
        return null;
    }

    const normalizedPageTexts = pages.map((page) =>
        normalizeText(page.innerText || page.textContent || ''),
    );

    for (let idx = 0; idx < normalizedPageTexts.length; idx += 1) {
        if (normalizedPageTexts[idx].includes(normalizedSnippet)) {
            return idx + 1;
        }
    }

    const fragments = buildFragments(normalizedSnippet);
    if (fragments.length === 0) {
        return null;
    }

    let bestPage = -1;
    let bestScore = 0;
    for (let idx = 0; idx < normalizedPageTexts.length; idx += 1) {
        let score = 0;
        for (const fragment of fragments) {
            if (normalizedPageTexts[idx].includes(fragment)) {
                score += 1;
            }
        }
        if (score > bestScore) {
            bestScore = score;
            bestPage = idx + 1;
        }
    }

    const minScore = fragments.length >= 3 ? 2 : 1;
    return bestPage > 0 && bestScore >= minScore ? bestPage : null;
}

function decodeXmlEntities(value: string): string {
    return value
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&')
        .replace(/&quot;/g, '"')
        .replace(/&apos;/g, "'");
}

export async function extractDocxSourcePageMap(arrayBuffer: ArrayBuffer): Promise<DocxSourcePageMap> {
    try {
        const zip = await JSZip.loadAsync(arrayBuffer);
        const docEntry = zip.file('word/document.xml');
        if (!docEntry) {
            return { pageCount: 1, snippets: new Map() };
        }

        const xml = await docEntry.async('string');
        const paragraphRegex = /<w:p\b[\s\S]*?<\/w:p>/g;
        const textRegex = /<w:t\b[^>]*>([\s\S]*?)<\/w:t>/g;
        const pageBreakRegex = /<w:br\b[^>]*w:type=["']page["'][^>]*\/?>/g;
        const renderedBreakRegex = /<w:lastRenderedPageBreak\b[^>]*\/?>/g;

        const snippets = new Map<number, string>();
        let currentPage = 1;
        snippets.set(currentPage, '');

        let paraMatch: RegExpExecArray | null = null;
        while ((paraMatch = paragraphRegex.exec(xml)) !== null) {
            const paraXml = paraMatch[0];
            const breakCount =
                (paraXml.match(pageBreakRegex) || []).length +
                (paraXml.match(renderedBreakRegex) || []).length;

            if (breakCount > 0) {
                for (let i = 0; i < breakCount; i += 1) {
                    currentPage += 1;
                    if (!snippets.has(currentPage)) {
                        snippets.set(currentPage, '');
                    }
                }
            }

            const textPieces: string[] = [];
            let textMatch: RegExpExecArray | null = null;
            while ((textMatch = textRegex.exec(paraXml)) !== null) {
                const piece = decodeXmlEntities(textMatch[1] || '').trim();
                if (piece) {
                    textPieces.push(piece);
                }
            }

            if (textPieces.length > 0) {
                const merged = textPieces.join('').trim();
                if (merged && !snippets.get(currentPage)) {
                    snippets.set(currentPage, merged.slice(0, 80));
                }
            }
        }

        return { pageCount: Math.max(1, currentPage), snippets };
    } catch {
        return { pageCount: 1, snippets: new Map() };
    }
}
