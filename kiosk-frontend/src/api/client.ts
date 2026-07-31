import { API_CONFIG, getDeviceToken } from "./config";
import type * as Types from "./types";

class ExperienceApiClient {
  private buildHeaders(headers?: HeadersInit, auth = true): Headers {
    const mergedHeaders = new Headers(headers);
    if (auth) {
      const token = getDeviceToken();
      if (!token) {
        throw new Error("device_token_missing");
      }
      mergedHeaders.set("X-Device-Token", token);
    }
    return mergedHeaders;
  }

  private async fetchOrThrow(
    path: string,
    init: RequestInit = {},
    auth = true,
  ): Promise<Response> {
    const response = await fetch(`${API_CONFIG.baseUrl}${path}`, {
      ...init,
      headers: this.buildHeaders(init.headers, auth),
    });
    if (!response.ok) {
      const txt = await response.text();
      throw new Error(`API Error [${response.status}]: ${txt}`);
    }
    return response;
  }

  private async requestJson<T>(
    path: string,
    init: RequestInit = {},
    auth = true,
  ): Promise<T> {
    const response = await this.fetchOrThrow(path, init, auth);
    return response.json() as Promise<T>;
  }

  private postJson<T>(path: string, body: unknown, auth = true): Promise<T> {
    return this.requestJson<T>(
      path,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      auth,
    );
  }

  private postForm<T>(path: string, body: FormData, auth = true): Promise<T> {
    return this.requestJson<T>(
      path,
      {
        method: "POST",
        body,
      },
      auth,
    );
  }

  private getJson<T>(path: string, auth = true): Promise<T> {
    return this.requestJson<T>(
      path,
      {
        method: "GET",
      },
      auth,
    );
  }

  public activateDevice(req: Types.DeviceActivateRequest): Promise<Types.DeviceActivateResponse> {
    return this.postJson<Types.DeviceActivateResponse>("/device/activate", req, false);
  }

  public startSession(visitorId?: string): Promise<Types.SessionStartResponse> {
    return this.postJson<Types.SessionStartResponse>("/session/start", {
      visitor_id: visitorId,
    });
  }

  public profileInfer(params: {
    imageBlob: Blob;
    sessionId?: string;
    enableTts?: boolean;
  }): Promise<Types.ProfileInferResponse> {
    const formData = new FormData();
    if (params.sessionId) {
      formData.append("session_id", params.sessionId);
    }
    formData.append("enable_tts", String(params.enableTts ?? false));
    formData.append("image", params.imageBlob, "snapshot.jpg");
    return this.postForm<Types.ProfileInferResponse>("/profile/infer", formData);
  }

  public asrTurn(req: Types.AsrTurnRequest): Promise<Types.AsrTurnResponse> {
    return this.postJson<Types.AsrTurnResponse>("/asr-turn", req);
  }

  public transcribe(audioBlob: Blob): Promise<Types.TranscribeResponse> {
    const formData = new FormData();
    formData.append("audio", audioBlob, "recording.wav");
    return this.postForm<Types.TranscribeResponse>("/speech/transcribe", formData);
  }

  public quizStart(req: Types.QuizStartRequest): Promise<Types.QuizStartResponse> {
    return this.postJson<Types.QuizStartResponse>("/quiz/start", req);
  }

  public quizAnswer(req: Types.QuizAnswerRequest): Promise<Types.QuizAnswerResponse> {
    return this.postJson<Types.QuizAnswerResponse>("/quiz/answer", req);
  }

  public couponQrcode(issuanceId: string): Promise<Types.CouponQrResponse> {
    return this.getJson<Types.CouponQrResponse>(
      `/coupon/qrcode/${encodeURIComponent(issuanceId)}`,
    );
  }
}

export const client = new ExperienceApiClient();
