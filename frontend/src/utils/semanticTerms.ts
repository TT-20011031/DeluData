export function splitSemanticTerms(value: string): string[] {
    return value
        .split(/[,，、;；\n]+/u)
        .map(item => item.trim())
        .filter(Boolean)
}
