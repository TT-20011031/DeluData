/**
 * 智能体配置 - 类型定义
 */

export interface TemplateInfo {
    template_id: string
    name: string
    description: string
    prompt: string
    is_system: boolean
}

export interface TemplateListResponse {
    system_templates: TemplateInfo[]
    user_templates: TemplateInfo[]
}
