"""检查图片存储目录"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings

settings = get_settings()
images_dir = Path(settings.app.data_dir) / "knowledge" / "images"

print(f"图片存储目录: {images_dir}")
print(f"目录存在: {images_dir.exists()}")

if images_dir.exists():
    subdirs = list(images_dir.iterdir())
    print(f"子目录数: {len(subdirs)}")
    for d in subdirs:
        if d.is_dir():
            files = list(d.glob("*"))
            print(f"  {d.name}: {len(files)} 个文件")
            for f in files[:3]:
                print(f"    - {f.name}")
else:
    print("目录不存在，图片可能未被提取")
