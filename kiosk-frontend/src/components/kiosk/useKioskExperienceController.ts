import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { client } from '../../api/client';
import type { RetrievalScope, Source } from '../../api/types';
import { KIOSK_INTERACTION } from '../../config/interaction';
import { useBrowserSpeech } from '../../hooks/useBrowserSpeech';
import { useSpeech } from '../../hooks/useSpeech';
import { useTts, type TtsPlayReason } from '../../hooks/useTts';
import {
  useVisitorPresence,
  type VisitorPresenceMode,
  type VisitorPresenceStatus,
} from '../../hooks/useVisitorPresence';
import { useSessionStore, type FlowIntent } from '../../store/session';
import { getDerivedStageViewModel } from './stageViewModel';

type NoticeTone = 'default' | 'warm' | 'success' | 'danger';

type QuizFeedback = {
  msg: string;
  exp?: string;
} | null;

type RewardCardState = {
  couponCode: string;
  qrValue: string;
  qrLink: string | null;
};

type SpeechPlaybackResult = {
  played: boolean;
  source: 'tts' | 'browser' | 'none';
  reason: TtsPlayReason | 'browser_ok' | 'browser_unavailable' | 'browser_fallback_failed';
  ttsReason?: TtsPlayReason;
};

type PresenceState = {
  status: VisitorPresenceStatus;
  mode: VisitorPresenceMode;
  progress: number;
  cameraReady: boolean;
  error: string | null;
  label: string;
  tone: NoticeTone;
};

export type KioskController = {
  currentPage: ReturnType<typeof useSessionStore.getState>['currentPage'];
  intent: FlowIntent;
  sessionId: string;
  deviceId: string;
  workspaceId: string;
  statusMessage: string;
  statusTone: NoticeTone;
  countdownLabel: string | null;
  countdownValue: number | null;
  presence: PresenceState;
  viewModel: ReturnType<typeof getDerivedStageViewModel>;
  introText: string;
  personaText: string;
  featureTags: string[];
  transcriptDraft: string;
  lastUserQuestion: string;
  lastAssistantAnswer: string;
  lastSources: Source[];
  lastRetrievalScope: RetrievalScope | null;
  speechError: string | null;
  questionText: string;
  options: string[];
  quizLoading: boolean;
  selectedOpt: number | null;
  quizStatus: 'default' | 'correct' | 'wrong';
  quizFeedback: QuizFeedback;
  correctCount: number;
  requiredCount: number;
  rewardCard: RewardCardState;
  onConsult: () => void;
  onQuiz: () => void;
  onSubmitTranscript: () => void;
  onRetryListening: () => void;
  onReturnHome: () => void;
  onSelectQuizOption: (index: number) => void;
  onFinishReward: () => void;
};

const DEFAULT_ATTRACT_MESSAGE = '靠近屏幕或点击下方按钮即可开始。';
const DEFAULT_PERSONA_TEXT = '我是你的熊猫讲解员，会把复杂内容讲得更自然、更好懂。';
const DEFAULT_WELCOME_TEXT = '欢迎来到互动体验区，我已经准备好和你语音交流了。';
const QUIZ_SWITCH_COMMANDS = new Set([
  '我要玩游戏',
  '玩游戏',
  '我要去玩游戏',
  '我要答题',
  '我要去答题',
  '去答题',
  '开始答题',
  '进入答题',
  '进入答题挑战',
  '答题挑战',
  '我要闯关',
  '开始闯关',
]);
const QUIZ_ANSWER_PREFIXES = ['我选', '选项', '答案是', '答案', '选', '答'];

async function createFallbackSnapshot(): Promise<Blob> {
  return new Promise((resolve) => {
    const canvas = document.createElement('canvas');
    canvas.width = KIOSK_INTERACTION.fallbackSnapshotWidth;
    canvas.height = KIOSK_INTERACTION.fallbackSnapshotHeight;

    const context = canvas.getContext('2d');
    if (context) {
      context.fillStyle = KIOSK_INTERACTION.fallbackSnapshotBackground;
      context.fillRect(0, 0, canvas.width, canvas.height);
    }

    canvas.toBlob(
      (blob) => resolve(blob as Blob),
      KIOSK_INTERACTION.fallbackSnapshotMime,
      KIOSK_INTERACTION.fallbackSnapshotQuality,
    );
  });
}

function wait(ms: number) {
  if (ms <= 0) {
    return Promise.resolve();
  }

  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function buildIntroText(_personaText: string, welcomeText: string) {
  return welcomeText.trim() || DEFAULT_WELCOME_TEXT;
}

function formatScopeLabel(mode?: 'workspace' | 'dept' | 'files') {
  if (mode === 'dept') return '部门知识范围';
  if (mode === 'files') return '指定资料范围';
  return '全馆知识范围';
}

function toQuizAnswer(option: string, index: number): string {
  const trimmed = (option || '').trim();
  const labelMatch = trimmed.match(/^([A-Da-d])[.\s、]/);
  if (labelMatch) {
    return labelMatch[1].toUpperCase();
  }
  return String.fromCharCode(65 + index);
}

function normalizeVoiceText(value: string) {
  return value
    .toUpperCase()
    .replace(/[Ａ-Ｚ]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 65248))
    .replace(/[\s,，。！？!?.、:：；;（）()【】<>《》"'“”‘’]/g, '')
    .replace(/[[\]]/g, '');
}

function trimVoiceFiller(value: string) {
  return value
    .replace(/^(那个|这个|就是|然后|嗯嗯|嗯|呃|额)+/, '')
    .replace(/(啊|呀|吧|啦|嘛|哦|呢|哈|哇)+$/, '');
}

function detectQuizIntentCommand(transcript: string) {
  const normalized = trimVoiceFiller(normalizeVoiceText(transcript));
  if (!normalized) {
    return false;
  }

  if (/(什么|怎么|为什么|哪里|哪儿|有没有|能不能|可不可以|介绍|讲讲|看看|吗|么|呢)/.test(normalized)) {
    return false;
  }

  return QUIZ_SWITCH_COMMANDS.has(normalized);
}

function resolveQuizAnswerToken(token: string): number | null {
  switch (token) {
    case 'A':
    case '诶':
    case '哎':
    case '欸':
      return 0;
    case 'B':
    case '比':
    case '毕':
      return 1;
    case 'C':
    case '西':
    case '希':
      return 2;
    case 'D':
    case '弟':
    case '低':
    case '滴':
      return 3;
    default:
      return null;
  }
}

function extractQuizAnswerOption(transcript: string, optionCount: number): number | null {
  if (optionCount <= 0) {
    return null;
  }

  const normalized = trimVoiceFiller(normalizeVoiceText(transcript));
  if (!normalized) {
    return null;
  }

  let token = normalized;
  for (const prefix of QUIZ_ANSWER_PREFIXES) {
    if (normalized.startsWith(prefix)) {
      token = normalized.slice(prefix.length);
      break;
    }
  }

  const index = resolveQuizAnswerToken(token);
  if (index === null || index >= optionCount) {
    return null;
  }

  return index;
}

function createInitialRewardCard(): RewardCardState {
  return {
    couponCode: KIOSK_INTERACTION.rewardPlaceholderCode,
    qrValue: KIOSK_INTERACTION.rewardPlaceholderCode,
    qrLink: null,
  };
}

function buildPresenceState(params: {
  isStarting: boolean;
  statusMessage: string;
  progress: number;
  status: VisitorPresenceStatus;
  mode: VisitorPresenceMode;
  cameraReady: boolean;
  error: string | null;
}): PresenceState {
  if (params.isStarting) {
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: params.statusMessage,
      tone: 'warm',
    };
  }

  if (params.status === 'detected') {
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: `检测到有人靠近，再停留 ${((KIOSK_INTERACTION.attractPresenceConfirmationMs / 1000) * (1 - params.progress)).toFixed(1)} 秒即可自动开始。`,
      tone: 'success',
    };
  }

  if (params.status === 'confirmed') {
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: '已锁定游客，正在准备互动。',
      tone: 'success',
    };
  }

  if (params.status === 'blocked') {
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: '摄像头不可用，请直接点击咨询或答题按钮开始。',
      tone: 'danger',
    };
  }

  if (params.status === 'unsupported') {
    const isFaceDetectorMissing = params.error?.includes('face_detector_not_supported');
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: isFaceDetectorMissing
        ? '当前环境未启用本地人脸检测，请点击下方按钮开始。'
        : '当前浏览器不支持自动感应，请点击下方按钮开始。',
      tone: 'warm',
    };
  }

  if (params.cameraReady) {
    return {
      status: params.status,
      mode: params.mode,
      progress: params.progress,
      cameraReady: params.cameraReady,
      error: params.error,
      label: '站到屏幕前即可自动开始，也可以直接点击按钮。',
      tone: 'default',
    };
  }

  return {
    status: params.status,
    mode: params.mode,
    progress: params.progress,
    cameraReady: params.cameraReady,
    error: params.error,
    label: '正在准备自动感应与欢迎流程。',
    tone: 'warm',
  };
}

export function useKioskExperienceController(): KioskController {
  const {
    currentPage,
    setPage,
    intent,
    setIntent,
    sessionId,
    deviceId,
    workspaceId,
    lastUserQuestion,
    lastAssistantAnswer,
    transcriptDraft,
    pendingQuestion,
    lastTtsBase64,
    lastSources,
    lastRetrievalScope,
    currentAgeGroup,
    currentGender,
    currentGenderConfidence,
    currentFeatureTags,
    currentPersonaText,
    currentWelcomeText,
    currentIntroTtsBase64,
    correctCount,
    requiredCount,
    lastIssuanceId,
    isWelcomePlaying,
    isMicActive,
    isTtsPlaying,
    isStarting,
    setSession,
    setProfileContext,
    setTranscriptDraft,
    setPendingQuestion,
    commitPendingQuestion,
    setRuntimeFlags,
    setAssistantTurn,
    setQuizProgress,
    setReward,
    resetInteraction,
  } = useSessionStore();

  const [statusMessage, setStatusMessage] = useState(DEFAULT_ATTRACT_MESSAGE);
  const [statusTone, setStatusTone] = useState<NoticeTone>('default');
  const [browserSpeechPlaying, setBrowserSpeechPlaying] = useState(false);
  const [countdownLabel, setCountdownLabel] = useState<string | null>(null);
  const [countdownValue, setCountdownValue] = useState<number | null>(null);
  const [speechError, setSpeechError] = useState<string | null>(null);

  const [quizLoading, setQuizLoading] = useState(false);
  const [questionId, setQuestionId] = useState('');
  const [questionText, setQuestionText] = useState('');
  const [options, setOptions] = useState<string[]>([]);
  const [selectedOpt, setSelectedOpt] = useState<number | null>(null);
  const [quizStatus, setQuizStatus] = useState<'default' | 'correct' | 'wrong'>('default');
  const [quizFeedback, setQuizFeedback] = useState<QuizFeedback>(null);
  const [rewardCard, setRewardCard] = useState<RewardCardState>(createInitialRewardCard);
  const [introReplayNonce, setIntroReplayNonce] = useState(0);

  const { play: playTts, stop: stopTts, prime: primeTts, isPlaying: ttsHookPlaying } = useTts();
  const { speak: speakBrowser, stop: stopBrowserSpeech } = useBrowserSpeech();
  const {
    start: startSpeech,
    stop: stopSpeech,
    transcript,
    isListening,
    error: speechEngineError,
  } = useSpeech();

  const stageEpochRef = useRef(0);
  const startLockRef = useRef(false);
  const captureSnapshotRef = useRef<() => Promise<Blob>>(createFallbackSnapshot);
  const countdownIntervalRef = useRef<ReturnType<typeof window.setInterval> | null>(null);
  const countdownExpireRef = useRef<(() => void) | null>(null);
  const silenceTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const quizFeedbackTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const listeningTranscriptReadyRef = useRef(false);
  const quizTranscriptReadyRef = useRef(false);
  const introReplayPendingRef = useRef(false);

  const clearCountdown = useCallback(() => {
    if (countdownIntervalRef.current !== null) {
      window.clearInterval(countdownIntervalRef.current);
      countdownIntervalRef.current = null;
    }
    countdownExpireRef.current = null;
    setCountdownLabel(null);
    setCountdownValue(null);
  }, []);

  const clearSilenceTimer = useCallback(() => {
    if (silenceTimerRef.current !== null) {
      window.clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
  }, []);

  const clearQuizFeedbackTimer = useCallback(() => {
    if (quizFeedbackTimerRef.current !== null) {
      window.clearTimeout(quizFeedbackTimerRef.current);
      quizFeedbackTimerRef.current = null;
    }
  }, []);

  const startCountdown = useCallback(
    (seconds: number, label: string, onExpire: () => void) => {
      clearCountdown();
      setCountdownLabel(label);
      setCountdownValue(seconds);
      countdownExpireRef.current = onExpire;
      countdownIntervalRef.current = window.setInterval(() => {
        setCountdownValue((prev) => {
          if (prev === null) {
            return prev;
          }
          if (prev <= 1) {
            const expire = countdownExpireRef.current;
            clearCountdown();
            expire?.();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    },
    [clearCountdown],
  );

  const resetQuizLocalState = useCallback(() => {
    setQuizLoading(false);
    setQuestionId('');
    setQuestionText('');
    setOptions([]);
    setSelectedOpt(null);
    setQuizStatus('default');
    setQuizFeedback(null);
    clearQuizFeedbackTimer();
  }, [clearQuizFeedbackTimer]);

  const resetRewardLocalState = useCallback(() => {
    setRewardCard(createInitialRewardCard());
  }, []);

  const cleanupActiveEffects = useCallback(
    (options?: {
      preserveMic?: boolean;
      preserveDraft?: boolean;
      keepQuizFeedback?: boolean;
    }) => {
      stageEpochRef.current += 1;
      clearCountdown();
      clearSilenceTimer();
      clearQuizFeedbackTimer();
      stopBrowserSpeech();
      stopTts();
      setBrowserSpeechPlaying(false);

      if (!options?.preserveMic) {
        stopSpeech();
      }

      if (!options?.preserveDraft) {
        setTranscriptDraft('');
      }

      if (!options?.keepQuizFeedback) {
        setSelectedOpt(null);
        setQuizStatus('default');
        setQuizFeedback(null);
      }

      setRuntimeFlags({
        isWelcomePlaying: false,
        isMicActive: false,
        isTtsPlaying: false,
      });
      introReplayPendingRef.current = false;
    },
    [
      clearCountdown,
      clearQuizFeedbackTimer,
      clearSilenceTimer,
      setRuntimeFlags,
      setTranscriptDraft,
      stopBrowserSpeech,
      stopSpeech,
      stopTts,
    ],
  );

  const enterAttract = useCallback(
    (message = DEFAULT_ATTRACT_MESSAGE, tone: NoticeTone = 'default') => {
      cleanupActiveEffects();
      resetQuizLocalState();
      resetRewardLocalState();
      resetInteraction();
      setStatusMessage(message);
      setStatusTone(tone);
    },
    [cleanupActiveEffects, resetInteraction, resetQuizLocalState, resetRewardLocalState],
  );

  const enterListening = useCallback(
    (options?: { preserveMic?: boolean; preserveDraft?: boolean; message?: string }) => {
      if (!sessionId) {
        return;
      }

      cleanupActiveEffects({
        preserveMic: options?.preserveMic,
        preserveDraft: options?.preserveDraft,
      });
      setIntent('consult');
      setPendingQuestion('');
      setPage('Listening');
      setStatusMessage(options?.message ?? '请直接开口提问。');
      setStatusTone('success');
    },
    [cleanupActiveEffects, sessionId, setIntent, setPage, setPendingQuestion],
  );

  const enterQuiz = useCallback(
    (message = '已切换到答题挑战。') => {
      if (!sessionId) {
        return;
      }

      cleanupActiveEffects({ preserveMic: true });
      resetQuizLocalState();
      setIntent('quiz');
      setPendingQuestion('');
      setTranscriptDraft('');
      setPage('Quiz');
      setStatusMessage(message);
      setStatusTone('warm');
    },
    [
      cleanupActiveEffects,
      resetQuizLocalState,
      sessionId,
      setIntent,
      setPage,
      setPendingQuestion,
      setTranscriptDraft,
    ],
  );

  const enterThinking = useCallback(
    (question: string) => {
      const trimmedQuestion = question.trim();
      if (!trimmedQuestion) {
        enterAttract('没有捕捉到有效问题，已返回待机。', 'warm');
        return;
      }

      cleanupActiveEffects();
      setPendingQuestion(trimmedQuestion);
      commitPendingQuestion(trimmedQuestion);
      setPage('Thinking');
      setStatusMessage('问题已收到，正在整理回答。');
      setStatusTone('warm');
    },
    [cleanupActiveEffects, commitPendingQuestion, enterAttract, setPage, setPendingQuestion],
  );

  const enterAnswer = useCallback(
    (
      answer: string,
      ttsAudio: string | null,
      sources: Source[] = [],
      retrievalScope: RetrievalScope | null = null,
    ) => {
      cleanupActiveEffects();
      setAssistantTurn(answer, ttsAudio, sources, retrievalScope);
      setPage('Answer');
      setStatusMessage('熊猫已经准备好为你讲解。');
      setStatusTone('success');
    },
    [cleanupActiveEffects, setAssistantTurn, setPage],
  );

  const enterReward = useCallback(
    (issuanceId: string) => {
      cleanupActiveEffects();
      setReward(issuanceId);
      setPage('Reward');
      setStatusMessage('闯关成功，奖励已解锁。');
      setStatusTone('success');
    },
    [cleanupActiveEffects, setPage, setReward],
  );

  const beginStartFlow = useCallback(
    async (nextIntent: FlowIntent, source: 'button' | 'presence') => {
      if (startLockRef.current) {
        return;
      }

      startLockRef.current = true;
      cleanupActiveEffects();
      resetQuizLocalState();
      resetRewardLocalState();
      setIntent(nextIntent);
      setSpeechError(null);
      setStatusTone('warm');
      setStatusMessage(
        source === 'presence'
          ? '检测到游客靠近，正在打开互动。'
          : nextIntent === 'quiz'
            ? '正在为你准备答题挑战。'
            : '正在唤醒熊猫讲解员。',
      );
      // 在 setRuntimeFlags 之前提前截图，避免 isStarting=true 触发 cleanup() 导致 videoRef 被置空
      let preCapture: Blob | null = null;
      try {
        preCapture = await captureSnapshotRef.current();
      } catch {
        preCapture = null;
      }

      setRuntimeFlags({ isStarting: true, startFlowLock: true });

      const flowEpoch = stageEpochRef.current;

      try {
        const session = await client.startSession();
        if (flowEpoch !== stageEpochRef.current) {
          return;
        }

        setSession({
          sessionId: session.session_id,
          deviceId: session.device_id,
          workspaceId: session.workspace_id,
        });

        const imageBlob: Blob = preCapture ?? (await createFallbackSnapshot());

        setStatusMessage('正在观察你的样子，准备专属问候。');

        try {
          const profile = await client.profileInfer({
            sessionId: session.session_id,
            imageBlob,
            enableTts: true,
          });

          if (flowEpoch !== stageEpochRef.current) {
            return;
          }

          setProfileContext({
            ageGroup: profile.age_group,
            gender: profile.gender,
            confidence: profile.gender_confidence,
            featureTags: profile.outfit_tags.length ? profile.outfit_tags : profile.feature_tags,
            personaText: profile.persona_text,
            welcomeText: profile.welcome_text,
            introTtsBase64: profile.tts_audio_base64 ?? null,
          });
          console.info('[KioskIntro] profileInfer done', {
            sessionId: session.session_id,
            traceId: profile.trace_id,
            fallback: profile.fallback,
            hasTts: Boolean(profile.tts_audio_base64),
          });
        } catch (error) {
          console.error('profileInfer failed:', error);
          if (flowEpoch !== stageEpochRef.current) {
            return;
          }

          setProfileContext({
            ageGroup: 'unknown',
            gender: 'unknown',
            confidence: 0,
            featureTags: ['语音问答', '知识讲解', '互动体验'],
            personaText: DEFAULT_PERSONA_TEXT,
            welcomeText: DEFAULT_WELCOME_TEXT,
            introTtsBase64: null,
          });
          console.warn('[KioskIntro] profileInfer fallback', {
            sessionId: session.session_id,
          });
        }

        if (flowEpoch !== stageEpochRef.current) {
          return;
        }

        setStatusMessage('熊猫已经准备好，正在欢迎你。');
        console.info('[KioskIntro] setPage PersonaIntro', {
          sessionId: session.session_id,
          intent: nextIntent,
        });
        setPage('PersonaIntro');
      } catch (error) {
        console.error('start flow failed:', error);
        enterAttract('启动失败，请稍后再试。', 'danger');
      } finally {
        startLockRef.current = false;
        setRuntimeFlags({ isStarting: false, startFlowLock: false });
      }
    },
    [
      cleanupActiveEffects,
      enterAttract,
      resetQuizLocalState,
      resetRewardLocalState,
      setIntent,
      setPage,
      setProfileContext,
      setRuntimeFlags,
      setSession,
    ],
  );

  const visitorPresence = useVisitorPresence({
    enabled: currentPage === 'Attract' && !isStarting,
    confirmationMs: KIOSK_INTERACTION.attractPresenceConfirmationMs,
    detectIntervalMs: KIOSK_INTERACTION.attractPresenceDetectIntervalMs,
    onConfirmed: () => {
      void beginStartFlow('consult', 'presence');
    },
  });

  useEffect(() => {
    captureSnapshotRef.current = visitorPresence.captureSnapshot;
  }, [visitorPresence.captureSnapshot]);

  // 全局持续监听用户手势，随时 prime AudioContext。
  // 必须持续监听而不是 once，因为每次 session 都需要重新解锁。
  useEffect(() => {
    const handler = () => {
      primeTts();

      if (currentPage !== 'PersonaIntro' || !introReplayPendingRef.current) {
        return;
      }

      introReplayPendingRef.current = false;
      setStatusMessage('已检测到触屏，正在重播欢迎语。');
      setStatusTone('warm');
      console.info('[KioskIntro] replay intro after user gesture');
      setIntroReplayNonce((value) => value + 1);
    };
    document.addEventListener('click', handler, { passive: true });
    document.addEventListener('touchstart', handler, { passive: true });
    return () => {
      document.removeEventListener('click', handler);
      document.removeEventListener('touchstart', handler);
    };
  }, [currentPage, primeTts]);

  const playSpeech = useCallback(
    async (ttsBase64: string | null, text: string) => {
      if (ttsBase64) {
        const ttsResult = await playTts(ttsBase64);
        if (ttsResult.played) {
          return {
            played: true,
            source: 'tts',
            reason: ttsResult.reason,
          } satisfies SpeechPlaybackResult;
        }

        if (ttsResult.reason === 'stopped') {
          return {
            played: false,
            source: 'none',
            reason: ttsResult.reason,
            ttsReason: ttsResult.reason,
          } satisfies SpeechPlaybackResult;
        }

        setBrowserSpeechPlaying(true);
        try {
          const browserPlayed = await speakBrowser(text);
          if (browserPlayed) {
            return {
              played: true,
              source: 'browser',
              reason: 'browser_ok',
              ttsReason: ttsResult.reason,
            } satisfies SpeechPlaybackResult;
          }

          return {
            played: false,
            source: 'none',
            reason: 'browser_fallback_failed',
            ttsReason: ttsResult.reason,
          } satisfies SpeechPlaybackResult;
        } finally {
          setBrowserSpeechPlaying(false);
        }
      }

      setBrowserSpeechPlaying(true);
      try {
        const browserPlayed = await speakBrowser(text);
        return browserPlayed
          ? ({
              played: true,
              source: 'browser',
              reason: 'browser_ok',
            } satisfies SpeechPlaybackResult)
          : ({
              played: false,
              source: 'none',
              reason: 'browser_unavailable',
            } satisfies SpeechPlaybackResult);
      } finally {
        setBrowserSpeechPlaying(false);
      }
    },
    [playTts, speakBrowser],
  );

  const submitTranscript = useCallback(() => {
    const question = (transcript || transcriptDraft).trim();
    enterThinking(question);
  }, [enterThinking, transcript, transcriptDraft]);

  const fetchQuizQuestion = useCallback(async (excludedQuestionId?: string) => {
    if (!sessionId) {
      return;
    }

    const effectEpoch = stageEpochRef.current;
    resetQuizLocalState();
    setQuizLoading(true);

    try {
      let response = await client.quizStart({
        session_id: sessionId,
        excluded_question_ids: excludedQuestionId ? [excludedQuestionId] : [],
      });
      if (
        excludedQuestionId
        && response.payload.question_id === excludedQuestionId
      ) {
        response = await client.quizStart({
          session_id: sessionId,
          excluded_question_ids: [excludedQuestionId],
        });
      }
      if (effectEpoch !== stageEpochRef.current) {
        return;
      }

      setQuestionId(response.payload.question_id);
      setQuestionText(response.payload.question_text);
      setOptions(response.payload.options);
    } catch (error) {
      console.error('quizStart failed:', error);
      if (effectEpoch !== stageEpochRef.current) {
        return;
      }

      const message = error instanceof Error ? error.message : '';
      setQuizFeedback({
        msg: message.includes('quiz_question_bank_empty')
          ? '当前题库暂无可用题目，请先补充题库。'
          : '获取题目失败，请稍后重试。',
      });
      setQuizStatus('wrong');
    } finally {
      if (effectEpoch === stageEpochRef.current) {
        setQuizLoading(false);
      }
    }
  }, [resetQuizLocalState, sessionId]);

  const fetchQuizQuestionRef = useRef(fetchQuizQuestion);

  useEffect(() => {
    fetchQuizQuestionRef.current = fetchQuizQuestion;
  }, [fetchQuizQuestion]);

  const handleConsult = useCallback(() => {
    primeTts();
    if (currentPage === 'Attract' || !sessionId) {
      void beginStartFlow('consult', 'button');
      return;
    }

    enterListening({
      preserveMic: currentPage === 'PersonaIntro' || currentPage === 'Listening',
      preserveDraft: currentPage === 'PersonaIntro' || currentPage === 'Listening',
      message:
        currentPage === 'PersonaIntro'
          ? '已打断欢迎语，开始听你提问。'
          : '继续咨询模式，请直接开口。',
    });
  }, [beginStartFlow, currentPage, enterListening, primeTts, sessionId]);

  const handleQuiz = useCallback(() => {
    primeTts();
    if (currentPage === 'Attract' || !sessionId) {
      void beginStartFlow('quiz', 'button');
      return;
    }

    enterQuiz(
      currentPage === 'PersonaIntro'
        ? '已打断欢迎语，直接进入答题挑战。'
        : '已切换到答题挑战。',
    );
  }, [beginStartFlow, currentPage, enterQuiz, primeTts, sessionId]);

  const handleRetryListening = useCallback(() => {
    setSpeechError(null);
    enterListening({ message: '已重新打开语音识别，请继续提问。' });
  }, [enterListening]);

  const handleReturnHome = useCallback(() => {
    enterAttract('已返回待机接待页。');
  }, [enterAttract]);

  const handleSelectQuizOption = useCallback(
    async (index: number) => {
      if (quizLoading || !sessionId || !questionId || selectedOpt !== null) {
        return;
      }

      setSelectedOpt(index);
      setQuizLoading(true);

      try {
        const selectedText = options[index];
        const response = await client.quizAnswer({
          session_id: sessionId,
          question_id: questionId,
          answer: toQuizAnswer(selectedText, index),
        });

        setQuizProgress(response.correct_count, response.required_correct_count);

        if (response.correct) {
          setQuizStatus('correct');
          setQuizFeedback(
            response.next_action === 'reward_ready'
              ? { msg: '回答正确，奖励已解锁。' }
              : response.next_action === 'return_to_consult'
                ? {
                    msg: '回答正确，本题挑战完成。',
                    exp: response.reward_message || '欢迎继续提问，也可以再次进入答题挑战。',
                  }
                : { msg: '回答正确，正在准备下一题。' },
          );
        } else {
          setQuizStatus('wrong');
          setQuizFeedback({
            msg: '这题答错了。',
            exp: `正确答案：${response.correct_answer}${response.explanation ? `\n${response.explanation}` : ''}`,
          });
        }

        clearQuizFeedbackTimer();
        quizFeedbackTimerRef.current = window.setTimeout(() => {
          if (response.next_action === 'reward_ready') {
            if (response.issuance_id) {
              enterReward(response.issuance_id);
              return;
            }

            enterListening({
              message: '奖励暂时还没准备好，你可以继续提问或稍后再试答题。',
            });
            return;
          }

          if (response.next_action === 'return_to_consult') {
            enterListening({
              message:
                response.reward_message || '答题环节已结束，请继续提问，也可以稍后再玩一轮。',
            });
            return;
          }

          void fetchQuizQuestionRef.current(questionId);
        }, 2200);
      } catch (error) {
        console.error('quizAnswer failed:', error);
        setQuizStatus('wrong');
        setQuizFeedback({ msg: '系统暂时不可用，请稍后再试。' });
        setQuizLoading(false);
      }
    },
    [
      clearQuizFeedbackTimer,
      enterListening,
      enterReward,
      options,
      questionId,
      quizLoading,
      selectedOpt,
      sessionId,
      setQuizProgress,
    ],
  );

  useEffect(() => {
    return () => {
      cleanupActiveEffects();
    };
  }, [cleanupActiveEffects]);

  useEffect(() => {
    setRuntimeFlags({
      isMicActive: isListening,
    });
  }, [isListening, setRuntimeFlags]);

  useEffect(() => {
    setRuntimeFlags({
      isTtsPlaying: ttsHookPlaying || browserSpeechPlaying,
    });
  }, [browserSpeechPlaying, setRuntimeFlags, ttsHookPlaying]);

  useEffect(() => {
    if (currentPage === 'Listening' || currentPage === 'PersonaIntro') {
      setTranscriptDraft(transcript);
    }
  }, [currentPage, setTranscriptDraft, transcript]);

  useEffect(() => {
    if (!speechEngineError) {
      return;
    }

    setSpeechError(speechEngineError);
    if (currentPage === 'Listening') {
      setStatusMessage('语音识别不可用，你可以重试或切去答题。');
      setStatusTone('danger');
    } else if (currentPage === 'Quiz') {
      setStatusMessage('语音识别不可用，你也可以直接点击作答。');
      setStatusTone('danger');
    }
  }, [currentPage, speechEngineError]);

  useEffect(() => {
    if (currentPage !== 'PersonaIntro') {
      return;
    }

    const introText = buildIntroText(currentPersonaText, currentWelcomeText);
    const effectEpoch = stageEpochRef.current;
    const introStartedAt = Date.now();
    let cancelled = false;
    introReplayPendingRef.current = false;

    setStatusMessage(intent === 'quiz' ? '欢迎后将自动进入答题挑战。' : '欢迎后将自动进入咨询模式。');
    setStatusTone('warm');
    setRuntimeFlags({ isWelcomePlaying: true });

    async function runIntro() {
      let nextStage: 'quiz' | 'consult' | null = null;
      let introPlaybackResult: SpeechPlaybackResult = {
        played: false,
        source: 'none',
        reason: 'browser_unavailable',
      };

      try {
        console.info('[KioskIntro] intro play start', {
          intent,
          hasTts: Boolean(currentIntroTtsBase64),
        });
        introPlaybackResult = await playSpeech(currentIntroTtsBase64, introText);
        if (introPlaybackResult.source === 'tts') {
          console.info('[KioskIntro] tts played', {
            reason: introPlaybackResult.reason,
          });
        } else if (introPlaybackResult.source === 'browser') {
          console.info('[KioskIntro] tts fallback', {
            reason: introPlaybackResult.reason,
            ttsReason: introPlaybackResult.ttsReason,
          });
        } else {
          console.warn('[KioskIntro] intro play failed', {
            reason: introPlaybackResult.reason,
            ttsReason: introPlaybackResult.ttsReason,
          });
        }
        await wait(
          KIOSK_INTERACTION.personaIntroMinDurationMs - (Date.now() - introStartedAt),
        );
      } finally {
        setBrowserSpeechPlaying(false);
        setRuntimeFlags({ isWelcomePlaying: false });

        if (!cancelled && effectEpoch === stageEpochRef.current && introPlaybackResult.played) {
          nextStage = intent === 'quiz' ? 'quiz' : 'consult';
        } else if (!cancelled && effectEpoch === stageEpochRef.current) {
          introReplayPendingRef.current = true;
          setStatusMessage('欢迎语未成功播出，请点击屏幕继续。');
          setStatusTone('danger');
        }

        console.info('[KioskIntro] intro finished', {
          played: introPlaybackResult.played,
          source: introPlaybackResult.source,
          reason: introPlaybackResult.reason,
          nextStage,
        });
      }

      if (nextStage === 'quiz') {
        console.info('[KioskIntro] enterQuiz after intro');
        enterQuiz('欢迎结束，已进入答题挑战。');
      } else if (nextStage === 'consult') {
        console.info('[KioskIntro] enterListening after intro');
        enterListening({
          message: '欢迎结束，请直接提问。',
        });
      }
    }

    void runIntro();

    return () => {
      cancelled = true;
      setBrowserSpeechPlaying(false);
      setRuntimeFlags({ isWelcomePlaying: false });
    };
  }, [
    currentIntroTtsBase64,
    currentPage,
    currentPersonaText,
    currentWelcomeText,
    enterListening,
    enterQuiz,
    intent,
    introReplayNonce,
    playSpeech,
    setRuntimeFlags,
  ]);

  useEffect(() => {
    if (currentPage !== 'Listening') {
      listeningTranscriptReadyRef.current = false;
      clearSilenceTimer();
      return;
    }

    if (!isListening && !speechEngineError) {
      listeningTranscriptReadyRef.current = false;
      startSpeech();
    }

    setStatusMessage('请直接开口提问，也可以说“我要玩游戏”进入答题。');
    setStatusTone('success');
    startCountdown(KIOSK_INTERACTION.listeningCountdownSeconds, '自动提交', submitTranscript);

    return () => {
      clearSilenceTimer();
      clearCountdown();
    };
  }, [
    clearCountdown,
    clearSilenceTimer,
    currentPage,
    isListening,
    speechEngineError,
    startCountdown,
    startSpeech,
    submitTranscript,
  ]);

  useEffect(() => {
    if (currentPage !== 'Listening') {
      listeningTranscriptReadyRef.current = false;
      return;
    }

    if (!listeningTranscriptReadyRef.current && transcript.trim() === '') {
      listeningTranscriptReadyRef.current = true;
    }
  }, [currentPage, transcript]);

  useEffect(() => {
    if (currentPage !== 'Listening' || !listeningTranscriptReadyRef.current) {
      return;
    }

    if (!detectQuizIntentCommand(transcript)) {
      return;
    }

    enterQuiz('已收到答题指令，正在进入答题挑战。');
  }, [currentPage, enterQuiz, transcript]);

  useEffect(() => {
    if (currentPage !== 'Listening') {
      return;
    }

    clearSilenceTimer();
    if (transcript.trim().length > 0) {
      silenceTimerRef.current = window.setTimeout(() => {
        submitTranscript();
      }, KIOSK_INTERACTION.listeningSilenceTimeoutMs);
    }
  }, [clearSilenceTimer, currentPage, submitTranscript, transcript]);

  useEffect(() => {
    if (currentPage !== 'Thinking') {
      return;
    }

    const question = (pendingQuestion || lastUserQuestion).trim();
    if (!question || !sessionId) {
      enterAttract('当前没有可处理的问题，已返回待机。', 'warm');
      return;
    }

    const effectEpoch = stageEpochRef.current;
    let active = true;

    setStatusMessage('正在检索知识并组织回答。');
    setStatusTone('warm');

    async function runTurn() {
      try {
        const result = await client.asrTurn({
          session_id: sessionId,
          text: question,
          enable_tts: true,
          age_group: currentAgeGroup,
          gender: currentGender,
          gender_confidence: currentGenderConfidence,
        });

        if (!active || effectEpoch !== stageEpochRef.current) {
          return;
        }

        enterAnswer(
          result.payload?.answer || '我暂时没有找到合适答案。',
          result.payload?.tts_audio_base64 || null,
          result.sources || [],
          result.retrieval_scope || null,
        );
      } catch (error) {
        console.error('asrTurn failed:', error);
        if (!active || effectEpoch !== stageEpochRef.current) {
          return;
        }

        enterAnswer('我现在有点忙，请稍后再试一次。', null, [], null);
      }
    }

    void runTurn();

    return () => {
      active = false;
    };
  }, [
    currentAgeGroup,
    currentGender,
    currentGenderConfidence,
    currentPage,
    enterAnswer,
    enterAttract,
    lastUserQuestion,
    pendingQuestion,
    sessionId,
  ]);

  useEffect(() => {
    if (currentPage !== 'Answer') {
      return;
    }

    const answerText = lastAssistantAnswer.trim() || '我暂时没有找到答案。';
    const resumeListeningMessage = '讲解完成，请继续提问，也可以说“我要玩游戏”进入答题。';
    const effectEpoch = stageEpochRef.current;
    let active = true;

    startCountdown(
      KIOSK_INTERACTION.answerAutoReturnSeconds,
      '恢复收听',
      () => enterListening({ message: resumeListeningMessage }),
    );
    setStatusMessage('熊猫正在为你播报答案。');
    setStatusTone('success');

    async function playAnswer() {
      try {
        await playSpeech(lastTtsBase64, answerText);
      } finally {
        setBrowserSpeechPlaying(false);
        if (active && effectEpoch === stageEpochRef.current) {
          enterListening({ message: resumeListeningMessage });
        }
      }
    }

    void playAnswer();

    return () => {
      active = false;
      clearCountdown();
      setBrowserSpeechPlaying(false);
    };
  }, [
    clearCountdown,
    currentPage,
    enterListening,
    lastAssistantAnswer,
    lastTtsBase64,
    playSpeech,
    startCountdown,
  ]);

  useEffect(() => {
    if (currentPage !== 'Quiz') {
      quizTranscriptReadyRef.current = false;
      return;
    }

    setStatusMessage('请说出选项字母（A/B/C/D）或点击作答。');
    setStatusTone('warm');
    void fetchQuizQuestion();
  }, [currentPage, fetchQuizQuestion]);

  useEffect(() => {
    if (currentPage !== 'Quiz') {
      quizTranscriptReadyRef.current = false;
      return;
    }

    return () => {
      quizTranscriptReadyRef.current = false;
      stopSpeech();
    };
  }, [currentPage, stopSpeech]);

  useEffect(() => {
    if (currentPage !== 'Quiz' || speechEngineError || isListening) {
      return;
    }

    quizTranscriptReadyRef.current = false;
    startSpeech();
  }, [currentPage, isListening, speechEngineError, startSpeech]);

  useEffect(() => {
    if (currentPage !== 'Quiz' || !questionId || speechEngineError) {
      return;
    }

    quizTranscriptReadyRef.current = false;
    startSpeech();
  }, [currentPage, questionId, speechEngineError, startSpeech]);

  useEffect(() => {
    if (currentPage !== 'Quiz') {
      quizTranscriptReadyRef.current = false;
      return;
    }

    if (!quizTranscriptReadyRef.current && transcript.trim() === '') {
      quizTranscriptReadyRef.current = true;
    }
  }, [currentPage, transcript]);

  useEffect(() => {
    if (currentPage !== 'Quiz' || !quizTranscriptReadyRef.current) {
      return;
    }

    if (quizLoading || selectedOpt !== null || !questionText || options.length === 0) {
      return;
    }

    const optionIndex = extractQuizAnswerOption(transcript, options.length);
    if (optionIndex === null) {
      return;
    }

    void handleSelectQuizOption(optionIndex);
  }, [
    currentPage,
    handleSelectQuizOption,
    options.length,
    questionText,
    quizLoading,
    selectedOpt,
    transcript,
  ]);

  useEffect(() => {
    if (currentPage !== 'Reward') {
      return;
    }

    const issuanceId = lastIssuanceId;
    const effectEpoch = stageEpochRef.current;
    let active = true;
    let countdownStarted = false;

    const startRewardCountdown = () => {
      if (countdownStarted || !active || effectEpoch !== stageEpochRef.current) {
        return;
      }
      countdownStarted = true;
      startCountdown(
        KIOSK_INTERACTION.rewardAutoReturnSeconds,
        '回到待机',
        () => enterAttract('奖励展示结束，已回到待机页。'),
      );
    };

    async function loadCoupon() {
      if (!issuanceId) {
        startRewardCountdown();
        return;
      }

      try {
        const payload = await client.couponQrcode(issuanceId);
        if (!active || effectEpoch !== stageEpochRef.current) {
          return;
        }

        setRewardCard({
          couponCode: payload.coupon_code || KIOSK_INTERACTION.rewardPlaceholderCode,
          qrValue:
            payload.qr_link ||
            payload.qr_payload ||
            payload.coupon_code ||
            KIOSK_INTERACTION.rewardPlaceholderCode,
          qrLink: payload.qr_link || null,
        });
        setStatusMessage('请先扫码领取奖励，页面会保留足够时间。');
        setStatusTone('success');
      } catch (error) {
        console.error('couponQrcode failed:', error);
        if (!active || effectEpoch !== stageEpochRef.current) {
          return;
        }

        setRewardCard({
          couponCode: '奖励信息加载失败',
          qrValue: issuanceId || KIOSK_INTERACTION.rewardPlaceholderCode,
          qrLink: null,
        });
        setStatusMessage('奖励信息已展示，请拍照或记录券码后再离开。');
        setStatusTone('warm');
      } finally {
        startRewardCountdown();
      }
    }

    void loadCoupon();

    return () => {
      active = false;
      countdownStarted = true;
      clearCountdown();
    };
  }, [
    clearCountdown,
    currentPage,
    enterAttract,
    lastIssuanceId,
    setStatusMessage,
    setStatusTone,
    startCountdown,
  ]);

  const introText = useMemo(
    () => buildIntroText(currentPersonaText, currentWelcomeText),
    [currentPersonaText, currentWelcomeText],
  );

  const presence = useMemo(
    () =>
      buildPresenceState({
        isStarting,
        statusMessage,
        progress: visitorPresence.progress,
        status: visitorPresence.status,
        mode: visitorPresence.mode,
        cameraReady: visitorPresence.cameraReady,
        error: visitorPresence.error,
      }),
    [
      isStarting,
      statusMessage,
      visitorPresence.cameraReady,
      visitorPresence.error,
      visitorPresence.mode,
      visitorPresence.progress,
      visitorPresence.status,
    ],
  );

  const viewModel = useMemo(
    () =>
      getDerivedStageViewModel(currentPage, intent, {
        isWelcomePlaying,
        isMicActive,
        isTtsPlaying,
        isStarting,
        countdown: countdownValue,
        hasReward: Boolean(lastIssuanceId),
        transcriptDraft,
      }),
    [
      countdownValue,
      currentPage,
      intent,
      isMicActive,
      isStarting,
      isTtsPlaying,
      isWelcomePlaying,
      lastIssuanceId,
      transcriptDraft,
    ],
  );

  return {
    currentPage,
    intent,
    sessionId,
    deviceId,
    workspaceId,
    statusMessage,
    statusTone,
    countdownLabel,
    countdownValue,
    presence,
    viewModel,
    introText,
    personaText: currentPersonaText || DEFAULT_PERSONA_TEXT,
    featureTags: currentFeatureTags.length ? currentFeatureTags : ['知识讲解', '语音问答', '答题挑战'],
    transcriptDraft,
    lastUserQuestion,
    lastAssistantAnswer,
    lastSources,
    lastRetrievalScope,
    speechError,
    questionText,
    options,
    quizLoading,
    selectedOpt,
    quizStatus,
    quizFeedback,
    correctCount,
    requiredCount,
    rewardCard,
    onConsult: handleConsult,
    onQuiz: handleQuiz,
    onSubmitTranscript: submitTranscript,
    onRetryListening: handleRetryListening,
    onReturnHome: handleReturnHome,
    onSelectQuizOption: handleSelectQuizOption,
    onFinishReward: () => enterAttract('奖励领取流程已结束。'),
  };
}

export { formatScopeLabel };
