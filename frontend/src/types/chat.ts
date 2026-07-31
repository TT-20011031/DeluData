/**
 * 聊天相关类型定义
 * 
 * 包含任务步骤、任务计划、思考节点等类型
 */

// 子步骤类型 (DeluSQL 生成器子进度)
export interface SubStep {
    id: string
    name: string
    status: 'pending' | 'running' | 'done' | 'error'
    detail?: string
}

// 任务步骤类型（更新为支持 Consultative Planner）
export interface TaskStep {
    step_id: string
    description: string
    worker: string
    status: 'pending' | 'running' | 'completed' | 'error' | 'waiting' | 'cancelled' | 'loading' | 'skipped' | 'invalid_data'
    result?: string
    // [逻辑门控] 任务分类
    category?: 'extraction' | 'terminal' | 'utility'
    // Consultative Planner 字段
    editable?: boolean
    suggested_value?: string
    input_type?: 'text' | 'number' | 'select' | 'date'
    input_label?: string
    options?: string[]
    // 子步骤 (DeluSQL 生成器进度)
    subSteps?: SubStep[]
    // [嵌套化] 行内反思和重试子任务
    inlineThoughts?: ThoughtNode[]  // 该任务的反思气泡
    retrySteps?: TaskStep[]         // 该任务的重试子任务（递归）
    params?: Record<string, any>
}

// 系统节点类型（用于反思机制）
export interface SystemNode {
    id: string
    label: string
    status: 'pending' | 'running' | 'completed' | 'error'
    thinking?: string  // 思考过程日志
}

// [双通道反馈] Reflector 拟人化思考节点
export interface ThoughtNode {
    id: string
    thought: string
    verdict: 'pass' | 'fail' | 'partial' | 'break' | 'loading'
    roundIndex: number
    timestamp: string
    targetStepId?: string  // [嵌套化] 关联的任务 step_id
}

// 任务计划类型
export interface TaskPlan {
    session_id: string
    plan_id: string
    summary: string
    steps: TaskStep[]
    status: 'draft' | 'confirmed' | 'executing' | 'completed' | 'error' | 'planning' | 'suspended'
    selected_skill_name?: string
    // 系统节点（反思机制）
    systemNodes?: SystemNode[]
    // [双通道反馈] Reflector 思考节点
    thoughtNodes?: ThoughtNode[]
}

// 上传文件信息
export interface UploadedFile {
    name: string
    path: string
    session_id: string
    sandbox_path: string
    type?: 'file' | 'image'   // 文件类型：普通文件或图片
    previewUrl?: string       // 图片预览 URL (ObjectURL)
    base64?: string           // 图片 Base64 数据 (用于发送给后端 VLM)
}
