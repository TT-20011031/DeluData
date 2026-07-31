/**
 * InterruptInput 组件
 * 
 * 处理 Inline HITL (Interrupt/Resume) 的用户输入界面
 * 使用 shadcn/ui 组件，适配深色主题
 */
import React, { useState, useEffect } from 'react';
import { MessageSquare, Send, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

export interface InterruptData {
  message: string;
  options: string[];
  target_step_id: string;
  source: 'planner' | 'reflector' | 'executor' | 'router';
  payload?: any;
  signal_type?: string;
}

interface TemplatePreviewField {
  key: string;
  label?: string;
  type?: string;
  value?: any;
}

interface TemplatePreviewTable {
  key: string;
  label?: string;
  rows?: any[];
  columns?: any[];
}

interface TemplatePreviewPayload {
  template_id?: number | string;
  template_name?: string;
  file_type?: string;
  context?: Record<string, any>;
  fields?: TemplatePreviewField[];
  tables?: TemplatePreviewTable[];
  schema?: Record<string, any>;
}

interface InterruptInputProps {
  /** 中断数据 */
  data: InterruptData;
  /** 会话ID */
  sessionId: string;
  /** 计划ID */
  planId: string;
  /** 提交回调 */
  onSubmit: (input: string) => void;
  /** 取消回调 */
  onCancel?: () => void;
  /** 是否正在提交 */
  isSubmitting?: boolean;
}

export const InterruptInput: React.FC<InterruptInputProps> = ({
  data,
  sessionId: _sessionId, // 保留供父组件 API 调用使用
  planId: _planId, // 保留供父组件 API 调用使用
  onSubmit,
  onCancel,
  isSubmitting = false,
}) => {
  const [inputValue, setInputValue] = useState('');
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [tableValues, setTableValues] = useState<Record<string, string>>({});
  const [tableErrors, setTableErrors] = useState<Record<string, string>>({});

  const previewPayload = (data.signal_type === 'template_preview' && data.payload)
    ? (data.payload as TemplatePreviewPayload)
    : null;

  useEffect(() => {
    if (!previewPayload) return;
    const nextFields: Record<string, string> = {};
    const nextTables: Record<string, string> = {};

    if (previewPayload.fields && previewPayload.fields.length > 0) {
      previewPayload.fields.forEach((f) => {
        nextFields[f.key] = f.value !== undefined && f.value !== null ? String(f.value) : '';
      });
    } else if (previewPayload.context) {
      Object.entries(previewPayload.context).forEach(([key, value]) => {
        if (Array.isArray(value)) {
          nextTables[key] = JSON.stringify(value, null, 2);
        } else {
          nextFields[key] = value !== undefined && value !== null ? String(value) : '';
        }
      });
    }

    if (previewPayload.tables && previewPayload.tables.length > 0) {
      previewPayload.tables.forEach((t) => {
        const rows = t.rows || [];
        nextTables[t.key] = JSON.stringify(rows, null, 2);
      });
    }

    setFieldValues(nextFields);
    setTableValues(nextTables);
    setTableErrors({});
  }, [previewPayload?.template_id, previewPayload?.fields, previewPayload?.tables, previewPayload?.context]);

  const handleSubmit = () => {
    if (inputValue.trim()) {
      onSubmit(inputValue.trim());
    }
  };

  const handleOptionClick = (option: string) => {
    onSubmit(option);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const getSourceLabel = () => {
    switch (data.source) {
      case 'planner': return '需要更多信息';
      case 'reflector': return '执行遇到问题';
      case 'executor': return '需要澄清';
      case 'router': return '需要确认口径';
      default: return '请补充信息';
    }
  };

  const handlePreviewSubmit = () => {
    if (!previewPayload) return;
    const baseContext = { ...(previewPayload.context || {}) };
    const nextContext: Record<string, any> = { ...baseContext };

    Object.entries(fieldValues).forEach(([key, value]) => {
      nextContext[key] = value;
    });

    const nextErrors: Record<string, string> = {};
    Object.entries(tableValues).forEach(([key, value]) => {
      if (!value.trim()) {
        nextContext[key] = [];
        return;
      }
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          nextContext[key] = parsed;
        } else {
          nextErrors[key] = '表格内容必须是 JSON 数组';
        }
      } catch (error) {
        nextErrors[key] = 'JSON 解析失败';
      }
    });

    setTableErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) return;

    onSubmit(JSON.stringify({ context: nextContext }));
  };

  if (previewPayload) {
    return (
      <div className="rounded-xl border border-white/10 bg-manus-secondary/60 backdrop-blur-sm p-4 shadow-lg">
        <div className="flex items-center gap-2 mb-3">
          <MessageSquare className="h-4 w-4 text-accent" />
          <span className="text-xs font-semibold text-accent uppercase tracking-wide">
            字段预览
          </span>
        </div>

        <p className="text-sm text-manus-text mb-4 leading-relaxed">
          {data.message}
        </p>

        <div className="space-y-4">
          {previewPayload.template_name && (
            <div className="text-xs text-manus-subtle">
              模板：{previewPayload.template_name}
            </div>
          )}

          {Object.keys(fieldValues).length > 0 && (
            <div className="space-y-3">
              <div className="text-xs font-semibold text-manus-text">字段</div>
              {Object.entries(fieldValues).map(([key, value]) => (
                <div key={key} className="flex items-center gap-2">
                  <span className="w-32 text-xs text-manus-subtle truncate">{key}</span>
                  <Input
                    value={value}
                    onChange={(e) => setFieldValues(prev => ({ ...prev, [key]: e.target.value }))}
                    className="flex-1 bg-manus-bg/50 border-white/10 text-manus-text"
                  />
                </div>
              ))}
            </div>
          )}

          {Object.keys(tableValues).length > 0 && (
            <div className="space-y-3">
              <div className="text-xs font-semibold text-manus-text">表格（JSON 数组）</div>
              {Object.entries(tableValues).map(([key, value]) => (
                <div key={key} className="space-y-2">
                  <div className="text-xs text-manus-subtle">{key}</div>
                  <Textarea
                    value={value}
                    onChange={(e) => setTableValues(prev => ({ ...prev, [key]: e.target.value }))}
                    rows={4}
                    className="bg-manus-bg/50 border-white/10 text-manus-text font-mono text-xs"
                  />
                  {tableErrors[key] && (
                    <div className="text-xs text-red-400">{tableErrors[key]}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex gap-2 mt-4">
          <Button
            onClick={handlePreviewSubmit}
            disabled={isSubmitting}
            className="bg-accent hover:bg-accent/80 text-white"
          >
            {isSubmitting ? '提交中...' : '确认并继续'}
          </Button>
          {onCancel && (
            <Button
              variant="outline"
              onClick={onCancel}
              disabled={isSubmitting}
              className="border-white/20 text-manus-subtle hover:bg-white/5"
            >
              取消
            </Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/10 bg-manus-secondary/60 backdrop-blur-sm p-4 shadow-lg">
      {/* 头部 */}
      <div className="flex items-center gap-2 mb-3">
        <MessageSquare className="h-4 w-4 text-accent" />
        <span className="text-xs font-semibold text-accent uppercase tracking-wide">
          {getSourceLabel()}
        </span>
      </div>

      {/* 消息内容 */}
      <p className="text-sm text-manus-text mb-4 leading-relaxed">
        {data.message}
      </p>

      {/* 选项按钮 */}
      {data.options && data.options.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {data.options.map((option, index) => (
            <Button
              key={index}
              variant="outline"
              size="sm"
              onClick={() => handleOptionClick(option)}
              disabled={isSubmitting}
              className="border-white/20 bg-white/5 hover:bg-accent hover:text-white hover:border-accent transition-all"
            >
              {option}
            </Button>
          ))}
        </div>
      )}

      {/* 输入框和按钮 */}
      <div className="flex gap-2">
        <Input
          type="text"
          placeholder="或者输入您的回复..."
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isSubmitting}
          className="flex-1 bg-manus-bg/50 border-white/10 focus:border-accent text-manus-text placeholder:text-manus-subtle"
        />
        <Button
          onClick={handleSubmit}
          disabled={isSubmitting || !inputValue.trim()}
          className="bg-accent hover:bg-accent/80 text-white"
        >
          {isSubmitting ? (
            <span className="animate-pulse">提交中...</span>
          ) : (
            <>
              <Send className="h-4 w-4 mr-1" />
              发送
            </>
          )}
        </Button>
        {onCancel && (
          <Button
            variant="outline"
            onClick={onCancel}
            disabled={isSubmitting}
            className="border-white/20 text-manus-subtle hover:bg-white/5"
          >
            <X className="h-4 w-4 mr-1" />
            取消
          </Button>
        )}
      </div>
    </div>
  );
};

export default InterruptInput;
