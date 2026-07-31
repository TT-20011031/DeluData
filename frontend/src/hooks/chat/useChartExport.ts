/**
 * 图表导出 Hook
 * 
 * 使用 html2canvas + jsPDF 在前端实现 HTML 导出功能
 * 无需后端 Playwright，更轻量高效
 */

/**
 * 导出 HTML 元素为 PDF
 * 
 * @param element - 要导出的 HTML 元素
 * @param filename - 输出文件名（不含扩展名）
 */
export async function exportToPdf(element: HTMLElement, filename: string = 'chart'): Promise<void> {
    const [html2canvas, jsPDF] = await Promise.all([
        import('html2canvas').then(m => m.default),
        import('jspdf').then(m => m.default)
    ])

    // 创建 canvas
    const canvas = await html2canvas(element, {
        scale: 2,  // 2x 分辨率
        useCORS: true,
        logging: false,
        backgroundColor: '#ffffff'
    })

    // 计算 PDF 尺寸
    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF('p', 'mm', 'a4')

    const pdfWidth = pdf.internal.pageSize.getWidth()
    const pdfHeight = (canvas.height * pdfWidth) / canvas.width
    const pageHeight = pdf.internal.pageSize.getHeight()

    // [修复] 分页逻辑：position 表示图片在当前页的 Y 偏移
    let heightLeft = pdfHeight
    let position = 0

    // 第一页
    pdf.addImage(imgData, 'PNG', 0, position, pdfWidth, pdfHeight)
    heightLeft -= pageHeight

    // 后续页：图片向上偏移，每次减去一页高度
    while (heightLeft > 0) {
        position -= pageHeight  // 关键：向上移动一整页
        pdf.addPage()
        pdf.addImage(imgData, 'PNG', 0, position, pdfWidth, pdfHeight)
        heightLeft -= pageHeight
    }

    pdf.save(`${filename}.pdf`)
}

/**
 * 导出 HTML 元素为 PNG 图片
 * 
 * @param element - 要导出的 HTML 元素
 * @param filename - 输出文件名（不含扩展名）
 */
export async function exportToImage(element: HTMLElement, filename: string = 'chart'): Promise<void> {
    const html2canvas = (await import('html2canvas')).default

    const canvas = await html2canvas(element, {
        scale: 2,
        useCORS: true,
        logging: false,
        backgroundColor: '#ffffff'
    })

    // 创建下载链接
    const link = document.createElement('a')
    link.download = `${filename}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()
}

/**
 * React Hook: 图表导出功能
 */
export function useChartExport() {
    const exportPdf = async (element: HTMLElement | null, filename?: string) => {
        if (!element) {
            console.warn('[useChartExport] No element provided')
            return
        }
        await exportToPdf(element, filename)
    }

    const exportImage = async (element: HTMLElement | null, filename?: string) => {
        if (!element) {
            console.warn('[useChartExport] No element provided')
            return
        }
        await exportToImage(element, filename)
    }

    return {
        exportPdf,
        exportImage
    }
}
