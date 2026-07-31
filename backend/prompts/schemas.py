"""
Native Tool Calling Schema 定义
统一管理所有 Agent 的结构化输出 Schema
"""
# ========== Planner Schema ==========
# 用于 planner_node 的结构化输出
# 关键：thinking 字段可选，避免 Planner 在结构化输出场景产生额外负担
SUBMIT_PLAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": "提交任务执行计划。可选提供 thinking 分析，但必须返回 steps 与 summary。",
        "parameters": {
            "type": "object",
            "properties": {
                "thinking": {
                    "type": "string",
                    "description": "可选思考过程。分析用户意图、识别潜在歧义、评估工具置信度。"
                },
                "steps": {
                    "type": "array",
                    "description": "任务执行步骤列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {
                                "type": "string",
                                "description": "步骤唯一标识"
                            },
                            "description": {
                                "type": "string",
                                "description": "步骤描述"
                            },
                            "worker": {
                                "type": "string",
                                "enum": ["sql_worker", "doc_worker", "office_worker", "chart_worker", "inspect_file"],
                                "description": "执行该步骤的 Worker"
                            },
                            "editable": {
                                "type": "boolean",
                                "description": "用户是否可编辑此步骤参数"
                            },
                            "suggested_value": {
                                "type": "string",
                                "description": "可编辑步骤的建议默认值"
                            },
                            "input_type": {
                                "type": "string",
                                "description": "输入类型: text, select, date 等"
                            },
                            "input_label": {
                                "type": "string",
                                "description": "输入框标签"
                            },
                            "options": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "select 类型的选项列表"
                            },
                            "params": {
                                "type": "object",
                                "description": "Step-level structured params. 当 Skill 手册指定了模板ID时，必须在此填写。",
                                "properties": {
                                    "template_id": {
                                        "type": "integer",
                                        "description": "文档模板ID。如果 Skill 手册中指定了模板ID，必须填写此字段。"
                                    },
                                    "template_mode": {
                                        "type": "string",
                                        "enum": ["draft", "render", "preview"],
                                        "description": "模板模式。有 template_id 时默认为 draft。"
                                    },
                                    "template_version": {
                                        "type": "string",
                                        "description": "模板版本号"
                                    },
                                    "output_filename": {
                                        "type": "string",
                                        "description": "输出文件名"
                                    },
                                    "doc_scope": {
                                        "type": "object",
                                        "description": "文档检索范围"
                                    }
                                }
                            },
                        },
                        "required": ["step_id", "description", "worker"]
                    }
                },
                "summary": {
                    "type": "string",
                    "description": "计划的简短摘要（一句话）"
                },
                "need_confirm": {
                    "type": "boolean",
                    "description": "是否需要用户确认。高风险操作设为 true，简单查询可设为 false"
                },
                "tool_confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "工具选择置信度。medium 时应生成多个备选步骤"
                },
                "detected_constraints": {
                    "type": "object",
                    "description": "检测到的约束条件（槽位填充结果）"
                }
            },
            "required": ["steps", "summary"]
        }
    }
}
