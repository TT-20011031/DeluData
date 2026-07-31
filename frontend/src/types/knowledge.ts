/**
 * 知识库相关类型定义
 */
import type { Node, Edge } from '@xyflow/react'

// =============================================================================
// 部门与统计
// =============================================================================

/** 部门统计树状结构 */
export interface DeptStats {
    id: number
    name: string
    count: number
    children: DeptStats[]
}

/** 概览统计数据 */
export interface StatsData {
    global: { count: number }
    private: { count: number }
    departments: DeptStats[]
}

// =============================================================================
// 文件结构
// =============================================================================

/** 文件夹/文件节点 */
export interface FolderNode {
    id: string
    name: string
    type: 'folder' | 'file'
    file_type?: string
    status?: string
    pageindex_status?: string | null
    children?: FolderNode[]
}

/** 预览文件信息 */
export interface PreviewFile {
    id: string
    name: string
    type: string
}

// =============================================================================
// 文档溯源信息
// =============================================================================

export interface OriginInfo {
    document_id: string
    name: string
    owner_name: string
    department_name: string | null
    visibility: string
    created_at: string
    // [新增] 切片统计
    chunk_count?: number
    chunks_with_file_id?: number
    file_id_coverage?: string
}

// =============================================================================
// 操作相关
// =============================================================================

/** 删除目标 */
export interface DeleteTarget {
    id: string
    type: 'file' | 'folder'
}

export interface KnowledgePermissionSet {
    canUpload: boolean
    canDelete: boolean
    canRename: boolean
    canMove: boolean
    canCreateFolder: boolean
}

/** 图谱节点（扩展 ReactFlow Node） */
export type KnowledgeNode = Node

/** 图谱边（扩展 ReactFlow Edge） */
export type KnowledgeEdge = Edge

// =============================================================================
// 上传相关
// =============================================================================

/** 上传结果 */
export interface UploadResult {
    file: File
    success: boolean
    error?: string
}

/** 可见范围类型 */
export type VisibilityType = 'public' | 'dept' | 'private'

// =============================================================================
// 连接/关联相关
// =============================================================================

/** 连接参数 */
export interface ConnectParams {
    source: string
    target: string
}

/** 预设关联类型 */
export const RELATION_TYPES = ['关联', '引用', '补充', '冲突', '前置'] as const
export type RelationType = typeof RELATION_TYPES[number] | 'custom'

// =============================================================================
// 部门选项（简化版，用于下拉框）
// =============================================================================

export interface DepartmentOption {
    id: number
    name: string
}
