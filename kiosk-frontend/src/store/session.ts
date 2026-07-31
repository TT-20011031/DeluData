import { create } from 'zustand';
import type { AgeGroup, RetrievalScope, Source } from '../api/types';

export type PageName =
    | 'Activation'
    | 'Attract'
    | 'PersonaIntro'
    | 'Listening'
    | 'Thinking'
    | 'Answer'
    | 'Quiz'
    | 'Reward';

export type FlowIntent = 'consult' | 'quiz';

type RuntimeFlags = Pick<
    SessionState,
    'isWelcomePlaying' | 'isMicActive' | 'isTtsPlaying' | 'isStarting' | 'startFlowLock'
>;

interface SessionState {
    currentPage: PageName;
    intent: FlowIntent;

    // Session Data
    sessionId: string;
    deviceId: string;
    workspaceId: string;

    // Chat Data
    lastUserQuestion: string;
    lastAssistantAnswer: string;
    transcriptDraft: string;
    pendingQuestion: string;
    lastTtsBase64: string | null;
    lastSources: Source[];
    lastRetrievalScope: RetrievalScope | null;

    // User input context
    currentAgeGroup?: AgeGroup;
    currentGender?: 'female' | 'male' | 'unknown';
    currentGenderConfidence?: number;
    currentFeatureTags: string[];
    currentPersonaText: string;
    currentWelcomeText: string;
    currentIntroTtsBase64: string | null;

    // Game/Quiz Data
    correctCount: number;
    requiredCount: number;
    lastIssuanceId: string | null;

    // Runtime Flags
    isWelcomePlaying: boolean;
    isMicActive: boolean;
    isTtsPlaying: boolean;
    isStarting: boolean;
    startFlowLock: boolean;

    // Actions
    setPage: (page: PageName) => void;
    setIntent: (intent: FlowIntent) => void;
    setSession: (params: { sessionId: string; deviceId: string; workspaceId: string }) => void;
    setProfileContext: (params: {
        ageGroup?: AgeGroup;
        gender?: 'female' | 'male' | 'unknown';
        confidence?: number;
        featureTags?: string[];
        personaText?: string;
        welcomeText?: string;
        introTtsBase64?: string | null;
    }) => void;
    setTranscriptDraft: (text: string) => void;
    setPendingQuestion: (text: string) => void;
    commitPendingQuestion: (text?: string) => void;
    setRuntimeFlags: (params: Partial<RuntimeFlags>) => void;
    setAssistantTurn: (
        answer: string,
        tts: string | null,
        sources?: Source[],
        retrievalScope?: RetrievalScope | null,
    ) => void;
    setQuizProgress: (correct: number, required: number) => void;
    setReward: (issuanceId: string) => void;
    resetInteraction: () => void;
    resetSession: () => void;
}

const initialState = {
    currentPage: 'Activation' as PageName,
    intent: 'consult' as FlowIntent,
    sessionId: '',
    deviceId: '',
    workspaceId: '',
    lastUserQuestion: '',
    lastAssistantAnswer: '',
    transcriptDraft: '',
    pendingQuestion: '',
    lastTtsBase64: null,
    lastSources: [],
    lastRetrievalScope: null,
    correctCount: 0,
    requiredCount: 3,
    lastIssuanceId: null,
    currentAgeGroup: undefined,
    currentGender: undefined,
    currentGenderConfidence: undefined,
    currentFeatureTags: [],
    currentPersonaText: '',
    currentWelcomeText: '',
    currentIntroTtsBase64: null,
    isWelcomePlaying: false,
    isMicActive: false,
    isTtsPlaying: false,
    isStarting: false,
    startFlowLock: false,
};

export const useSessionStore = create<SessionState>((set) => ({
    ...initialState,

    setPage: (page) => set({ currentPage: page }),

    setIntent: (intent) => set({ intent }),

    setSession: ({ sessionId, deviceId, workspaceId }) =>
        set({ sessionId, deviceId, workspaceId }),

    setProfileContext: ({
        ageGroup,
        gender,
        confidence,
        featureTags = [],
        personaText = '',
        welcomeText = '',
        introTtsBase64 = null,
    }) =>
        set({
            currentAgeGroup: ageGroup,
            currentGender: gender,
            currentGenderConfidence: confidence,
            currentFeatureTags: featureTags,
            currentPersonaText: personaText,
            currentWelcomeText: welcomeText,
            currentIntroTtsBase64: introTtsBase64,
        }),

    setTranscriptDraft: (text) => set({ transcriptDraft: text }),

    setPendingQuestion: (text) => set({ pendingQuestion: text }),

    commitPendingQuestion: (text) =>
        set((state) => {
            const question = (text ?? state.pendingQuestion).trim();
            return {
                pendingQuestion: question,
                lastUserQuestion: question,
                transcriptDraft: '',
            };
        }),

    setRuntimeFlags: (params) => set(params),

    setAssistantTurn: (answer, tts, sources = [], retrievalScope = null) =>
        set({
            lastAssistantAnswer: answer,
            lastTtsBase64: tts,
            lastSources: sources,
            lastRetrievalScope: retrievalScope,
        }),

    setQuizProgress: (correct, required) =>
        set({ correctCount: correct, requiredCount: required }),

    setReward: (issuanceId) =>
        set({ lastIssuanceId: issuanceId }),

    resetInteraction: () =>
        set({
            ...initialState,
            currentPage: 'Attract',
        }),

    resetSession: () =>
        set({
            ...initialState,
            currentPage: 'Activation',
        }),
}));
