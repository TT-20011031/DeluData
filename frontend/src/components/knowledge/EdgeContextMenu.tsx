/**
 * 边右键菜单组件
 */
import { Pencil, Trash2 } from 'lucide-react'

export interface EdgeContextMenuProps {
    position: { x: number; y: number }
    onEdit: () => void
    onDelete: () => void
}

export function EdgeContextMenu({ position, onEdit, onDelete }: EdgeContextMenuProps) {
    return (
        <div
            style={{ top: position.y, left: position.x }}
            className="fixed z-50 min-w-[120px] bg-gray-800 border border-gray-700 rounded-md shadow-lg py-1 text-gray-200"
            onClick={(e) => e.stopPropagation()}
        >
            <div
                className="px-3 py-2 text-sm hover:bg-gray-700 cursor-pointer flex items-center gap-2"
                onClick={onEdit}
            >
                <Pencil size={14} className="text-blue-400" /> 更改关系
            </div>
            <div
                className="px-3 py-2 text-sm hover:bg-gray-700 cursor-pointer flex items-center gap-2 text-red-400"
                onClick={onDelete}
            >
                <Trash2 size={14} /> 删除关系
            </div>
        </div>
    )
}
