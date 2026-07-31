export interface Source {
  file_id?: string | null;
  file_name?: string | null;
  score: number;
}

export type AgeGroup = "child" | "adult" | "elder" | "unknown";

export interface RetrievalScope {
  mode: "workspace" | "dept" | "files";
  file_ids: string[];
  dept_ids: string[];
  visibilities: string[];
  file_count: number;
  dept_count: number;
  visibility_count: number;
}

export interface DeviceActivateRequest {
  activation_key: string;
}

export interface DeviceActivateResponse {
  activated: boolean;
  device_id: string;
  device_token: string;
  workspace_id: string;
}

export interface SessionStartResponse {
  session_id: string;
  workspace_id: string;
  device_id: string;
  started_at: string;
}

export interface ProfileInferResponse {
  event_type: "profile_inferred" | string;
  trace_id: string;
  age_group: AgeGroup;
  age_confidence: number;
  gender: "female" | "male" | "unknown";
  gender_confidence: number;
  outfit_tags: string[];
  feature_tags: string[];
  persona_text: string;
  welcome_text: string;
  tts_voice: string;
  tts_audio_base64?: string | null;
  fallback: boolean;
  reason?: string;
}

export interface AsrTurnRequest {
  session_id: string;
  text: string;
  enable_tts?: boolean;
  age_group?: AgeGroup;
  gender?: "female" | "male" | "unknown";
  gender_confidence?: number;
}

export interface AsrTurnPayload {
  answer: string;
  tts_voice?: string;
  tts_audio_base64?: string | null;
}

export interface AsrTurnResponse {
  event_type: "voice_reply_ready" | string;
  session_id: string;
  trace_id: string;
  payload: AsrTurnPayload;
  sources?: Source[];
  retrieval_scope?: RetrievalScope;
}

export interface TranscribeResponse {
  code: number;
  text: string;
  msg: string;
  error_detail: string;
}

export interface QuizStartPayload {
  question_id: string;
  question_text: string;
  options: string[];
}

export interface QuizStartResponse {
  event_type: string;
  session_id: string;
  trace_id: string;
  payload: QuizStartPayload;
}

export interface QuizStartRequest {
  session_id: string;
  excluded_question_ids?: string[];
}

export interface QuizAnswerRequest {
  session_id: string;
  question_id: string;
  answer: string;
}

export type RewardState = "not_reached" | "coupon_issued" | "reached_no_coupon";
export type QuizNextAction = "next_question" | "reward_ready" | "return_to_consult";

export interface QuizAnswerResponse {
  event_type: string;
  session_id: string;
  trace_id: string;
  correct: boolean;
  correct_answer: string;
  explanation?: string;
  correct_count: number;
  required_correct_count: number;
  reward_ready: boolean;
  issuance_id?: string;
  reward_state: RewardState;
  reward_message?: string;
  reward_reason_code?: string;
  next_action: QuizNextAction;
}

export interface CouponQrResponse {
  issuance_id: string;
  coupon_code: string;
  status: string;
  expires_at?: string;
  qr_payload: string;
  qr_link?: string | null;
}
