import {
    forceCenter,
    forceCollide,
    forceLink,
    forceManyBody,
    forceSimulation,
    forceX,
    forceY,
    type Simulation,
    type SimulationLinkDatum,
    type SimulationNodeDatum,
} from 'd3-force'

import type { WikiGraphEdge, WikiGraphNode, WikiGraphResponse } from '@/types/wiki'

export type GraphNodeType = 'entity' | 'file'

export interface CanvasGraphNode extends SimulationNodeDatum {
    id: string
    nodeType: GraphNodeType
    title: string
    slug: string
    summary: string | null
    domain: string
    status: string
    degree: number
    radius: number
    fileId: string | null
    fileType: string | null
    fileName: string | null
    sourceCount: number
    sourceScope: WikiGraphNode['source_scope']
}

export interface CanvasGraphLink extends SimulationLinkDatum<CanvasGraphNode> {
    id: string
    sourceId: string
    targetId: string
    linkType: WikiGraphEdge['link_type']
    evidenceCount: number
}

export interface CanvasGraphData {
    nodes: CanvasGraphNode[]
    links: CanvasGraphLink[]
    adjacency: Map<string, Set<string>>
    byId: Map<string, CanvasGraphNode>
}

export interface GraphViewport {
    x: number
    y: number
    zoom: number
}

export interface DrawOptions {
    width: number
    height: number
    dpr: number
    viewport: GraphViewport
    hoveredId: string | null
    selectedId: string | null
    searchIds: Set<string>
    labelsVisible: boolean
}

export interface HitGrid {
    cellSize: number
    buckets: Map<string, CanvasGraphNode[]>
}

const OBSIDIAN_PURPLE = '#8b5cf6'
const FILE_NODE = '#505256'
const ENTITY_NODE = '#d0d2d6'
const ENTITY_NODE_ACTIVE = '#6b7280'
const FADED_NODE = '#e5e7eb'
const FILE_EDGE = '#d9dce1'
const WIKI_EDGE = '#c5c9d1'

function nodeRadius(node: WikiGraphNode, maxDegree: number) {
    const degree = Math.max(0, node.degree || 0)
    const normalized = maxDegree > 0 ? Math.log1p(degree) / Math.log1p(maxDegree) : 0
    if ((node.node_type || 'entity') === 'file') {
        return 5.5 + normalized * 5
    }
    return 4.2 + normalized * 4.2
}

function buildAdjacency(nodes: CanvasGraphNode[], links: CanvasGraphLink[]) {
    const adjacency = new Map<string, Set<string>>()
    for (const node of nodes) adjacency.set(node.id, new Set())
    for (const link of links) {
        adjacency.get(link.sourceId)?.add(link.targetId)
        adjacency.get(link.targetId)?.add(link.sourceId)
    }
    return adjacency
}

export function normalizeGraph(graph: WikiGraphResponse, showFiles: boolean): CanvasGraphData {
    const rawNodes = graph.nodes.filter((node) => showFiles || (node.node_type || 'entity') !== 'file')
    const ids = new Set(rawNodes.map((node) => node.id))
    const maxDegree = Math.max(1, ...rawNodes.map((node) => node.degree || 0))
    const nodes: CanvasGraphNode[] = rawNodes.map((node, index) => {
        const angle = index * Math.PI * (3 - Math.sqrt(5))
        const radius = 80 + Math.sqrt(index) * 80
        return {
            id: node.id,
            nodeType: (node.node_type || 'entity') as GraphNodeType,
            title: node.title,
            slug: node.slug,
            summary: node.summary,
            domain: String(node.domain || 'general'),
            status: String(node.status || 'active'),
            degree: node.degree || 0,
            radius: nodeRadius(node, maxDegree),
            fileId: node.file_id || null,
            fileType: node.file_type || null,
            fileName: node.file_name || null,
            sourceCount: node.source_count || node.source_scope?.source_count || 0,
            sourceScope: node.source_scope,
            x: Math.cos(angle) * radius,
            y: Math.sin(angle) * radius,
        }
    })
    const byId = new Map(nodes.map((node) => [node.id, node]))
    const links: CanvasGraphLink[] = graph.edges
        .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
        .filter((edge) => showFiles || edge.link_type !== 'source')
        .map((edge) => ({
            id: edge.id,
            sourceId: edge.source,
            targetId: edge.target,
            source: edge.source,
            target: edge.target,
            linkType: edge.link_type,
            evidenceCount: edge.evidence_count || 0,
        }))

    return {
        nodes,
        links,
        adjacency: buildAdjacency(nodes, links),
        byId,
    }
}

export function createGraphSimulation(
    nodes: CanvasGraphNode[],
    links: CanvasGraphLink[],
): Simulation<CanvasGraphNode, CanvasGraphLink> {
    return forceSimulation<CanvasGraphNode>(nodes)
        .force(
            'link',
            forceLink<CanvasGraphNode, CanvasGraphLink>(links)
                .id((node) => node.id)
                .distance((link) => (link.linkType === 'source' ? 118 : 172))
                .strength((link) => (link.linkType === 'source' ? 0.5 : 0.26)),
        )
        .force(
            'charge',
            forceManyBody<CanvasGraphNode>().strength((node) => (
                node.nodeType === 'file' ? -520 : -330
            )),
        )
        .force(
            'collide',
            forceCollide<CanvasGraphNode>().radius((node) => (
                node.nodeType === 'file' ? node.radius + 38 : node.radius + 30
            )).iterations(2),
        )
        .force('center', forceCenter(0, 0).strength(0.035))
        .force('x', forceX<CanvasGraphNode>(0).strength(0.018))
        .force('y', forceY<CanvasGraphNode>(0).strength(0.018))
        .alpha(0.95)
        .alphaDecay(0.028)
        .velocityDecay(0.42)
}

export function getRelatedIds(activeId: string | null, adjacency: Map<string, Set<string>>) {
    const related = new Set<string>()
    if (!activeId) return related
    related.add(activeId)
    for (const id of adjacency.get(activeId) || []) related.add(id)
    return related
}

export function isNodeFixed(node: CanvasGraphNode | null | undefined) {
    return Boolean(node && (node.fx !== undefined || node.fy !== undefined))
}

export function fixNodeAtCurrentPosition(node: CanvasGraphNode) {
    node.fx = node.x
    node.fy = node.y
}

export function unfixNode(node: CanvasGraphNode) {
    node.fx = undefined
    node.fy = undefined
}

export function clearFixedNodePositions(nodes: CanvasGraphNode[]) {
    for (const node of nodes) {
        unfixNode(node)
    }
}

export function moveNodeWithNeighbors(
    graph: CanvasGraphData,
    nodeId: string,
    neighborIds: string[],
    nextX: number,
    nextY: number,
    deltaX: number,
    deltaY: number,
    followRatio = 0.35,
) {
    const node = graph.byId.get(nodeId)
    if (!node) return false

    node.x = nextX
    node.y = nextY
    node.fx = nextX
    node.fy = nextY

    const followX = deltaX * followRatio
    const followY = deltaY * followRatio
    for (const id of neighborIds) {
        const neighbor = graph.byId.get(id)
        if (!neighbor) continue
        neighbor.x = (neighbor.x || 0) + followX
        neighbor.y = (neighbor.y || 0) + followY
        if (neighbor.fx !== undefined) neighbor.fx = (neighbor.fx || 0) + followX
        if (neighbor.fy !== undefined) neighbor.fy = (neighbor.fy || 0) + followY
    }

    return true
}

export function buildHitGrid(nodes: CanvasGraphNode[], cellSize = 72): HitGrid {
    const buckets = new Map<string, CanvasGraphNode[]>()
    for (const node of nodes) {
        const x = node.x || 0
        const y = node.y || 0
        const key = `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}`
        const bucket = buckets.get(key) || []
        bucket.push(node)
        buckets.set(key, bucket)
    }
    return { cellSize, buckets }
}

export function hitTestNode(
    grid: HitGrid,
    worldX: number,
    worldY: number,
    zoom: number,
): CanvasGraphNode | null {
    const cx = Math.floor(worldX / grid.cellSize)
    const cy = Math.floor(worldY / grid.cellSize)
    let best: CanvasGraphNode | null = null
    let bestDistance = Number.POSITIVE_INFINITY

    for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
            const bucket = grid.buckets.get(`${cx + dx}:${cy + dy}`)
            if (!bucket) continue
            for (const node of bucket) {
                const distance = Math.hypot((node.x || 0) - worldX, (node.y || 0) - worldY)
                const hitRadius = Math.max(node.radius + 7 / zoom, 13 / zoom)
                if (distance <= hitRadius && distance < bestDistance) {
                    best = node
                    bestDistance = distance
                }
            }
        }
    }
    return best
}

export function screenToWorld(
    screenX: number,
    screenY: number,
    viewport: GraphViewport,
) {
    return {
        x: (screenX - viewport.x) / viewport.zoom,
        y: (screenY - viewport.y) / viewport.zoom,
    }
}

export function fitViewport(nodes: CanvasGraphNode[], width: number, height: number): GraphViewport {
    if (nodes.length === 0) return { x: width / 2, y: height / 2, zoom: 1 }
    let minX = Number.POSITIVE_INFINITY
    let maxX = Number.NEGATIVE_INFINITY
    let minY = Number.POSITIVE_INFINITY
    let maxY = Number.NEGATIVE_INFINITY
    for (const node of nodes) {
        const x = node.x || 0
        const y = node.y || 0
        minX = Math.min(minX, x)
        maxX = Math.max(maxX, x)
        minY = Math.min(minY, y)
        maxY = Math.max(maxY, y)
    }
    const graphWidth = Math.max(1, maxX - minX)
    const graphHeight = Math.max(1, maxY - minY)
    const zoom = Math.max(0.22, Math.min(1.65, Math.min(width / (graphWidth + 260), height / (graphHeight + 220))))
    const centerX = (minX + maxX) / 2
    const centerY = (minY + maxY) / 2
    return {
        zoom,
        x: width / 2 - centerX * zoom,
        y: height / 2 - centerY * zoom,
    }
}

function linkEndpoint(value: string | number | CanvasGraphNode | undefined): CanvasGraphNode | null {
    if (!value || typeof value === 'string' || typeof value === 'number') return null
    return value
}

function intersects(a: DOMRect, b: DOMRect) {
    return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
}

function shouldDrawLabel(
    node: CanvasGraphNode,
    relatedIds: Set<string>,
    searchIds: Set<string>,
    selectedId: string | null,
    hoveredId: string | null,
    labelsVisible: boolean,
) {
    return (
        labelsVisible
        || node.id === selectedId
        || node.id === hoveredId
        || relatedIds.has(node.id)
        || searchIds.has(node.id)
    )
}

export function drawGraph(
    ctx: CanvasRenderingContext2D,
    graph: CanvasGraphData,
    options: DrawOptions,
) {
    const { width, height, dpr, viewport, hoveredId, selectedId, searchIds, labelsVisible } = options
    const activeId = hoveredId || selectedId
    const relatedIds = getRelatedIds(activeId, graph.adjacency)

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = '#fbfbfa'
    ctx.fillRect(0, 0, width, height)

    ctx.setTransform(
        dpr * viewport.zoom,
        0,
        0,
        dpr * viewport.zoom,
        dpr * viewport.x,
        dpr * viewport.y,
    )
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'

    for (const link of graph.links) {
        const source = linkEndpoint(link.source)
        const target = linkEndpoint(link.target)
        if (!source || !target) continue
        const highlighted = activeId ? source.id === activeId || target.id === activeId : false
        const searchHighlighted = !activeId && (searchIds.has(source.id) || searchIds.has(target.id))
        const faded = Boolean(activeId && !highlighted)
        ctx.beginPath()
        ctx.moveTo(source.x || 0, source.y || 0)
        ctx.lineTo(target.x || 0, target.y || 0)
        ctx.strokeStyle = highlighted || searchHighlighted ? OBSIDIAN_PURPLE : link.linkType === 'source' ? FILE_EDGE : WIKI_EDGE
        ctx.globalAlpha = faded ? 0.06 : highlighted ? 0.92 : searchHighlighted ? 0.72 : link.linkType === 'source' ? 0.48 : 0.58
        ctx.lineWidth = (highlighted || searchHighlighted ? 1.35 : link.linkType === 'source' ? 0.72 : 0.82) / viewport.zoom
        ctx.stroke()
    }

    for (const node of graph.nodes) {
        const active = node.id === activeId
        const related = relatedIds.has(node.id)
        const searchHit = searchIds.has(node.id)
        const faded = Boolean(activeId && !related)
        const x = node.x || 0
        const y = node.y || 0
        ctx.beginPath()
        ctx.arc(x, y, node.radius, 0, Math.PI * 2)
        ctx.fillStyle = active
            ? OBSIDIAN_PURPLE
            : faded
                ? FADED_NODE
                : searchHit
                    ? '#a78bfa'
                : node.nodeType === 'file'
                    ? FILE_NODE
                    : related
                        ? ENTITY_NODE_ACTIVE
                        : ENTITY_NODE
        ctx.globalAlpha = faded ? 0.38 : 1
        ctx.fill()
        if (node.status === 'verified' || node.nodeType === 'file') {
            ctx.globalAlpha = faded ? 0.22 : node.nodeType === 'file' ? 0.42 : 0.35
            ctx.strokeStyle = '#111827'
            ctx.lineWidth = 1 / viewport.zoom
            ctx.stroke()
        }
        if (active || searchHit) {
            ctx.beginPath()
            ctx.arc(x, y, node.radius + 7 / viewport.zoom, 0, Math.PI * 2)
            ctx.globalAlpha = active ? 0.16 : 0.12
            ctx.strokeStyle = OBSIDIAN_PURPLE
            ctx.lineWidth = (active ? 5 : 3.5) / viewport.zoom
            ctx.stroke()
        }
    }

    ctx.globalAlpha = 1
    const labels = graph.nodes
        .filter((node) => shouldDrawLabel(node, relatedIds, searchIds, selectedId, hoveredId, labelsVisible))
        .sort((a, b) => {
            const scoreA = (a.id === activeId ? 1000 : 0) + (searchIds.has(a.id) ? 600 : 0) + (relatedIds.has(a.id) ? 300 : 0) + a.degree
            const scoreB = (b.id === activeId ? 1000 : 0) + (searchIds.has(b.id) ? 600 : 0) + (relatedIds.has(b.id) ? 300 : 0) + b.degree
            return scoreB - scoreA
        })

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const boxes: DOMRect[] = []
    ctx.font = '500 14px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'

    for (const node of labels) {
        const sx = (node.x || 0) * viewport.zoom + viewport.x
        const sy = (node.y || 0) * viewport.zoom + viewport.y
        if (sx < -80 || sx > width + 80 || sy < -40 || sy > height + 80) continue

        const text = node.title.length > 30 ? `${node.title.slice(0, 29)}...` : node.title
        const metrics = ctx.measureText(text)
        const labelWidth = Math.min(220, metrics.width + 10)
        const labelHeight = 20
        const top = sy + node.radius * viewport.zoom + 5
        const rect = new DOMRect(sx - labelWidth / 2, top, labelWidth, labelHeight)
        const important = node.id === activeId || searchIds.has(node.id) || relatedIds.has(node.id)
        if (!important && boxes.some((box) => intersects(rect, box))) continue

        boxes.push(rect)
        ctx.fillStyle = node.id === activeId
            ? '#111827'
            : node.nodeType === 'file'
                ? '#27272a'
                : '#3f3f46'
        ctx.globalAlpha = important ? 1 : 0.82
        ctx.fillText(text, sx, top)
    }
    ctx.globalAlpha = 1
}
