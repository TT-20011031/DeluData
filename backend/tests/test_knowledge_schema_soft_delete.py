import sys

sys.path.insert(0, ".")

from app.api.knowledge.schemas import FileListItem, FileResponse
from app.models.common.enums import DeleteStatus


def test_file_response_contains_soft_delete_fields():
    payload = FileResponse(
        id="f1",
        name="demo",
        status="indexed",
        is_deleted=True,
        delete_status="delete_failed",
        delete_error="vector delete timeout",
        delete_op_id="op-1",
    ).model_dump()

    assert payload["is_deleted"] is True
    assert payload["delete_status"] == "delete_failed"
    assert payload["delete_error"] == "vector delete timeout"
    assert payload["delete_op_id"] == "op-1"


def test_file_list_item_soft_delete_defaults():
    item = FileListItem(id="f2", name="demo2", status="indexed")
    payload = item.model_dump()

    assert payload["is_deleted"] is False
    assert payload["delete_status"] == "active"
    assert payload["delete_error"] is None
    assert payload["delete_op_id"] is None


def test_pending_delete_status_fits_column_limit():
    # files.delete_status 为 VARCHAR(20)，防止再次写入超长状态值。
    assert len(DeleteStatus.PENDING_VECTOR_DELETE.value) <= 20
