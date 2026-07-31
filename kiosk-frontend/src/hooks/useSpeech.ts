import { useCallback, useRef, useState } from 'react';

type SpeechRecognitionErrorType =
  | 'not-allowed'
  | 'service-not-allowed'
  | 'network'
  | string;

interface BrowserSpeechRecognitionAlternative {
  transcript: string;
}

interface BrowserSpeechRecognitionResult {
  isFinal: boolean;
  length: number;
  [index: number]: BrowserSpeechRecognitionAlternative;
}

interface BrowserSpeechRecognitionResultList {
  length: number;
  [index: number]: BrowserSpeechRecognitionResult;
}

interface BrowserSpeechRecognitionEvent extends Event {
  results: BrowserSpeechRecognitionResultList;
}

interface BrowserSpeechRecognitionErrorEvent extends Event {
  error: SpeechRecognitionErrorType;
}

interface BrowserSpeechRecognition {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  abort: () => void;
}

type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type SpeechWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };

function safeAbort(recognition: BrowserSpeechRecognition | null) {
  if (!recognition) {
    return;
  }

  try {
    recognition.abort();
  } catch {
    // Ignore abort failures from stale or already-closed instances.
  }
}

export function useSpeech() {
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const shouldBeListeningRef = useRef(false);
  const generationRef = useRef(0);

  const start = useCallback(() => {
    safeAbort(recognitionRef.current);
    recognitionRef.current = null;

    setError(null);
    setTranscript('');
    setIsListening(true);
    shouldBeListeningRef.current = true;

    const currentGeneration = ++generationRef.current;
    const speechWindow = window as SpeechWindow;
    const RecognitionCtor =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

    if (!RecognitionCtor) {
      setError('浏览器不支持语音识别 API');
      setIsListening(false);
      shouldBeListeningRef.current = false;
      return;
    }

    const launchRecognition = (generation: number) => {
      if (generation !== generationRef.current || !shouldBeListeningRef.current) {
        return;
      }

      const recognition = new RecognitionCtor();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.lang = 'zh-CN';

      recognition.onresult = (event) => {
        if (generation !== generationRef.current) {
          return;
        }

        setError(null);

        let sessionFinal = '';
        let sessionInterim = '';

        for (let i = 0; i < event.results.length; i += 1) {
          const result = event.results[i];
          const transcriptText = result[0]?.transcript ?? '';

          if (result.isFinal) {
            sessionFinal += transcriptText;
          } else {
            sessionInterim += transcriptText;
          }
        }

        setTranscript(sessionFinal + sessionInterim);
      };

      recognition.onerror = (event) => {
        if (generation !== generationRef.current) {
          return;
        }

        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          setError('麦克风权限被拒绝');
          setIsListening(false);
          shouldBeListeningRef.current = false;
        } else if (event.error === 'network') {
          setError('网络连接波动，正在自动重试');
        }
      };

      recognition.onend = () => {
        if (generation !== generationRef.current) {
          return;
        }

        if (shouldBeListeningRef.current) {
          window.setTimeout(() => {
            launchRecognition(generation);
          }, 500);
        } else {
          setIsListening(false);
        }
      };

      try {
        recognition.start();
        recognitionRef.current = recognition;
      } catch (startError) {
        console.error('[Speech] 启动失败:', startError);
        setError('语音识别启动失败');
        setIsListening(false);
        shouldBeListeningRef.current = false;
      }
    };

    launchRecognition(currentGeneration);
  }, []);

  const stop = useCallback(() => {
    shouldBeListeningRef.current = false;
    generationRef.current += 1;
    safeAbort(recognitionRef.current);
    recognitionRef.current = null;
    setIsListening(false);
  }, []);

  return { start, stop, transcript, isListening, error };
}
