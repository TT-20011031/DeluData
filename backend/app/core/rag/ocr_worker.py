"""OCR 子进程执行函数（单页 PDF OCR）。

设计约束：
1. 顶层尽量少导入重模块，兼容 ``ProcessPoolExecutor`` 的 ``spawn`` 模式。
2. worker 内完成“渲染 + OCR”，避免主进程传输大图字节。
"""

from __future__ import annotations

import inspect
import logging
import os
import site
import sys
import time
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_WORKER_CONFIG: Dict[str, Any] = {}
_WORKER_ENGINE: Any = None
_WORKER_ENGINE_FAILED = False
_FITZ_CACHE_CONFIGURED = False
_CUDA_DLL_CONFIGURED = False
_CUDA_DLL_DIR_HANDLES: list[Any] = []
_RAPIDOCR_KWARGS_PATCHED = False


class OCRCancelledError(Exception):
    """OCR 协作取消异常。"""


def _normalize_device(value: Any) -> str:
    device = str(value or "cpu").strip().lower()
    return "gpu" if device == "gpu" else "cpu"


def _configure_windows_cuda_dll_dirs_if_needed(device: str) -> None:
    global _CUDA_DLL_CONFIGURED, _CUDA_DLL_DIR_HANDLES

    if _CUDA_DLL_CONFIGURED or os.name != "nt" or device != "gpu":
        return

    candidate_dirs: list[str] = []
    seen: set[str] = set()
    site_packages: list[str] = []
    try:
        site_packages.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        user_site = site.getusersitepackages()
        if user_site:
            site_packages.append(user_site)
    except Exception:
        pass

    nvidia_subdirs = (
        "cublas",
        "cudnn",
        "cusolver",
        "cusparse",
        "cuda_runtime",
        "cuda_nvrtc",
        "nvjitlink",
        "curand",
        "cufft",
    )

    for sp in site_packages:
        nvidia_root = os.path.join(sp, "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for sub in nvidia_subdirs:
            bin_dir = os.path.join(nvidia_root, sub, "bin")
            norm = os.path.normcase(os.path.normpath(bin_dir))
            if os.path.isdir(bin_dir) and norm not in seen:
                seen.add(norm)
                candidate_dirs.append(bin_dir)

    if not candidate_dirs:
        _CUDA_DLL_CONFIGURED = True
        return

    path_items = os.environ.get("PATH", "").split(os.pathsep)
    normalized_path_items = {os.path.normcase(os.path.normpath(p)) for p in path_items if p}
    for dll_dir in candidate_dirs:
        norm = os.path.normcase(os.path.normpath(dll_dir))
        if norm not in normalized_path_items:
            os.environ["PATH"] = f"{dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            normalized_path_items.add(norm)
        try:
            handle = os.add_dll_directory(dll_dir)
            _CUDA_DLL_DIR_HANDLES.append(handle)
        except Exception:
            continue

    _CUDA_DLL_CONFIGURED = True
    logger.info("OCR GPU DLL 路径已注入: count=%s", len(candidate_dirs))


def _is_cancel_requested(cancel_event: Optional[Any]) -> bool:
    if cancel_event is None:
        return False
    try:
        return bool(cancel_event.is_set())
    except Exception:
        return False


def _cancelled_payload(page_number: int, started: float) -> Dict[str, Any]:
    return {
        "page_number": page_number,
        "text": "",
        "error": "cancelled",
        "cancelled": True,
        "elapsed_s": time.monotonic() - started,
        "render_s": 0.0,
        "ocr_s": 0.0,
        "postprocess_s": 0.0,
        "tile_count": 0,
    }


def ensure_ocr_runtime(config: Optional[Dict[str, Any]] = None) -> None:
    """
    确保 OCR 运行时配置已加载。

    该函数可在主进程/线程池复用 OCR 逻辑时调用，
    不会强制重置已加载的 OCR 引擎实例。
    """
    global _WORKER_CONFIG
    if config:
        _WORKER_CONFIG.update(dict(config))

    _WORKER_CONFIG["device"] = _normalize_device(_WORKER_CONFIG.get("device", "cpu"))
    _configure_windows_cuda_dll_dirs_if_needed(_WORKER_CONFIG["device"])

    intra = int(_WORKER_CONFIG.get("ort_intra_threads", 1) or 1)
    os.environ["OMP_NUM_THREADS"] = str(intra)
    os.environ["MKL_NUM_THREADS"] = str(intra)
    os.environ["OPENBLAS_NUM_THREADS"] = str(intra)
    os.environ["NUMEXPR_NUM_THREADS"] = str(intra)


def init_ocr_worker(config: Dict[str, Any]) -> None:
    """初始化 worker 运行参数，并按需预热 OCR 引擎。"""
    global _WORKER_CONFIG, _WORKER_ENGINE, _WORKER_ENGINE_FAILED
    _WORKER_CONFIG = dict(config or {})
    _WORKER_ENGINE = None
    _WORKER_ENGINE_FAILED = False

    # 限制底层数学/OpenMP 线程，防止“进程内再爆线程”。
    ensure_ocr_runtime(_WORKER_CONFIG)

    if bool(_WORKER_CONFIG.get("prewarm", False)):
        _get_ocr_engine()


def prewarm_ocr_worker() -> Dict[str, Any]:
    """预热单个 worker，提前加载 OCR 模型。"""
    engine = _get_ocr_engine()
    return {
        "pid": os.getpid(),
        "ready": engine is not None,
    }


def ocr_pdf_page(
    pdf_path: str,
    page_number: int,
    dpi: int,
    cancel_event: Optional[Any] = None,
) -> Dict[str, Any]:
    """在 worker 内渲染单页并执行 OCR，返回可归并的结果结构。"""
    started = time.monotonic()
    render_s = 0.0
    ocr_s = 0.0
    postprocess_s = 0.0
    tile_count = 0
    if _is_cancel_requested(cancel_event):
        return _cancelled_payload(page_number, started)
    try:
        import fitz

        _configure_fitz_cache_if_needed(fitz)

        with fitz.open(pdf_path) as doc:
            if _is_cancel_requested(cancel_event):
                return _cancelled_payload(page_number, started)
            total_pages = len(doc)
            if page_number < 1 or page_number > total_pages:
                return {
                    "page_number": page_number,
                    "text": "",
                    "error": f"invalid_page:{page_number}/{total_pages}",
                    "elapsed_s": time.monotonic() - started,
                }

            page = doc[page_number - 1]
            render_dpi = max(72, int(dpi))
            est_w_px, est_h_px = _estimate_page_pixels(page, render_dpi)
            est_pixels = est_w_px * est_h_px
            max_ocr_image_height_px = max(
                512,
                int(_WORKER_CONFIG.get("max_ocr_image_height_px", 2200) or 2200),
            )
            max_render_pixels = int(_WORKER_CONFIG.get("max_render_pixels", 18000000) or 18000000)
            source_tile_overlap_px = max(
                0, int(_WORKER_CONFIG.get("source_tile_overlap_px", 192) or 192)
            )

            # 仅当页面像素和高度都较小时才走整页路径，避免 A4@300dpi 直接吃大矩阵。
            if est_pixels <= max_render_pixels and est_h_px <= max_ocr_image_height_px:
                if _is_cancel_requested(cancel_event):
                    return _cancelled_payload(page_number, started)
                t_render = time.monotonic()
                pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
                png_bytes = pixmap.tobytes("png")
                render_s += time.monotonic() - t_render
                tile_count = 1
                perf: Dict[str, float] = {}
                text, err = _ocr_from_image_bytes(
                    png_bytes,
                    cancel_event=cancel_event,
                    perf=perf,
                )
                ocr_s += float(perf.get("ocr_s", 0.0) or 0.0)
                postprocess_s += float(perf.get("postprocess_s", 0.0) or 0.0)
                if err == "cancelled":
                    return _cancelled_payload(page_number, started)
            else:
                # 源头分片：同时受“像素预算”和“单次 OCR 输入高度”双重约束。
                tile_h_by_pixels = max(1, max_render_pixels // max(1, est_w_px))
                tile_h_px = max(256, min(est_h_px, tile_h_by_pixels, max_ocr_image_height_px))
                step_px = max(256, tile_h_px - source_tile_overlap_px)
                y0_px = 0
                first_err: Optional[str] = None
                tiled_texts: list[tuple[int, str]] = []

                logger.info(
                    "启用源头分片渲染: page=%s est=%sx%s dpi=%s max_pixels=%s max_ocr_h=%s tile_h=%s step=%s",
                    page_number,
                    est_w_px,
                    est_h_px,
                    render_dpi,
                    max_render_pixels,
                    max_ocr_image_height_px,
                    tile_h_px,
                    step_px,
                )

                while y0_px < est_h_px:
                    if _is_cancel_requested(cancel_event):
                        return _cancelled_payload(page_number, started)
                    y1_px = min(est_h_px, y0_px + tile_h_px)
                    clip_rect = _clip_from_pixel_range(page.rect, y0_px, y1_px, est_h_px)
                    t_render = time.monotonic()
                    pixmap = page.get_pixmap(dpi=render_dpi, alpha=False, clip=clip_rect)
                    png_bytes = pixmap.tobytes("png")
                    render_s += time.monotonic() - t_render
                    tile_count += 1
                    perf: Dict[str, float] = {}
                    tile_text, tile_err = _ocr_from_image_bytes(
                        png_bytes,
                        cancel_event=cancel_event,
                        perf=perf,
                    )
                    ocr_s += float(perf.get("ocr_s", 0.0) or 0.0)
                    postprocess_s += float(perf.get("postprocess_s", 0.0) or 0.0)
                    if tile_err == "cancelled":
                        return _cancelled_payload(page_number, started)
                    if tile_text.strip():
                        tiled_texts.append((y0_px, tile_text))
                    if tile_err and first_err is None:
                        first_err = tile_err
                    # 显式释放大对象，降低 worker 峰值和碎片累计。
                    del pixmap
                    del png_bytes
                    y0_px += step_px

                t_post = time.monotonic()
                text = _merge_tiled_texts(tiled_texts)
                postprocess_s += time.monotonic() - t_post
                err = first_err

        try:
            # 主动收缩 MuPDF 缓存，降低 worker 长时间运行时的 RSS 累积。
            fitz.TOOLS.store_shrink(100)
        except Exception as exc:
            logger.warning("MuPDF 缓存收缩失败: page=%s err=%s", page_number, exc)
        return {
            "page_number": page_number,
            "text": text,
            "error": err,
            "elapsed_s": time.monotonic() - started,
            "render_s": round(render_s, 4),
            "ocr_s": round(ocr_s, 4),
            "postprocess_s": round(postprocess_s, 4),
            "tile_count": tile_count,
        }
    except Exception as exc:  # pragma: no cover - defensive worker guard
        return {
            "page_number": page_number,
            "text": "",
            "error": str(exc),
            "elapsed_s": time.monotonic() - started,
            "render_s": round(render_s, 4),
            "ocr_s": round(ocr_s, 4),
            "postprocess_s": round(postprocess_s, 4),
            "tile_count": tile_count,
        }


def _estimate_page_pixels(page: Any, dpi: int) -> tuple[int, int]:
    """按 DPI 估算页面渲染后的像素尺寸。"""
    scale = float(dpi) / 72.0
    w_px = max(1, int(page.rect.width * scale))
    h_px = max(1, int(page.rect.height * scale))
    return w_px, h_px


def _clip_from_pixel_range(page_rect: Any, y0_px: int, y1_px: int, total_h_px: int) -> Any:
    """
    将像素坐标范围映射到 PDF 坐标系，生成源头分片 clip 区域。
    """
    import fitz

    if total_h_px <= 0:
        return page_rect

    ratio0 = max(0.0, min(1.0, float(y0_px) / float(total_h_px)))
    ratio1 = max(0.0, min(1.0, float(y1_px) / float(total_h_px)))
    y0 = page_rect.y0 + page_rect.height * ratio0
    y1 = page_rect.y0 + page_rect.height * ratio1
    return fitz.Rect(page_rect.x0, y0, page_rect.x1, y1)


def _merge_tiled_texts(tiled_texts: list[tuple[int, str]]) -> str:
    """
    合并分片 OCR 文本，并在重叠区域进行轻量去重。
    """
    if not tiled_texts:
        return ""

    merged_lines: list[str] = []
    recent_lines: list[str] = []
    for _, text in sorted(tiled_texts, key=lambda item: item[0]):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if recent_lines and line == recent_lines[-1]:
                continue
            if line in recent_lines[-24:]:
                continue
            merged_lines.append(line)
            recent_lines.append(line)
    return "\n".join(merged_lines)


def _configure_fitz_cache_if_needed(fitz_module: Any) -> None:
    global _FITZ_CACHE_CONFIGURED
    if _FITZ_CACHE_CONFIGURED:
        return
    max_mb = int(_WORKER_CONFIG.get("mupdf_store_max_mb", 256) or 256)
    if max_mb > 0:
        fitz_module.TOOLS.store_maxsize = max_mb * 1024 * 1024
    _FITZ_CACHE_CONFIGURED = True


def _patch_rapidocr_cuda_kwargs_if_needed() -> None:
    """
    兼容 rapidocr_onnxruntime 旧版本:
    cls_use_cuda / rec_use_cuda 可能未正确去前缀，导致 silently 回退 CPU。
    """
    global _RAPIDOCR_KWARGS_PATCHED
    if _RAPIDOCR_KWARGS_PATCHED:
        return

    try:
        import rapidocr_onnxruntime.utils as rapid_utils
    except Exception:
        _RAPIDOCR_KWARGS_PATCHED = True
        return

    updater_cls = getattr(rapid_utils, "UpdateParameters", None)
    root_dir = getattr(rapid_utils, "root_dir", None)
    if updater_cls is None or root_dir is None:
        _RAPIDOCR_KWARGS_PATCHED = True
        return

    if getattr(updater_cls, "_delu_cuda_patch_applied", False):
        _RAPIDOCR_KWARGS_PATCHED = True
        return

    def _normalize_prefixed_dict(raw: Dict[str, Any], prefix: str) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in raw.items():
            if key.startswith(prefix):
                normalized[key[len(prefix) :]] = value
            else:
                normalized[key] = value
        return normalized

    def _merge_model_cfg(config: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        if not updates:
            return config
        if not updates.get("model_path"):
            updates["model_path"] = str(root_dir / config["model_path"])
        config.update(updates)
        return config

    def _patched_update_cls_params(self: Any, config: Dict[str, Any], cls_dict: Dict[str, Any]) -> Dict[str, Any]:
        if not cls_dict:
            return config
        return _merge_model_cfg(config, _normalize_prefixed_dict(cls_dict, "cls_"))

    def _patched_update_rec_params(self: Any, config: Dict[str, Any], rec_dict: Dict[str, Any]) -> Dict[str, Any]:
        if not rec_dict:
            return config
        return _merge_model_cfg(config, _normalize_prefixed_dict(rec_dict, "rec_"))

    setattr(updater_cls, "update_cls_params", _patched_update_cls_params)
    setattr(updater_cls, "update_rec_params", _patched_update_rec_params)
    setattr(updater_cls, "_delu_cuda_patch_applied", True)
    _RAPIDOCR_KWARGS_PATCHED = True
    logger.info("RapidOCR CUDA 参数兼容补丁已启用（cls/rec 前缀修复）")


def _collect_ort_sessions(module_obj: Any) -> list[Any]:
    sessions: list[Any] = []
    visited: set[int] = set()
    stack: list[Any] = [module_obj]

    while stack:
        node = stack.pop()
        if node is None:
            continue
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        get_providers = getattr(node, "get_providers", None)
        if callable(get_providers):
            sessions.append(node)
            continue

        for attr in ("session", "infer", "ort_session"):
            child = getattr(node, attr, None)
            if child is not None:
                stack.append(child)

    return sessions


def _extract_engine_provider_map(engine: Any) -> Dict[str, list[str]]:
    provider_map: Dict[str, list[str]] = {}
    modules = {
        "det": getattr(engine, "text_det", None) or getattr(engine, "text_detector", None),
        "rec": getattr(engine, "text_rec", None) or getattr(engine, "text_recognizer", None),
        "cls": getattr(engine, "text_cls", None),
    }

    for name, module_obj in modules.items():
        if module_obj is None:
            continue
        providers: list[str] = []
        for session in _collect_ort_sessions(module_obj):
            try:
                current = list(session.get_providers() or [])
            except Exception:
                continue
            for provider in current:
                provider_s = str(provider)
                if provider_s not in providers:
                    providers.append(provider_s)
        provider_map[name] = providers

    return provider_map


def _extract_engine_providers(engine: Any) -> list[str]:
    providers: list[str] = []
    for module_providers in _extract_engine_provider_map(engine).values():
        for provider in module_providers:
            if provider not in providers:
                providers.append(provider)
    return providers


def _try_init_rapidocr(rapidocr_cls: Any, kwargs_candidates: list[Dict[str, Any]]) -> tuple[Any, Optional[Exception]]:
    last_exc: Optional[Exception] = None
    for kwargs in kwargs_candidates:
        try:
            return rapidocr_cls(**kwargs), None
        except Exception as exc:
            last_exc = exc
    return None, last_exc


def _try_init_rapidocr_with_params(
    rapidocr_cls: Any,
    params_candidates: list[Dict[str, Any]],
) -> tuple[Any, Optional[Exception]]:
    last_exc: Optional[Exception] = None
    for params in params_candidates:
        try:
            return rapidocr_cls(params=params), None
        except Exception as exc:
            last_exc = exc
    return None, last_exc


def _supports_rapidocr_params(rapidocr_cls: Any) -> bool:
    try:
        sig = inspect.signature(rapidocr_cls.__init__)
    except Exception:
        return False
    return "params" in sig.parameters


def _build_rapidocr_params_candidates(intra: int, inter: int, use_cuda: bool) -> list[Dict[str, Any]]:
    return [
        {
            "EngineConfig.onnxruntime.use_cuda": use_cuda,
            "EngineConfig.onnxruntime.intra_op_num_threads": intra,
            "EngineConfig.onnxruntime.inter_op_num_threads": inter,
        },
        {"EngineConfig.onnxruntime.use_cuda": use_cuda},
    ]


def _init_rapidocr_engine(
    rapidocr_cls: Any,
    intra: int,
    inter: int,
    device: str,
    gpu_fallback_to_cpu: bool,
) -> Any:
    supports_params = _supports_rapidocr_params(rapidocr_cls)
    if supports_params:
        init_mode = "params"
        init_func = _try_init_rapidocr_with_params
        cpu_candidates = _build_rapidocr_params_candidates(intra=intra, inter=inter, use_cuda=False)
        gpu_candidates = _build_rapidocr_params_candidates(intra=intra, inter=inter, use_cuda=True)
    else:
        init_mode = "kwargs"
        init_func = _try_init_rapidocr
        cpu_candidates = [
            {"intra_op_num_threads": intra, "inter_op_num_threads": inter},
            {"ort_intra_threads": intra, "ort_inter_threads": inter},
            {},
        ]
        gpu_base = {
            "det_use_cuda": True,
            "cls_use_cuda": True,
            "rec_use_cuda": True,
            "det_model_path": None,
            "cls_model_path": None,
            "rec_model_path": None,
        }
        gpu_candidates = [
            {**gpu_base, "intra_op_num_threads": intra, "inter_op_num_threads": inter},
            {**gpu_base, "ort_intra_threads": intra, "ort_inter_threads": inter},
            {**gpu_base, "use_cuda": True},
            {"use_cuda": True, "intra_op_num_threads": intra, "inter_op_num_threads": inter},
        ]

    logger.info(
        "OCR 初始化 RapidOCR: device=%s intra=%s inter=%s init_mode=%s exe=%s",
        device,
        intra,
        inter,
        init_mode,
        os.path.abspath(sys.executable),
    )

    if device != "gpu":
        engine, cpu_exc = init_func(rapidocr_cls, cpu_candidates)
        if engine is not None:
            return engine
        raise RuntimeError(f"CPU OCR 引擎初始化失败（mode={init_mode}）: {cpu_exc}")

    engine, gpu_exc = init_func(rapidocr_cls, gpu_candidates)
    if engine is not None:
        provider_map = _extract_engine_provider_map(engine)
        providers = _extract_engine_providers(engine)
        non_cuda_modules = [
            name for name, module_providers in provider_map.items() if "CUDAExecutionProvider" not in module_providers
        ]
        if providers and "CUDAExecutionProvider" in providers and not non_cuda_modules:
            return engine

        if providers:
            ort_providers: list[str] = []
            try:
                import onnxruntime as ort

                ort_providers = list(ort.get_available_providers() or [])
            except Exception:
                ort_providers = []
            if not gpu_fallback_to_cpu:
                raise RuntimeError(
                    "OCR 配置为 GPU，但未在全部 OCR 子模块启用 CUDA 且禁用了 CPU 回退"
                )
            logger.warning(
                "OCR GPU Provider 校验失败，自动回退 CPU: module_providers=%s providers=%s ort_providers=%s exe=%s init_mode=%s",
                provider_map,
                providers,
                ort_providers,
                os.path.abspath(sys.executable),
                init_mode,
            )
        elif not gpu_fallback_to_cpu:
            raise RuntimeError("OCR 配置为 GPU，但无法识别 Provider 且禁用了 CPU 回退")
    elif not gpu_fallback_to_cpu:
        raise RuntimeError(f"OCR GPU 初始化失败（mode={init_mode}）: {gpu_exc}")
    else:
        logger.warning("OCR GPU 初始化失败，自动回退 CPU: error=%s mode=%s", gpu_exc, init_mode)

    engine, cpu_exc = init_func(rapidocr_cls, cpu_candidates)
    if engine is not None:
        return engine
    raise RuntimeError(f"OCR CPU 回退初始化失败（mode={init_mode}）: {cpu_exc}")


def _get_ocr_engine() -> Any:
    global _WORKER_ENGINE, _WORKER_ENGINE_FAILED
    if _WORKER_ENGINE is not None:
        return _WORKER_ENGINE
    if _WORKER_ENGINE_FAILED:
        return None

    try:
        device = _normalize_device(_WORKER_CONFIG.get("device", "cpu"))
        if device == "gpu":
            try:
                import onnxruntime as ort

                # 在 Windows 下主动预加载 nvidia site-packages 中的 CUDA/cuDNN DLL，
                # 降低 ProcessPool 子进程因 PATH 不完整导致的 GPU Provider 失败概率。
                preload_dlls = getattr(ort, "preload_dlls", None)
                if callable(preload_dlls):
                    preload_dlls(cuda=True, cudnn=True, msvc=True, directory="")
            except Exception as preload_exc:
                logger.warning("OCR GPU 预加载 CUDA DLL 失败: %s", preload_exc)

        try:
            _patch_rapidocr_cuda_kwargs_if_needed()
            from rapidocr_onnxruntime import RapidOCR
        except Exception:
            from rapidocr import RapidOCR
        intra = int(_WORKER_CONFIG.get("ort_intra_threads", 1) or 1)
        inter = int(_WORKER_CONFIG.get("ort_inter_threads", 1) or 1)
        gpu_fallback_to_cpu = bool(_WORKER_CONFIG.get("gpu_fallback_to_cpu", True))
        logger.info(
            "OCR 初始化请求: device=%s intra=%s inter=%s gpu_fallback_to_cpu=%s exe=%s",
            device,
            intra,
            inter,
            gpu_fallback_to_cpu,
            os.path.abspath(sys.executable),
        )
        _WORKER_ENGINE = _init_rapidocr_engine(
            RapidOCR,
            intra=intra,
            inter=inter,
            device=device,
            gpu_fallback_to_cpu=gpu_fallback_to_cpu,
        )
        provider_map = _extract_engine_provider_map(_WORKER_ENGINE)
        providers = _extract_engine_providers(_WORKER_ENGINE)
        if providers:
            logger.info("OCR 引擎已初始化: device=%s providers=%s module_providers=%s", device, providers, provider_map)
        else:
            logger.info("OCR 引擎已初始化: device=%s providers=unknown module_providers=%s", device, provider_map)
        return _WORKER_ENGINE
    except Exception as exc:
        logger.warning("OCR 引擎初始化失败: %s", exc)
        _WORKER_ENGINE_FAILED = True
        return None


def ocr_image_bytes(
    png_bytes: bytes,
    config: Optional[Dict[str, Any]] = None,
    cancel_event: Optional[Any] = None,
) -> Tuple[str, Optional[str]]:
    """
    对外暴露的 PNG OCR 能力，供非进程池场景复用。
    """
    ensure_ocr_runtime(config)
    return _ocr_from_image_bytes(png_bytes, cancel_event=cancel_event)


def _ocr_from_image_bytes(
    png_bytes: bytes,
    cancel_event: Optional[Any] = None,
    perf: Optional[Dict[str, float]] = None,
) -> Tuple[str, Optional[str]]:
    started = time.monotonic()
    ocr_infer_s = 0.0

    def _flush_perf() -> None:
        if perf is None:
            return
        total_s = max(0.0, time.monotonic() - started)
        perf["ocr_s"] = round(ocr_infer_s, 4)
        perf["postprocess_s"] = round(max(0.0, total_s - ocr_infer_s), 4)
        perf["total_s"] = round(total_s, 4)

    if _is_cancel_requested(cancel_event):
        _flush_perf()
        return "", "cancelled"
    engine = _get_ocr_engine()
    if engine is None:
        _flush_perf()
        return "", "rapidocr_unavailable"

    try:
        import numpy as np
        from PIL import Image
    except Exception as exc:
        _flush_perf()
        return "", str(exc)

    try:
        image = Image.open(BytesIO(png_bytes))
        width, height = image.size
        if width > height * 1.2:
            image = image.rotate(90, expand=True)
            width, height = image.size

        tile_threshold = int(_WORKER_CONFIG.get("tile_threshold", 4000) or 4000)
        overlap_ratio = float(_WORKER_CONFIG.get("tile_overlap_ratio", 0.05) or 0.05)
        try:
            det_db_box_thresh = float(_WORKER_CONFIG.get("det_db_box_thresh", 0.45) or 0.45)
        except Exception:
            det_db_box_thresh = 0.45
        try:
            det_db_unclip_ratio = float(_WORKER_CONFIG.get("det_db_unclip_ratio", 1.3) or 1.3)
        except Exception:
            det_db_unclip_ratio = 1.3
        det_db_box_thresh = max(0.05, min(0.95, det_db_box_thresh))
        det_db_unclip_ratio = max(0.5, min(3.0, det_db_unclip_ratio))
        lines: list[tuple[float, str]] = []

        def _call_engine(np_img: Any) -> Any:
            try:
                return engine(
                    np_img,
                    det_db_box_thresh=det_db_box_thresh,
                    det_db_unclip_ratio=det_db_unclip_ratio,
                )
            except TypeError:
                try:
                    return engine(
                        np_img,
                        box_thresh=det_db_box_thresh,
                        unclip_ratio=det_db_unclip_ratio,
                    )
                except TypeError:
                    return engine(np_img)

        def _run_ocr(img: Any, y_offset: int = 0) -> None:
            nonlocal ocr_infer_s
            if _is_cancel_requested(cancel_event):
                raise OCRCancelledError()
            np_img = np.array(img)
            if _is_cancel_requested(cancel_event):
                raise OCRCancelledError()
            t_infer = time.monotonic()
            raw_output = _call_engine(np_img)
            ocr_infer_s += time.monotonic() - t_infer
            if _is_cancel_requested(cancel_event):
                raise OCRCancelledError()
            items = _normalize_ocr_items(raw_output)
            if not items:
                return
            for box, text in items:
                y = float(y_offset)
                try:
                    if box is not None:
                        if isinstance(box, np.ndarray):
                            if box.ndim >= 2 and box.shape[0] > 0 and box.shape[1] > 1:
                                y = float(box[0][1]) + y_offset
                        elif isinstance(box, (list, tuple)):
                            if box and isinstance(box[0], (list, tuple, np.ndarray)):
                                first = box[0]
                                if len(first) > 1:
                                    y = float(first[1]) + y_offset
                except Exception:
                    y = float(y_offset)
                lines.append((y, text))

        if height <= tile_threshold:
            _run_ocr(image, 0)
        else:
            n_tiles = max(2, height // 3000)
            tile_h = max(1, height // n_tiles)
            overlap = int(tile_h * overlap_ratio)
            for idx in range(n_tiles):
                if _is_cancel_requested(cancel_event):
                    raise OCRCancelledError()
                y_start = max(0, idx * tile_h - overlap)
                y_end = min(height, (idx + 1) * tile_h + overlap)
                tile = image.crop((0, y_start, width, y_end))
                _run_ocr(tile, y_start)

        if not lines:
            _flush_perf()
            return "", None

        lines.sort(key=lambda item: item[0])
        deduped: list[str] = []
        seen = set()
        for y, text in lines:
            key = (text, int(y // 5))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        _flush_perf()
        return "\n".join(deduped), None
    except OCRCancelledError:
        _flush_perf()
        return "", "cancelled"
    except Exception as exc:
        _flush_perf()
        return "", str(exc)


def _normalize_ocr_items(raw_output: Any) -> list[tuple[Any, str]]:
    candidate = raw_output
    if isinstance(raw_output, (list, tuple)) and len(raw_output) == 2:
        first = raw_output[0]
        if isinstance(first, (list, tuple)) or hasattr(first, "boxes"):
            candidate = first

    if hasattr(candidate, "boxes") and hasattr(candidate, "txts"):
        boxes_raw = getattr(candidate, "boxes", None)
        txts_raw = getattr(candidate, "txts", None)
        boxes = list(boxes_raw) if boxes_raw is not None else []
        txts = list(txts_raw) if txts_raw is not None else []
        normalized: list[tuple[Any, str]] = []
        for idx, text_item in enumerate(txts):
            text = str(text_item).strip() if text_item is not None else ""
            if not text:
                continue
            box = boxes[idx] if idx < len(boxes) else None
            normalized.append((box, text))
        return normalized

    normalized: list[tuple[Any, str]] = []
    if isinstance(candidate, (list, tuple)):
        for item in candidate:
            if isinstance(item, dict):
                box = item.get("box")
                if box is None:
                    box = item.get("bbox")
                if box is None:
                    box = item.get("points")
                text_val = item.get("text")
                if text_val is None:
                    text_val = item.get("txt")
                text = str(text_val).strip() if text_val is not None else ""
                if text:
                    normalized.append((box, text))
                continue
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                box = item[0]
                text_val = item[1]
                text = str(text_val).strip() if text_val is not None else ""
                if text:
                    normalized.append((box, text))
    return normalized
