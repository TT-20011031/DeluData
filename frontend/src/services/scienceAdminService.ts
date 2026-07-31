import { API_BASE_URL } from '@/config';
import { getAuthHeader } from '@/stores/authStore';
import type {
    ScienceDeviceActivationCode,
    ScienceDeviceActivationCodeCreateRequest,
    ScienceDeviceActivationCodeCreateResponse,
    KnowledgeScope,
    QuizGenerateRequest,
    QuizGenerateResponse,
    ScienceCoupon,
    ScienceCouponIssuance,
    ScienceCouponRedeemRequest,
    ScienceCouponRedeemResponse,
    ScienceCouponUpsertRequest,
    ScienceDevice,
    ScienceDeviceUpsertRequest,
    ScienceQuizQuestion,
    ScienceQuizQuestionUpsertRequest,
    ScienceRewardPolicy,
    ScienceVoiceProfile,
    ScienceWakewordConfig,
} from '@/types/scienceAdmin';

const BASE = `${API_BASE_URL}/admin/science`;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`${BASE}${path}`, {
        ...options,
        headers: {
            ...getAuthHeader(),
            'Content-Type': 'application/json',
            ...options.headers,
        },
    });

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: '请求失败' }));
        throw new Error(error.detail || `Request failed: ${response.status}`);
    }

    if (response.status === 204) {
        return undefined as T;
    }
    return response.json() as Promise<T>;
}

export const scienceAdminService = {
    listDevices: () => request<ScienceDevice[]>('/devices'),
    upsertDevice: (payload: ScienceDeviceUpsertRequest) =>
        request<ScienceDevice>('/devices', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
    deleteDevice: (deviceId: string) =>
        request<{ success: boolean }>(`/devices/${encodeURIComponent(deviceId)}`, {
            method: 'DELETE',
        }),
    listDeviceActivationCodes: () => request<ScienceDeviceActivationCode[]>('/device-activation-codes'),
    createDeviceActivationCode: (payload: ScienceDeviceActivationCodeCreateRequest) =>
        request<ScienceDeviceActivationCodeCreateResponse>('/device-activation-codes', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
    getKnowledgeScope: () => request<KnowledgeScope>('/knowledge-scope'),
    updateKnowledgeScope: (payload: KnowledgeScope) =>
        request<KnowledgeScope>('/knowledge-scope', {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),

    getWakeword: () => request<ScienceWakewordConfig>('/wakeword'),
    updateWakeword: (payload: ScienceWakewordConfig) =>
        request<ScienceWakewordConfig>('/wakeword', {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),

    getVoiceProfile: () => request<ScienceVoiceProfile>('/voice-profiles'),
    updateVoiceProfile: (payload: ScienceVoiceProfile) =>
        request<ScienceVoiceProfile>('/voice-profiles', {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),

    listQuizQuestions: () => request<ScienceQuizQuestion[]>('/quiz/questions'),
    createQuizQuestion: (payload: ScienceQuizQuestionUpsertRequest) =>
        request<ScienceQuizQuestion>('/quiz/questions', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
    updateQuizQuestion: (questionId: string, payload: Partial<ScienceQuizQuestionUpsertRequest>) =>
        request<ScienceQuizQuestion>(`/quiz/questions/${questionId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),
    deleteQuizQuestion: (questionId: string) =>
        request<{ success: boolean }>(`/quiz/questions/${questionId}`, {
            method: 'DELETE',
        }),

    getRewardPolicy: () => request<ScienceRewardPolicy>('/reward-policies'),
    updateRewardPolicy: (payload: ScienceRewardPolicy) =>
        request<ScienceRewardPolicy>('/reward-policies', {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),

    listCoupons: () => request<ScienceCoupon[]>('/coupons'),
    createCoupon: (payload: ScienceCouponUpsertRequest) =>
        request<ScienceCoupon>('/coupons', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
    updateCoupon: (couponId: string, payload: Partial<ScienceCouponUpsertRequest>) =>
        request<ScienceCoupon>(`/coupons/${couponId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        }),
    listCouponIssuances: () => request<ScienceCouponIssuance[]>('/coupons/issuances'),
    redeemCoupon: (payload: ScienceCouponRedeemRequest) =>
        request<ScienceCouponRedeemResponse>('/coupons/redeem', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
    generateQuizQuestions: (payload: QuizGenerateRequest) =>
        request<QuizGenerateResponse>('/quiz/generate', {
            method: 'POST',
            body: JSON.stringify(payload),
        }),
};
