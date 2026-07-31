import React, { useCallback } from 'react';
import {
    ReactFlow,
    Controls,
    Background,
    useNodesState,
    useEdgesState,
    addEdge,
    type Edge,
    type Connection,
    MiniMap,
    type Node,
    Handle,
    Position
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { FileText } from 'lucide-react';

import { ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem } from '@/components/ui/context-menu';

type NodeActionHandler = (action: 'delete', nodeId: string) => void

// 自定义节点组件
const FileNode = ({ data, id }: { data: { label?: string; status?: string; onAction?: NodeActionHandler }, id: string }) => {
    // We need a way to trigger delete from here.
    // Ideally we pass callbacks via data. But for now simpler is fine.
    // Let's assume onNodeAction is passed via data
    const { onAction } = data

    const content = (
        <div className="px-4 py-2 shadow-md rounded-md bg-gray-800 border-2 border-gray-700 min-w-[150px]">
            <Handle type="target" position={Position.Top} className="w-16 !bg-blue-500" />
            <div className="flex items-center">
                <div className="rounded-full w-8 h-8 flex justify-center items-center bg-gray-700">
                    <FileText size={16} className="text-gray-300" />
                </div>
                <div className="ml-2">
                    <div className="text-sm font-bold text-gray-200">{data.label}</div>
                    <div className="text-xs text-gray-400">{data.status}</div>
                </div>
            </div>
            <Handle type="source" position={Position.Bottom} className="w-16 !bg-blue-500" />
        </div>
    )

    if (!onAction) {
        return content
    }

    return (
        <ContextMenu>
            <ContextMenuTrigger>
                {content}
            </ContextMenuTrigger>
            <ContextMenuContent className="bg-gray-800 border-gray-700 text-gray-200">
                <ContextMenuItem
                    className="focus:bg-gray-700 focus:text-white"
                    onClick={() => onAction && onAction('delete', id)}
                >
                    删除节点
                </ContextMenuItem>
            </ContextMenuContent>
        </ContextMenu>
    );
};

const nodeTypes = {
    fileNode: FileNode,
};

interface GraphEditorProps {
    initialNodes: Node[];
    initialEdges: Edge[];
    onConnect: (params: any) => void;
    onNodeAction?: NodeActionHandler;
    onNodeDragStop?: (nodeId: string, position: { x: number, y: number }) => void;
    onEdgeContextMenu?: (event: React.MouseEvent, edge: Edge) => void;
}

export const GraphEditor: React.FC<GraphEditorProps> = (props) => {
    const { initialNodes, initialEdges, onConnect, onNodeAction, onEdgeContextMenu } = props;
    const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

    const onConnectWrapper = useCallback(
        (params: Connection) => {
            setEdges((eds) => addEdge({ ...params, type: 'default', label: '关联' }, eds));
            onConnect(params);
        },
        [onConnect, setEdges],
    );

    // 自动布局 (Simple Grid Layout)
    React.useEffect(() => {
        if (initialNodes.length === 0) return

        // Check if all nodes are at 0,0 (newly loaded)
        // Or just force layout if we want to ensure visibility
        // Let's check if the first few nodes are overlapping at 0,0
        const isNotPositioned = initialNodes.every(n => n.position.x === 0 && n.position.y === 0)

        if (isNotPositioned) {
            const COLS = 4
            const X_SPACING = 250
            const Y_SPACING = 150

            const layoutedNodes = initialNodes.map((node, index) => {
                const col = index % COLS
                const row = Math.floor(index / COLS)
                return {
                    ...node,
                    position: {
                        x: col * X_SPACING,
                        y: row * Y_SPACING + (node.data.type === 'folder' ? 0 : 50) // Slight offset for files
                    },
                    // Inject handler here too to be safe
                    data: {
                        ...node.data,
                        onAction: onNodeAction
                    }
                }
            })
            setNodes(layoutedNodes)
        } else {
            // Just inject handlers
            const nodesWithHandler = initialNodes.map(node => ({
                ...node,
                data: {
                    ...node.data,
                    onAction: onNodeAction
                }
            }))
            setNodes(nodesWithHandler)
        }

        setEdges(initialEdges)
    }, [initialNodes, initialEdges, setNodes, setEdges, onNodeAction]);

    const onNodeDragStop = useCallback(
        (_: any, node: Node) => {
            if (props.onNodeDragStop) {
                props.onNodeDragStop(node.id, node.position);
            }
        },
        [props.onNodeDragStop],
    );

    return (
        <div style={{ width: '100%', height: '100%' }} className="bg-gray-900">
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnectWrapper}
                onNodeDragStop={onNodeDragStop}
                onEdgeContextMenu={onEdgeContextMenu}
                nodeTypes={nodeTypes}
                fitView
                defaultEdgeOptions={{ type: 'smoothstep', animated: true }}
            >
                <Background color="#333" gap={16} />
                <Controls className="bg-white text-black" />
                <MiniMap style={{ background: '#1a1a1a' }} nodeColor="#444" />
            </ReactFlow>
        </div>
    );
};
