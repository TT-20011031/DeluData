# Utility modules
from .dataframe_utils import (
    clean_dataframe_for_json, 
    export_dataframe_to_csv, 
    export_dataframe_to_excel
)
from .sandbox_cleanup import (
    start_sandbox_cleanup_service, 
    stop_sandbox_cleanup_service
)
from .task_manager import TaskManager

__all__ = [
    "clean_dataframe_for_json",
    "export_dataframe_to_csv",
    "export_dataframe_to_excel",
    "start_sandbox_cleanup_service",
    "stop_sandbox_cleanup_service",
    "TaskManager",
    "get_image_service",
    "ImageService",
    "ExtractedImage",
]


def __getattr__(name):
    if name in {"get_image_service", "ImageService", "ExtractedImage"}:
        from .image_service import ExtractedImage, ImageService, get_image_service

        exports = {
            "get_image_service": get_image_service,
            "ImageService": ImageService,
            "ExtractedImage": ExtractedImage,
        }
        return exports[name]
    raise AttributeError(name)
