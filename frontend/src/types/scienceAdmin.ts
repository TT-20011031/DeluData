export type DeviceKbScopeMode = 'dept' | 'files';

export interface ScienceDevice {
    device_id: string;
    name?: string | null;
    service_user_id: string;
    dept_id?: number | null;
    is_active: boolean;
    kb_scope_mode: DeviceKbScopeMode;
    kb_scope_dept_ids: string[];
    kb_scope_file_ids: string[];
    device_token: string;
    workspace_id: string;
    activation_key_last4?: string | null;
    has_activation_key?: boolean;
}

export interface ScienceDeviceUpsertRequest {
    device_id: string;
    name?: string | null;
    service_user_id?: string | null;
    dept_id?: number | null;
    is_active: boolean;
    kb_scope_mode: DeviceKbScopeMode;
    kb_scope_dept_ids: string[];
    kb_scope_file_ids: string[];
}

export interface ScienceDeviceActivationCode {
    code_id: string;
    name_hint?: string | null;
    activation_key_last4: string;
    status: string;
    created_at: string;
    used_at?: string | null;
    used_device_id?: string | null;
}

export interface ScienceDeviceActivationCodeCreateRequest {
    name_hint?: string | null;
}

export interface ScienceDeviceActivationCodeCreateResponse extends ScienceDeviceActivationCode {
    activation_key: string;
}

export interface ScienceWakewordConfig {
    wakeword: string;
    enabled: boolean;
    sensitivity: number;
}

export interface ScienceVoiceProfile {
    child_voice: string;
    adult_voice: string;
    elder_voice: string;
    female_voice: string;
    male_voice: string;
    default_voice: string;
    gender_confidence_threshold: number;
}

export interface ScienceQuizQuestion {
    id: string;
    question_text: string;
    options: string[];
    answer_key: string;
    explanation?: string | null;
    difficulty: number;
    source_type: 'bank' | 'generated';
    is_active: boolean;
}

export interface ScienceQuizQuestionUpsertRequest {
    question_text: string;
    options: string[];
    answer_key: string;
    explanation?: string | null;
    difficulty: number;
    source_type: 'bank' | 'generated';
    is_active: boolean;
}

export interface ScienceRewardPolicy {
    required_correct_count: number;
    period_hours: number;
    max_claims_per_period: number;
    coupon_id?: string | null;
    is_active: boolean;
}

export interface ScienceCoupon {
    id: string;
    title: string;
    description?: string | null;
    merchant_name?: string | null;
    redeem_link?: string | null;
    status: 'active' | 'inactive';
    total_stock: number;
    issued_count: number;
    redeemed_count: number;
    expires_at?: string | null;
    created_at: string;
}

export interface ScienceCouponUpsertRequest {
    title: string;
    description?: string | null;
    merchant_name?: string | null;
    redeem_link?: string | null;
    total_stock: number;
    expires_at?: string | null;
    status: 'active' | 'inactive';
}

export interface ScienceCouponIssuance {
    id: string;
    coupon_id: string;
    session_id: string;
    device_id: string;
    coupon_code: string;
    status: string;
    issued_at: string;
    expires_at?: string | null;
    redeemed_at?: string | null;
}

export interface ScienceCouponRedeemRequest {
    issuance_id?: string;
    coupon_code?: string;
    note?: string;
}

export interface ScienceCouponRedeemResponse {
    success: boolean;
    message: string;
    issuance_id?: string | null;
    coupon_code?: string | null;
    redeemed_at?: string | null;
}

export interface QuizGenerateRequest {
    topic: string;
    count: number;
    difficulty: number;
}

export interface QuizGenerateResponse {
    generated_count: number;
    questions: ScienceQuizQuestion[];
}

export interface KnowledgeScope {
    file_ids: string[];
    visibilities: string[];
    dept_ids: string[];
}
