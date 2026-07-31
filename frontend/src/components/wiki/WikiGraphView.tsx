import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from 'react'
import {
    ArrowUpRight,
    FileText,
    Loader2,
    Network,
    RefreshCw,
    RotateCcw,
    Search,
    ShieldCheck,
    SlidersHorizontal,
    X,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { wikiService } from '@/services/wikiService'
import type { WikiGraphNode, WikiGraphResponse } from '@/types/wiki'

import {
    buildHitGrid,
    clearFixedNodePositions,
    createGraphSimulation,
    drawGraph,
    fitViewport,
    fixNodeAtCurrentPosition,
    hitTestNode,
    isNodeFixed,
    moveNodeWithNeighbors,
    normalizeGraph,
    screenToWorld,
    unfixNode,
    type CanvasGraphData,
    type CanvasGraphNode,
    type GraphViewport,
    type HitGrid,
} from './graphCanvas'

interface WikiGraphViewProps {
    onOpenWikiPage?: (slug: string) => void
}

const LABEL_ZOOM_THRESHOLD = 0.74
const MIN_ZOOM = 0.14
const MAX_ZOOM = 3.4

const LINK_TYPE_LABEL: Record<string, string> = {
    source: '来源',
    mentions: '提及',
    related: '相关',
    supersedes: '取代',
    contradicts: '冲突',
}

const DOMAIN_LABELS: Record<string, string> = {
    general: '通用',
    source: '来源文件',
    policy: '制度政策',
    product: '产品手册',
    customer: '客户档案',
    term: '术语',
    decision: '决策记录',
}

function clamp(value: number, min: number, max: number) {
    return Math.max(min, Math.min(max, value))
}

function lerp(from: number, to: number, amount: number) {
    return from + (to - from) * amount
}

function pointerPosition(event: PointerEvent<HTMLCanvasElement> | WheelEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
    }
}

function matchesNode(node: WikiGraphNode, query: string) {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return false
    return `${node.title} ${node.slug} ${node.summary || ''} ${node.file_name || ''}`
        .toLowerCase()
        .includes(normalized)
}

function WikiGraphCanvas({
    graphData,
    query,
    searchIds,
    resetVersion,
    selectedId,
    onSelectedIdChange,
    onHoveredIdChange,
}: {
    graphData: CanvasGraphData
    query: string
    searchIds: Set<string>
    resetVersion: number
    selectedId: string | null
    onSelectedIdChange: (id: string | null) => void
    onHoveredIdChange: (id: string | null) => void
}) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null)
    const sizeRef = useRef({ width: 0, height: 0, dpr: 1 })
    const viewportRef = useRef<GraphViewport>({ x: 0, y: 0, zoom: 1 })
    const targetViewportRef = useRef<GraphViewport>({ x: 0, y: 0, zoom: 1 })
    const selectedIdRef = useRef<string | null>(selectedId)
    const hoveredIdRef = useRef<string | null>(null)
    const hitGridRef = useRef<HitGrid>(buildHitGrid(graphData.nodes))
    const simulationRef = useRef<ReturnType<typeof createGraphSimulation> | null>(null)
    const requestFrameRef = useRef<(() => void) | null>(null)
    const dragRef = useRef({
        pointerId: -1,
        dragging: false,
        mode: 'pan' as 'pan' | 'node',
        moved: false,
        lastX: 0,
        lastY: 0,
        lastWorldX: 0,
        lastWorldY: 0,
        grabOffsetX: 0,
        grabOffsetY: 0,
        downX: 0,
        downY: 0,
        downNodeId: null as string | null,
        dragNodeId: null as string | null,
        dragNodeWasFixed: false,
        neighborIds: [] as string[],
    })

    const draw = useCallback(() => {
        const canvas = canvasRef.current
        const ctx = canvas?.getContext('2d')
        if (!canvas || !ctx) return
        const { width, height, dpr } = sizeRef.current
        drawGraph(ctx, graphData, {
            width,
            height,
            dpr,
            viewport: viewportRef.current,
            hoveredId: hoveredIdRef.current,
            selectedId: selectedIdRef.current,
            searchIds,
            labelsVisible: viewportRef.current.zoom >= LABEL_ZOOM_THRESHOLD,
        })
    }, [graphData, searchIds])

    const applyFit = useCallback((animate = true) => {
        const { width, height } = sizeRef.current
        const next = fitViewport(graphData.nodes, width, height)
        targetViewportRef.current = next
        if (!animate) viewportRef.current = { ...next }
        requestFrameRef.current?.()
    }, [graphData.nodes])

    const focusNode = useCallback((node: CanvasGraphNode) => {
        const { width, height } = sizeRef.current
        const zoom = Math.max(targetViewportRef.current.zoom, 1.05)
        targetViewportRef.current = {
            zoom,
            x: width / 2 - (node.x || 0) * zoom,
            y: height / 2 - (node.y || 0) * zoom,
        }
    }, [])

    useEffect(() => {
        selectedIdRef.current = selectedId
        requestFrameRef.current?.()
    }, [selectedId])

    useEffect(() => {
        draw()
        requestFrameRef.current?.()
    }, [draw])

    useEffect(() => {
        const canvas = canvasRef.current
        if (!canvas) return

        const resize = () => {
            const rect = canvas.getBoundingClientRect()
            const dpr = window.devicePixelRatio || 1
            canvas.width = Math.max(1, Math.floor(rect.width * dpr))
            canvas.height = Math.max(1, Math.floor(rect.height * dpr))
            sizeRef.current = { width: rect.width, height: rect.height, dpr }
            applyFit(false)
        }

        resize()
        const observer = new ResizeObserver(resize)
        observer.observe(canvas)
        return () => observer.disconnect()
    }, [applyFit])

    useEffect(() => {
        const simulation = createGraphSimulation(graphData.nodes, graphData.links)
        simulation.stop()
        simulationRef.current = simulation
        let frame = 0
        let tick = 0
        let running = true

        const requestFrame = () => {
            if (!frame) {
                frame = window.requestAnimationFrame(animate)
            }
        }

        const animate = () => {
            if (!running) return
            frame = 0
            const viewport = viewportRef.current
            const target = targetViewportRef.current
            const viewportMoving = (
                Math.abs(viewport.x - target.x) > 0.2
                || Math.abs(viewport.y - target.y) > 0.2
                || Math.abs(viewport.zoom - target.zoom) > 0.002
            )
            viewport.x = lerp(viewport.x, target.x, 0.18)
            viewport.y = lerp(viewport.y, target.y, 0.18)
            viewport.zoom = lerp(viewport.zoom, target.zoom, 0.18)

            const simulationActive = simulation.alpha() > 0.018
            if (simulationActive) {
                simulation.tick(2)
                tick += 1
                if (tick % 4 === 0) {
                    hitGridRef.current = buildHitGrid(graphData.nodes)
                }
            }

            draw()
            if (simulationActive || viewportMoving) {
                requestFrame()
            }
        }

        requestFrameRef.current = requestFrame
        applyFit(false)
        requestFrame()
        return () => {
            running = false
            simulationRef.current = null
            requestFrameRef.current = null
            window.cancelAnimationFrame(frame)
            simulation.stop()
        }
    }, [applyFit, draw, graphData])

    useEffect(() => {
        clearFixedNodePositions(graphData.nodes)
        simulationRef.current?.alpha(0.75)
        applyFit(true)
        requestFrameRef.current?.()
    }, [applyFit, graphData.nodes, resetVersion])

    useEffect(() => {
        const trimmed = query.trim()
        if (!trimmed) return
        const match = graphData.nodes.find((node) => {
            return `${node.title} ${node.slug} ${node.summary || ''} ${node.fileName || ''}`
                .toLowerCase()
                .includes(trimmed.toLowerCase())
        })
        if (!match) return
        selectedIdRef.current = match.id
        onSelectedIdChange(match.id)
        focusNode(match)
        requestFrameRef.current?.()
    }, [focusNode, graphData.nodes, onSelectedIdChange, query])

    const pickNode = useCallback((x: number, y: number) => {
        const world = screenToWorld(x, y, viewportRef.current)
        return hitTestNode(hitGridRef.current, world.x, world.y, viewportRef.current.zoom)
    }, [])

    const handlePointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
        const pos = pointerPosition(event)
        const drag = dragRef.current
        if (drag.dragging) {
            if (Math.hypot(pos.x - drag.downX, pos.y - drag.downY) > 3) {
                drag.moved = true
            }
            if (drag.mode === 'node' && drag.dragNodeId) {
                const world = screenToWorld(pos.x, pos.y, viewportRef.current)
                const dx = world.x - drag.lastWorldX
                const dy = world.y - drag.lastWorldY
                const nextX = world.x + drag.grabOffsetX
                const nextY = world.y + drag.grabOffsetY
                if (moveNodeWithNeighbors(graphData, drag.dragNodeId, drag.neighborIds, nextX, nextY, dx, dy)) {
                    drag.lastWorldX = world.x
                    drag.lastWorldY = world.y
                    hitGridRef.current = buildHitGrid(graphData.nodes)
                    simulationRef.current?.alpha(0.28)
                }
            } else {
                const dx = pos.x - drag.lastX
                const dy = pos.y - drag.lastY
                viewportRef.current.x += dx
                viewportRef.current.y += dy
                targetViewportRef.current.x += dx
                targetViewportRef.current.y += dy
            }
            drag.lastX = pos.x
            drag.lastY = pos.y
            requestFrameRef.current?.()
            return
        }

        const hit = pickNode(pos.x, pos.y)
        const nextId = hit?.id || null
        if (hoveredIdRef.current !== nextId) {
            hoveredIdRef.current = nextId
            onHoveredIdChange(nextId)
            event.currentTarget.style.cursor = nextId ? 'pointer' : 'grab'
            requestFrameRef.current?.()
        }
    }

    const handlePointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
        const pos = pointerPosition(event)
        const hit = pickNode(pos.x, pos.y)
        const world = screenToWorld(pos.x, pos.y, viewportRef.current)
        dragRef.current = {
            pointerId: event.pointerId,
            dragging: true,
            mode: hit ? 'node' : 'pan',
            moved: false,
            lastX: pos.x,
            lastY: pos.y,
            lastWorldX: world.x,
            lastWorldY: world.y,
            grabOffsetX: hit ? (hit.x || 0) - world.x : 0,
            grabOffsetY: hit ? (hit.y || 0) - world.y : 0,
            downX: pos.x,
            downY: pos.y,
            downNodeId: hit?.id || null,
            dragNodeId: hit?.id || null,
            dragNodeWasFixed: isNodeFixed(hit),
            neighborIds: hit ? Array.from(graphData.adjacency.get(hit.id) || []) : [],
        }
        if (hit) {
            fixNodeAtCurrentPosition(hit)
            simulationRef.current?.alpha(0.28)
        }
        event.currentTarget.setPointerCapture(event.pointerId)
        event.currentTarget.style.cursor = 'grabbing'
        requestFrameRef.current?.()
    }

    const handlePointerUp = (event: PointerEvent<HTMLCanvasElement>) => {
        const drag = dragRef.current
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
        }
        drag.dragging = false
        event.currentTarget.style.cursor = hoveredIdRef.current ? 'pointer' : 'grab'
        hitGridRef.current = buildHitGrid(graphData.nodes)
        if (!drag.moved) {
            const node = drag.dragNodeId ? graphData.byId.get(drag.dragNodeId) : null
            if (node && !drag.dragNodeWasFixed) {
                unfixNode(node)
            }
            selectedIdRef.current = drag.downNodeId
            onSelectedIdChange(drag.downNodeId)
            requestFrameRef.current?.()
        } else if (drag.mode === 'node') {
            simulationRef.current?.alpha(0.18)
            requestFrameRef.current?.()
        }
    }

    const handlePointerLeave = () => {
        hoveredIdRef.current = null
        onHoveredIdChange(null)
        requestFrameRef.current?.()
    }

    const handleWheel = (event: WheelEvent<HTMLCanvasElement>) => {
        event.preventDefault()
        const pos = pointerPosition(event)
        const target = targetViewportRef.current
        const world = screenToWorld(pos.x, pos.y, target)
        const factor = Math.exp(-event.deltaY * 0.0012)
        const nextZoom = clamp(target.zoom * factor, MIN_ZOOM, MAX_ZOOM)
        targetViewportRef.current = {
            zoom: nextZoom,
            x: pos.x - world.x * nextZoom,
            y: pos.y - world.y * nextZoom,
        }
        requestFrameRef.current?.()
    }

    return (
        <canvas
            ref={canvasRef}
            className="absolute inset-0 h-full w-full cursor-grab touch-none"
            onPointerMove={handlePointerMove}
            onPointerDown={handlePointerDown}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            onPointerLeave={handlePointerLeave}
            onWheel={handleWheel}
        />
    )
}

export function WikiGraphView({ onOpenWikiPage }: WikiGraphViewProps) {
    const [graph, setGraph] = useState<WikiGraphResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [query, setQuery] = useState('')
    const [domainFilter, setDomainFilter] = useState('all')
    const [linkTypeFilter, setLinkTypeFilter] = useState('all')
    const [hideIsolated, setHideIsolated] = useState(false)
    const [showFiles, setShowFiles] = useState(true)
    const [filterOpen, setFilterOpen] = useState(false)
    const [selectedId, setSelectedId] = useState<string | null>(null)
    const [hoveredId, setHoveredId] = useState<string | null>(null)
    const [resetVersion, setResetVersion] = useState(0)

    const load = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            setGraph(await wikiService.getGraph())
        } catch (err) {
            setError(err instanceof Error ? err.message : '知识图谱加载失败')
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        load()
    }, [load])

    const filteredGraph = useMemo<WikiGraphResponse | null>(() => {
        if (!graph) return null
        const visibleNodeIds = new Set<string>()
        for (const node of graph.nodes) {
            if (!showFiles && (node.node_type || 'entity') === 'file') continue
            if (domainFilter !== 'all' && node.domain !== domainFilter) continue
            if (hideIsolated && node.degree === 0) continue
            visibleNodeIds.add(node.id)
        }
        const edges = graph.edges.filter((edge) => {
            if (!visibleNodeIds.has(edge.source) || !visibleNodeIds.has(edge.target)) return false
            if (linkTypeFilter !== 'all' && edge.link_type !== linkTypeFilter) return false
            return showFiles || edge.link_type !== 'source'
        })
        return {
            ...graph,
            nodes: graph.nodes.filter((node) => visibleNodeIds.has(node.id)),
            edges,
        }
    }, [domainFilter, graph, hideIsolated, linkTypeFilter, showFiles])

    const graphData = useMemo(() => {
        if (!filteredGraph) return null
        return normalizeGraph(filteredGraph, showFiles)
    }, [filteredGraph, showFiles])

    const searchIds = useMemo(() => {
        if (!filteredGraph || !query.trim()) return new Set<string>()
        return new Set(
            filteredGraph.nodes
                .filter((node) => matchesNode(node, query))
                .map((node) => node.id),
        )
    }, [filteredGraph, query])

    const selectedNode = useMemo(() => {
        if (!graph) return null
        return graph.nodes.find((node) => node.id === selectedId) || null
    }, [graph, selectedId])

    const activeNode = useMemo(() => {
        if (!graph) return null
        return graph.nodes.find((node) => node.id === (hoveredId || selectedId)) || null
    }, [graph, hoveredId, selectedId])

    const selectedLinks = useMemo(() => {
        if (!graph || !selectedId) return { incoming: 0, outgoing: 0, sources: 0 }
        return {
            incoming: graph.edges.filter((edge) => edge.target === selectedId && edge.link_type !== 'source').length,
            outgoing: graph.edges.filter((edge) => edge.source === selectedId && edge.link_type !== 'source').length,
            sources: graph.edges.filter((edge) => edge.target === selectedId && edge.link_type === 'source').length,
        }
    }, [graph, selectedId])

    const domains = useMemo(() => {
        if (!graph) return []
        return Object.keys(graph.stats.domain_counts).sort()
    }, [graph])

    const linkTypes = useMemo(() => {
        if (!graph) return []
        return Object.keys(graph.stats.link_type_counts).sort()
    }, [graph])

    const handleReset = () => {
        setQuery('')
        setDomainFilter('all')
        setLinkTypeFilter('all')
        setHideIsolated(false)
        setShowFiles(true)
        setSelectedId(null)
        setHoveredId(null)
        setResetVersion((value) => value + 1)
    }

    return (
        <div className="relative h-full min-h-0 overflow-hidden bg-[#fbfbfa] text-zinc-900">
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_45%,rgba(24,24,27,0.028)_0,rgba(24,24,27,0)_44%)]" />

            <div className="absolute left-5 top-5 z-20 flex max-w-[calc(100%-8rem)] items-center gap-2">
                <div className="flex h-10 min-w-[280px] items-center gap-2 rounded-full border border-zinc-200/90 bg-white/90 px-3 shadow-[0_8px_24px_rgba(24,24,27,0.08)] backdrop-blur">
                    <Search className="h-4 w-4 text-zinc-400" />
                    <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="搜索实体 / 文件 / 摘要"
                        className="h-8 border-0 bg-transparent px-0 text-sm text-zinc-900 placeholder:text-zinc-400 focus-visible:ring-0"
                    />
                    {query && (
                        <button
                            type="button"
                            onClick={() => setQuery('')}
                            className="rounded-full p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
                            aria-label="清除搜索"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    )}
                </div>
            </div>

            <div className="absolute right-5 top-5 z-20 flex flex-col items-end gap-2">
                <div className="flex items-center gap-2 rounded-full border border-zinc-200/90 bg-white/90 p-1 shadow-[0_8px_24px_rgba(24,24,27,0.08)] backdrop-blur">
                    <IconButton label="筛选" active={filterOpen} onClick={() => setFilterOpen((value) => !value)}>
                        <SlidersHorizontal className="h-4 w-4" />
                    </IconButton>
                    <IconButton label="重置视图" onClick={handleReset}>
                        <RotateCcw className="h-4 w-4" />
                    </IconButton>
                    <IconButton label="刷新图谱" disabled={loading} onClick={load}>
                        <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
                    </IconButton>
                </div>

                {filterOpen && (
                    <div className="w-72 rounded-2xl border border-zinc-200/90 bg-white/94 p-3 shadow-[0_18px_48px_rgba(24,24,27,0.12)] backdrop-blur">
                        <div className="grid gap-2">
                            <label className="text-xs font-medium text-zinc-500">领域</label>
                            <select
                                value={domainFilter}
                                onChange={(event) => setDomainFilter(event.target.value)}
                                className="h-9 rounded-lg border border-zinc-200 bg-white px-3 text-sm text-zinc-800 outline-none focus:border-violet-300"
                            >
                                <option value="all">全部领域</option>
                                {domains.map((domain) => (
                                    <option key={domain} value={domain}>
                                        {DOMAIN_LABELS[domain] || domain}
                                    </option>
                                ))}
                            </select>

                            <label className="mt-2 text-xs font-medium text-zinc-500">关系</label>
                            <select
                                value={linkTypeFilter}
                                onChange={(event) => setLinkTypeFilter(event.target.value)}
                                className="h-9 rounded-lg border border-zinc-200 bg-white px-3 text-sm text-zinc-800 outline-none focus:border-violet-300"
                            >
                                <option value="all">全部关系</option>
                                {linkTypes.map((type) => (
                                    <option key={type} value={type}>
                                        {LINK_TYPE_LABEL[type] || type}
                                    </option>
                                ))}
                            </select>

                            <ToggleButton active={showFiles} onClick={() => setShowFiles((value) => !value)}>
                                {showFiles ? '显示来源文件' : '隐藏来源文件'}
                            </ToggleButton>
                            <ToggleButton active={hideIsolated} onClick={() => setHideIsolated((value) => !value)}>
                                {hideIsolated ? '已隐藏孤立节点' : '显示孤立节点'}
                            </ToggleButton>
                        </div>
                    </div>
                )}
            </div>

            <div className="absolute bottom-5 left-5 z-20 flex items-center gap-2 rounded-full border border-zinc-200/90 bg-white/90 px-4 py-2 text-xs text-zinc-500 shadow-[0_8px_24px_rgba(24,24,27,0.08)] backdrop-blur">
                <Network className="h-4 w-4 text-zinc-400" />
                {graph && filteredGraph ? (
                    <>
                        <LegendDot className="bg-zinc-600" />
                        <span><strong className="text-zinc-900">{graph.stats.file_count ?? 0}</strong> 文件</span>
                        <LegendDot className="bg-zinc-300" />
                        <span><strong className="text-zinc-900">{graph.stats.entity_count ?? graph.stats.page_count}</strong> 实体</span>
                        <span className="h-3 w-px bg-zinc-200" />
                        <span><strong className="text-zinc-900">{filteredGraph.edges.length}</strong> 当前关系</span>
                    </>
                ) : (
                    <span>等待图谱数据</span>
                )}
            </div>

            {activeNode && !selectedNode && (
                <div className="pointer-events-none absolute bottom-5 right-5 z-20 rounded-2xl border border-zinc-200/90 bg-white/88 px-4 py-3 text-sm shadow-[0_18px_48px_rgba(24,24,27,0.12)] backdrop-blur">
                    <div className="font-medium text-zinc-900">{activeNode.title}</div>
                    <div className="mt-1 text-xs text-zinc-500">
                        {(activeNode.node_type || 'entity') === 'file' ? '来源文件' : 'Wiki 实体'}
                    </div>
                </div>
            )}

            {selectedNode && (
                <div className="absolute bottom-5 right-5 z-30 w-80 rounded-2xl border border-zinc-200/90 bg-white/95 p-4 shadow-[0_24px_70px_rgba(24,24,27,0.16)] backdrop-blur">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <div className="flex items-center gap-2 truncate text-base font-semibold text-zinc-950">
                                {(selectedNode.node_type || 'entity') === 'file' && <FileText className="h-4 w-4 shrink-0 text-zinc-500" />}
                                <span className="truncate">{selectedNode.title}</span>
                            </div>
                            <div className="mt-1 truncate text-xs text-zinc-500">
                                {(selectedNode.node_type || 'entity') === 'file'
                                    ? selectedNode.file_type || 'source file'
                                    : selectedNode.slug}
                            </div>
                        </div>
                        {selectedNode.status === 'verified' && (
                            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" />
                        )}
                    </div>

                    {selectedNode.summary && (
                        <p className="mt-3 line-clamp-4 text-sm leading-6 text-zinc-600">
                            {selectedNode.summary}
                        </p>
                    )}

                    <div className="mt-4 grid grid-cols-3 gap-2 text-xs">
                        <Info label="类型" value={(selectedNode.node_type || 'entity') === 'file' ? '文件' : '实体'} />
                        <Info label="连接" value={selectedNode.degree} />
                        <Info label="来源" value={selectedNode.source_count ?? selectedLinks.sources} />
                        <Info label="领域" value={DOMAIN_LABELS[selectedNode.domain] || selectedNode.domain} />
                        <Info label="入链" value={selectedLinks.incoming} />
                        <Info label="出链" value={selectedLinks.outgoing} />
                    </div>

                    {(selectedNode.node_type || 'entity') !== 'file' && (
                        <Button
                            type="button"
                            className="mt-4 h-10 w-full bg-zinc-950 text-white hover:bg-zinc-800"
                            onClick={() => onOpenWikiPage?.(selectedNode.slug)}
                        >
                            <ArrowUpRight className="h-4 w-4" />
                            打开 Wiki 详情
                        </Button>
                    )}
                </div>
            )}

            {loading ? (
                <div className="relative z-10 flex h-full items-center justify-center text-sm text-zinc-500">
                    <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                    正在加载知识图谱...
                </div>
            ) : error ? (
                <div className="relative z-10 flex h-full items-center justify-center text-sm text-rose-500">
                    {error}
                </div>
            ) : graphData ? (
                <WikiGraphCanvas
                    graphData={graphData}
                    query={query}
                    searchIds={searchIds}
                    resetVersion={resetVersion}
                    selectedId={selectedId}
                    onSelectedIdChange={setSelectedId}
                    onHoveredIdChange={setHoveredId}
                />
            ) : (
                <div className="relative z-10 flex h-full flex-col items-center justify-center gap-3 text-sm text-zinc-500">
                    <Network className="h-12 w-12 text-zinc-300" />
                    <div>暂无已发布 Wiki 实体</div>
                </div>
            )}
        </div>
    )
}

function IconButton({
    label,
    active,
    disabled,
    onClick,
    children,
}: {
    label: string
    active?: boolean
    disabled?: boolean
    onClick: () => void
    children: React.ReactNode
}) {
    return (
        <button
            type="button"
            disabled={disabled}
            onClick={onClick}
            title={label}
            aria-label={label}
            className={cn(
                'flex h-9 w-9 items-center justify-center rounded-full text-zinc-500 transition-colors',
                active ? 'bg-zinc-950 text-white' : 'hover:bg-zinc-100 hover:text-zinc-950',
                disabled && 'cursor-not-allowed opacity-45'
            )}
        >
            {children}
        </button>
    )
}

function ToggleButton({
    active,
    onClick,
    children,
}: {
    active: boolean
    onClick: () => void
    children: React.ReactNode
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                'h-9 rounded-lg border px-3 text-left text-sm transition-colors',
                active
                    ? 'border-violet-200 bg-violet-50 text-violet-700'
                    : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
            )}
        >
            {children}
        </button>
    )
}

function LegendDot({ className }: { className: string }) {
    return <span className={cn('h-2.5 w-2.5 rounded-full', className)} />
}

function Info({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="rounded-lg bg-zinc-50 px-2.5 py-2">
            <div className="text-[11px] text-zinc-400">{label}</div>
            <div className="mt-1 truncate text-sm font-medium text-zinc-800">{value}</div>
        </div>
    )
}
