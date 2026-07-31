"""
PDF 文本提取质量测试脚本

对比多种 PDF 文本提取方式，生成 HTML 可视化报告。
支持的提取方式：
1. PyMuPDF get_text() - 当前系统使用的方式
2. PyMuPDF get_text("blocks") - 按块提取，保留位置信息
3. PyMuPDF get_text("dict") - 结构化提取，含字体/位置
4. PyMuPDF 页面渲染为图片 - 直接看原始页面

使用方法：
    python tests/test_pdf_extraction.py <pdf_path> [--pages 1,2,3] [--port 8877]
"""
import sys
import os
import json
import argparse
import http.server
import threading
import webbrowser
import time
from pathlib import Path
from typing import Optional

import base64

import fitz


WATERMARK_PATTERNS = [
    "标准分享网", "www.bzfxw.com", "免费下载",
    "仅供参考", "试用版", "watermark",
]


def is_scanned_page(page: fitz.Page) -> bool:
    """
    检测页面是否为扫描版（纯图片，无文本层）
    
    判断逻辑：
    1. 页面含有图片
    2. 提取的文本要么极少（<50字符），要么全是水印
    """
    text = page.get_text().strip()
    images = page.get_images()
    if not images:
        return False
    cleaned = text
    for pat in WATERMARK_PATTERNS:
        cleaned = cleaned.replace(pat, "")
    cleaned = cleaned.replace(" ", "").strip()
    if len(cleaned) < 50:
        return True
    return False


def extract_with_ocr(img_bytes: bytes) -> list[dict]:
    """
    使用 RapidOCR 从页面图片中提取文字（分块策略）
    
    大页面（>4000px 高）会自动分 3 段 OCR 再合并，
    避免 RapidOCR 内部缩放导致小字体丢失。
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image
        from io import BytesIO

        ocr = RapidOCR()
        img = Image.open(BytesIO(img_bytes))
        w, h = img.size

        if w > h * 1.2:
            img = img.rotate(90, expand=True)
            w, h = img.size

        TILE_THRESHOLD = 4000
        OVERLAP_RATIO = 0.05

        if h <= TILE_THRESHOLD:
            result, _ = ocr(np.array(img), det_db_box_thresh=0.3, det_db_unclip_ratio=1.6)
            return _parse_ocr_result(result)

        n_tiles = max(2, h // 3000)
        tile_h = h // n_tiles
        overlap = int(tile_h * OVERLAP_RATIO)

        all_lines = []
        seen_texts = set()

        for i in range(n_tiles):
            y_start = max(0, i * tile_h - overlap)
            y_end = min(h, (i + 1) * tile_h + overlap)
            tile = img.crop((0, y_start, w, y_end))

            result, _ = ocr(np.array(tile), det_db_box_thresh=0.3, det_db_unclip_ratio=1.6)
            if not result:
                continue

            for box, text, confidence in result:
                dedup_key = text.strip()
                if dedup_key in seen_texts:
                    continue
                seen_texts.add(dedup_key)

                adjusted_box = []
                for point in box:
                    adjusted_box.append([point[0], point[1] + y_start])

                all_lines.append({
                    "text": text,
                    "confidence": round(float(confidence), 3),
                    "bbox": adjusted_box,
                })

        all_lines.sort(key=lambda x: x["bbox"][0][1] if x["bbox"] else 0)
        return all_lines

    except Exception as e:
        return [{"text": f"OCR 错误: {e}", "confidence": 0, "bbox": []}]


def _parse_ocr_result(result) -> list[dict]:
    if not result:
        return []
    lines = []
    for item in result:
        box, text, confidence = item
        lines.append({
            "text": text,
            "confidence": round(float(confidence), 3),
            "bbox": box,
        })
    return lines


def extract_with_get_text(page: fitz.Page) -> str:
    return page.get_text()


def extract_with_blocks(page: fitz.Page) -> list[dict]:
    blocks = page.get_text("blocks")
    result = []
    for b in blocks:
        x0, y0, x1, y1, text, block_no, block_type = b
        result.append({
            "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            "text": text.strip(),
            "block_no": block_no,
            "type": "text" if block_type == 0 else "image",
        })
    return result


def extract_with_dict(page: fitz.Page) -> list[dict]:
    data = page.get_text("dict")
    result = []
    for block in data.get("blocks", []):
        if block["type"] == 0:
            for line in block.get("lines", []):
                spans_text = "".join(s["text"] for s in line.get("spans", []))
                if not spans_text.strip():
                    continue
                font_info = line["spans"][0] if line.get("spans") else {}
                result.append({
                    "text": spans_text,
                    "font": font_info.get("font", ""),
                    "size": round(font_info.get("size", 0), 1),
                    "bbox": [round(v, 1) for v in line.get("bbox", [])],
                    "flags": font_info.get("flags", 0),
                })
    return result


def render_page_image(page: fitz.Page, dpi: int = 150) -> bytes:
    pixmap = page.get_pixmap(dpi=dpi)
    return pixmap.tobytes("png")


def render_page_image_for_ocr(page: fitz.Page, dpi: int = 300) -> bytes:
    pixmap = page.get_pixmap(dpi=dpi)
    return pixmap.tobytes("png")


def process_pdf(
    pdf_path: str,
    page_numbers: Optional[list[int]] = None,
    ocr_dpi: int = 300,
) -> dict:
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    if page_numbers is None:
        page_numbers = list(range(1, min(total_pages + 1, 11)))

    selected_total = len(page_numbers)
    ocr_dpi = max(72, int(ocr_dpi))
    print(f"OCR 渲染 DPI: {ocr_dpi}")

    pages_data = []
    for idx, pn in enumerate(page_numbers, start=1):
        if pn < 1 or pn > total_pages:
            continue
        page = doc[pn - 1]
        print(f"[进度] {idx}/{selected_total} 页 (第 {pn} 页) 开始处理")

        plain_text = extract_with_get_text(page)
        blocks = extract_with_blocks(page)
        dict_lines = extract_with_dict(page)
        img_bytes = render_page_image(page)
        img_filename = f"page_{pn:04d}.png"
        img_b64 = base64.b64encode(img_bytes).decode()

        scanned = is_scanned_page(page)
        ocr_lines = []
        ocr_time_ms = 0
        if scanned:
            print(f"  第 {pn} 页: 扫描版，正在 OCR @{ocr_dpi}dpi...")
            t0 = time.time()
            ocr_img = render_page_image_for_ocr(page, dpi=ocr_dpi)
            ocr_lines = extract_with_ocr(ocr_img)
            ocr_time_ms = int((time.time() - t0) * 1000)
            print(f"  第 {pn} 页: OCR 识别 {len(ocr_lines)} 行，耗时 {ocr_time_ms}ms")
        else:
            print(f"  第 {pn} 页: 文本版，{len(plain_text.strip())} 字符")

        pages_data.append({
            "page_number": pn,
            "plain_text": plain_text,
            "blocks": blocks,
            "dict_lines": dict_lines,
            "image_b64": img_b64,
            "is_scanned": scanned,
            "ocr_lines": ocr_lines,
            "ocr_time_ms": ocr_time_ms,
        })

    doc.close()
    return {
        "file_name": Path(pdf_path).name,
        "total_pages": total_pages,
        "extracted_pages": [p["page_number"] for p in pages_data],
        "ocr_dpi": ocr_dpi,
        "pages": pages_data,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>PDF 提取质量对比 - $file_name</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", Roboto, "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; }
  .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 24px 32px; }
  .header h1 { font-size: 22px; margin-bottom: 6px; }
  .header .meta { font-size: 13px; opacity: 0.85; }
  .nav { display: flex; gap: 8px; padding: 12px 32px; background: #fff; border-bottom: 1px solid #e0e0e0; flex-wrap: wrap; position: sticky; top: 0; z-index: 100; }
  .nav button { padding: 6px 16px; border: 1px solid #d0d0d0; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; transition: all 0.15s; }
  .nav button:hover { background: #f5f5f5; }
  .nav button.active { background: #667eea; color: #fff; border-color: #667eea; }
  .page-section { margin: 20px 32px; }
  .page-title { font-size: 18px; font-weight: 600; margin-bottom: 12px; color: #444; padding-top: 8px; border-top: 2px solid #667eea; }
  .comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .panel { background: #fff; border-radius: 10px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); overflow: hidden; }
  .panel-header { padding: 10px 16px; font-weight: 600; font-size: 14px; border-bottom: 1px solid #eee; display: flex; align-items: center; gap: 8px; }
  .panel-header .badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500; }
  .badge-current { background: #fff3e0; color: #e65100; }
  .badge-blocks { background: #e3f2fd; color: #1565c0; }
  .badge-dict { background: #f3e5f5; color: #7b1fa2; }
  .badge-render { background: #e8f5e9; color: #2e7d32; }
  .panel-body { padding: 12px 16px; max-height: 700px; overflow-y: auto; }
  .panel-body pre { white-space: pre-wrap; word-break: break-all; font-size: 12.5px; line-height: 1.7; font-family: "Cascadia Code", "Fira Code", Consolas, monospace; }
  .panel-body img { width: 100%; height: auto; display: block; border: 1px solid #eee; }
  .block-item { margin-bottom: 8px; padding: 6px 8px; border-left: 3px solid #90caf9; background: #fafbfc; font-size: 12px; }
  .block-item .block-meta { color: #888; font-size: 11px; margin-bottom: 2px; }
  .dict-line { margin-bottom: 4px; padding: 4px 8px; border-left: 3px solid #ce93d8; background: #fafbfc; font-size: 12px; }
  .dict-line .line-meta { color: #888; font-size: 10px; }
  .tab-bar { display: flex; gap: 0; margin-bottom: 0; }
  .tab-bar button { padding: 10px 14px; border: none; background: #f5f5f5; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.15s; border-bottom: 2px solid transparent; }
  .badge-ocr { background: #fce4ec; color: #c62828; }
  .badge-scan { background: #fff3e0; color: #e65100; font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: 8px; }
  .ocr-line { margin-bottom: 4px; padding: 4px 8px; border-left: 3px solid #ef9a9a; background: #fafbfc; font-size: 12px; }
  .ocr-line .ocr-meta { color: #888; font-size: 10px; }
  .ocr-line .conf-high { color: #2e7d32; }
  .ocr-line .conf-mid { color: #f57f17; }
  .ocr-line .conf-low { color: #c62828; }
  .tab-bar button:hover { background: #eee; }
  .tab-bar button.active { background: #fff; border-bottom-color: #667eea; color: #667eea; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .stats { padding: 8px 16px; background: #f9f9f9; font-size: 12px; color: #666; border-top: 1px solid #eee; }
</style>
</head>
<body>
<div class="header">
  <h1>PDF 文本提取质量对比</h1>
  <div class="meta">$file_name | 共 $total_pages 页 | 已提取: $extracted_pages | OCR DPI: $ocr_dpi</div>
</div>

<div class="nav" id="pageNav">
  $page_buttons
</div>

$page_sections

<script>
document.querySelectorAll('.nav button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const target = document.getElementById(btn.dataset.target);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
});

document.querySelectorAll('.tab-bar button').forEach(btn => {
  btn.addEventListener('click', () => {
    const parent = btn.closest('.panel');
    parent.querySelectorAll('.tab-bar button').forEach(b => b.classList.remove('active'));
    parent.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    parent.querySelector('#' + btn.dataset.tab).classList.add('active');
  });
});
</script>
</body>
</html>"""


def render_template(template: str, **kwargs) -> str:
    from string import Template
    return Template(template).safe_substitute(**kwargs)


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_html(data: dict) -> str:
    page_buttons = ""
    for pn in data["extracted_pages"]:
        page_buttons += f'<button data-target="page-{pn}">第 {pn} 页</button>\n'

    page_sections = ""
    for page in data["pages"]:
        pn = page["page_number"]
        plain = escape_html(page["plain_text"])
        plain_chars = len(page["plain_text"].strip())
        plain_lines = page["plain_text"].strip().count("\n") + 1
        is_scanned = page.get("is_scanned", False)
        scan_badge = '<span class="badge-scan">扫描版 (纯图片)</span>' if is_scanned else ''

        blocks_html = ""
        for b in page["blocks"]:
            if b["type"] == "image":
                blocks_html += f'<div class="block-item"><div class="block-meta">#{b["block_no"]} [IMAGE] bbox={b["bbox"]}</div></div>\n'
            else:
                blocks_html += f'<div class="block-item"><div class="block-meta">#{b["block_no"]} bbox={b["bbox"]}</div>{escape_html(b["text"])}</div>\n'

        dict_html = ""
        for d in page["dict_lines"]:
            flags_str = []
            f = d["flags"]
            if f & 1: flags_str.append("superscript")
            if f & 2: flags_str.append("italic")
            if f & 4: flags_str.append("serif")
            if f & 8: flags_str.append("monospace")
            if f & 16: flags_str.append("bold")
            flag_label = ", ".join(flags_str) if flags_str else "regular"
            dict_html += (
                f'<div class="dict-line">'
                f'<div class="line-meta">{d["font"]} {d["size"]}pt [{flag_label}] bbox={d["bbox"]}</div>'
                f'{escape_html(d["text"])}</div>\n'
            )

        ocr_html = ""
        for o in page.get("ocr_lines", []):
            conf = float(o["confidence"])
            if conf >= 0.9:
                conf_cls = "conf-high"
            elif conf >= 0.7:
                conf_cls = "conf-mid"
            else:
                conf_cls = "conf-low"
            ocr_html += (
                f'<div class="ocr-line">'
                f'<div class="ocr-meta"><span class="{conf_cls}">{conf:.1%}</span></div>'
                f'{escape_html(o["text"])}</div>\n'
            )
        if not ocr_html and page.get("is_scanned"):
            ocr_html = '<div class="ocr-line">OCR 未识别到文字</div>'
        elif not ocr_html:
            ocr_html = '<div class="ocr-line">非扫描页，已跳过 OCR</div>'

        img_b64 = page["image_b64"]

        page_sections += f"""
<div class="page-section" id="page-{pn}">
  <div class="page-title">第 {pn} 页{scan_badge}</div>
  <div class="comparison">
    <div class="panel">
      <div class="panel-header"><span class="badge badge-render">页面渲染</span> 原始页面 @150dpi</div>
      <div class="panel-body"><img src="data:image/png;base64,{img_b64}" alt="Page {pn}"></div>
    </div>
    <div class="panel">
      <div class="tab-bar">
        <button class="active" data-tab="tab-plain-{pn}">get_text() <span class="badge badge-current">当前方式</span></button>
        <button data-tab="tab-blocks-{pn}">blocks <span class="badge badge-blocks">按块</span></button>
        <button data-tab="tab-dict-{pn}">dict <span class="badge badge-dict">结构化</span></button>
        <button data-tab="tab-ocr-{pn}">RapidOCR <span class="badge badge-ocr">OCR</span></button>
      </div>
      <div id="tab-plain-{pn}" class="tab-content active">
        <div class="stats">{plain_chars} 字符 | {plain_lines} 行</div>
        <div class="panel-body"><pre>{plain}</pre></div>
      </div>
      <div id="tab-blocks-{pn}" class="tab-content">
        <div class="stats">{len(page["blocks"])} 个文本块</div>
        <div class="panel-body">{blocks_html}</div>
      </div>
      <div id="tab-dict-{pn}" class="tab-content">
        <div class="stats">{len(page["dict_lines"])} 行（含字体信息）</div>
        <div class="panel-body">{dict_html}</div>
      </div>
      <div id="tab-ocr-{pn}" class="tab-content">
        <div class="stats">{len(page.get('ocr_lines', []))} 行 (RapidOCR) | 耗时 {page.get('ocr_time_ms', 0)}ms</div>
        <div class="panel-body">{ocr_html}</div>
      </div>
    </div>
  </div>
</div>
"""

    return render_template(
        HTML_TEMPLATE,
        file_name=escape_html(data["file_name"]),
        total_pages=str(data["total_pages"]),
        extracted_pages=", ".join(str(p) for p in data["extracted_pages"]),
        ocr_dpi=str(data.get("ocr_dpi", 300)),
        page_buttons=page_buttons,
        page_sections=page_sections,
    )


def main():
    parser = argparse.ArgumentParser(description="PDF 文本提取质量测试")
    parser.add_argument("pdf_path", help="PDF 文件路径")
    parser.add_argument("--pages", help="指定页码，逗号分隔，如 1,2,5。默认前 10 页", default=None)
    parser.add_argument("--ocr-dpi", type=int, default=300, help="OCR 渲染 DPI（默认 300）")
    parser.add_argument("--port", type=int, default=8877, help="HTTP 服务端口（默认 8877）")
    parser.add_argument("--no-serve", action="store_true", help="只生成 HTML 不启动服务")
    args = parser.parse_args()

    pdf_path = args.pdf_path
    if not os.path.exists(pdf_path):
        print(f"错误: 文件不存在 - {pdf_path}")
        sys.exit(1)

    page_numbers = None
    if args.pages:
        page_numbers = [int(p.strip()) for p in args.pages.split(",")]

    print(f"正在提取: {pdf_path}")
    data = process_pdf(pdf_path, page_numbers, ocr_dpi=args.ocr_dpi)
    print(f"已提取 {len(data['pages'])} 页")

    html = build_html(data)

    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / f"pdf_extraction_{Path(pdf_path).stem}_{args.ocr_dpi}dpi.html"
    output_file.write_text(html, encoding="utf-8")
    print(f"HTML 报告已保存: {output_file}")

    if not args.no_serve:
        os.chdir(str(output_dir))
        handler = http.server.SimpleHTTPRequestHandler
        server = http.server.HTTPServer(("127.0.0.1", args.port), handler)
        url = f"http://127.0.0.1:{args.port}/{output_file.name}"
        print(f"\n启动预览服务: {url}")
        print("按 Ctrl+C 停止\n")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止服务")
            server.shutdown()


if __name__ == "__main__":
    main()
