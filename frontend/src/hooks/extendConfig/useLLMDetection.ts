/**
 * LLM 智能检测 Hook
 * 
 * 职责：
 * - 启动后台检测任务
 * - 轮询任务进度
 * - 管理超时和错误处理
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { extendConfigService } from '@/services/extendConfigService';
import type { CandidateField } from '@/types/extendConfig';

// 任务状态类型
type DetectionStatus = 'idle' | 'pending' | 'running' | 'completed' | 'failed' | 'timeout';

interface LLMDetectionState {
    status: DetectionStatus;
    progress: number;
    stage: string;
    candidates: CandidateField[] | null;
    error: string | null;
}

interface UseLLMDetectionOptions {
    /** 轮询间隔（毫秒），默认 2000 */
    pollInterval?: number;
    /** 最大轮询次数，默认 180（6分钟） */
    maxAttempts?: number;
    /** 任务完成回调 */
    onComplete?: (candidates: CandidateField[]) => void;
    /** 任务失败回调 */
    onError?: (error: string) => void;
}

/**
 * LLM 智能检测 Hook
 * 
 * @example
 * ```tsx
 * const { 
 *   status, progress, stage, candidates, error,
 *   startDetection, reset 
 * } = useLLMDetection({
 *   onComplete: (fields) => console.log('检测完成', fields),
 *   onError: (err) => console.error('检测失败', err)
 * });
 * ```
 */
export function useLLMDetection(options: UseLLMDetectionOptions = {}) {
    const {
        pollInterval = 2000,
        maxAttempts = 180,
        onComplete,
        onError
    } = options;

    // 状态
    const [state, setState] = useState<LLMDetectionState>({
        status: 'idle',
        progress: 0,
        stage: '',
        candidates: null,
        error: null
    });

    // 引用
    const taskIdRef = useRef<string | null>(null);
    const attemptCountRef = useRef(0);
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // 清理轮询
    const stopPolling = useCallback(() => {
        if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
        }
    }, []);

    // 轮询状态
    const pollStatus = useCallback(async () => {
        if (!taskIdRef.current) return;

        attemptCountRef.current++;

        // 超时检测
        if (attemptCountRef.current > maxAttempts) {
            stopPolling();
            setState(prev => ({
                ...prev,
                status: 'timeout',
                error: '检测超时，请重试'
            }));
            onError?.('检测超时，请重试');
            return;
        }

        try {
            const result = await extendConfigService.getLLMDetectionStatus(taskIdRef.current);

            if (result.status === 'completed') {
                stopPolling();
                const candidates = result.result || [];
                setState({
                    status: 'completed',
                    progress: 100,
                    stage: '检测完成',
                    candidates,
                    error: null
                });
                onComplete?.(candidates);
            } else if (result.status === 'failed') {
                stopPolling();
                const errorMsg = result.error || '检测失败';
                setState(prev => ({
                    ...prev,
                    status: 'failed',
                    error: errorMsg
                }));
                onError?.(errorMsg);
            } else {
                // 更新进度
                setState(prev => ({
                    ...prev,
                    status: result.status as DetectionStatus,
                    progress: result.progress || prev.progress,
                    stage: result.stage || prev.stage
                }));
            }
        } catch (err) {
            console.error('查询检测状态失败:', err);
            // 不立即停止，继续尝试
        }
    }, [maxAttempts, stopPolling, onComplete, onError]);

    // 启动检测
    const startDetection = useCallback(async (templateId: number) => {
        // 重置状态
        stopPolling();
        attemptCountRef.current = 0;
        taskIdRef.current = null;

        setState({
            status: 'pending',
            progress: 0,
            stage: '正在初始化...',
            candidates: null,
            error: null
        });

        try {
            // 调用启动 API
            const result = await extendConfigService.startLLMDetection(templateId);
            taskIdRef.current = result.task_id;

            setState(prev => ({
                ...prev,
                status: 'running',
                progress: result.progress || 5,
                stage: result.stage || '任务已启动'
            }));

            // 开始轮询
            intervalRef.current = setInterval(pollStatus, pollInterval);

        } catch (err: any) {
            const errorMsg = err.message || '启动检测失败';
            setState({
                status: 'failed',
                progress: 0,
                stage: '',
                candidates: null,
                error: errorMsg
            });
            onError?.(errorMsg);
        }
    }, [pollInterval, pollStatus, stopPolling, onError]);

    // 重置状态
    const reset = useCallback(() => {
        stopPolling();
        taskIdRef.current = null;
        attemptCountRef.current = 0;
        setState({
            status: 'idle',
            progress: 0,
            stage: '',
            candidates: null,
            error: null
        });
    }, [stopPolling]);

    // 组件卸载时清理
    useEffect(() => {
        return () => {
            stopPolling();
        };
    }, [stopPolling]);

    return {
        ...state,
        startDetection,
        reset,
        isLoading: state.status === 'pending' || state.status === 'running'
    };
}

export default useLLMDetection;
