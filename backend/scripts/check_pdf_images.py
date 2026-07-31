"""检查 PDF 图片的实际页码分布"""
import fitz
import sys
from pathlib import Path
from collections import defaultdict

def check_pdf(pdf_path: str):
    doc = fitz.open(pdf_path)
    print(f"PDF 页数: {len(doc)}")
    
    # 使用 get_image_info 获取每页实际渲染的图片
    print(f"\n=== 每页实际渲染的图片 (get_image_info) ===")
    xref_render_page = {}  # xref -> 实际渲染的页码
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        # get_image_info 返回该页实际渲染的图片信息
        image_infos = page.get_image_info(xrefs=True)
        
        rendered_xrefs = []
        for info in image_infos:
            xref = info.get('xref', 0)
            bbox = info.get('bbox', [])
            # 只统计有实际渲染位置的图片
            if xref and bbox:
                rendered_xrefs.append(xref)
                if xref not in xref_render_page:
                    xref_render_page[xref] = page_num + 1
        
        if rendered_xrefs:
            print(f"  Page {page_num + 1:2d}: {len(rendered_xrefs)} 个渲染图片, xrefs={rendered_xrefs[:3]}{'...' if len(rendered_xrefs) > 3 else ''}")
    
    print(f"\n=== xref 实际渲染页码 ===")
    for xref, page in sorted(xref_render_page.items(), key=lambda x: x[1]):
        print(f"  xref {xref:4d} -> Page {page}")
    
    print(f"\n=== 统计 ===")
    print(f"  实际渲染的图片数: {len(xref_render_page)}")
    
    doc.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # 默认检查最新上传的 PDF
        data_dir = Path("data/uploads")
        pdfs = list(data_dir.glob("**/*.pdf"))
        if pdfs:
            pdf_path = str(pdfs[-1])
            print(f"检查最新 PDF: {pdf_path}")
        else:
            print("用法: python check_pdf_images.py <pdf_path>")
            sys.exit(1)
    else:
        pdf_path = sys.argv[1]
    
    check_pdf(pdf_path)
