import { useCallback, useEffect, useRef } from "react";

export function useBrowserSpeech() {
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);

  const stop = useCallback(() => {
    window.speechSynthesis?.cancel();
    utteranceRef.current = null;
  }, []);

  const speak = useCallback((text: string) => {
    const content = (text || "").trim();
    if (!content || !window.speechSynthesis || typeof window.SpeechSynthesisUtterance === "undefined") {
      return Promise.resolve(false);
    }

    stop();
    return new Promise<boolean>((resolve) => {
      const utterance = new SpeechSynthesisUtterance(content);
      utterance.lang = "zh-CN";
      utterance.rate = 1;
      utterance.pitch = 1;
      utteranceRef.current = utterance;
      utterance.onend = () => resolve(true);
      utterance.onerror = () => resolve(false);
      window.speechSynthesis.speak(utterance);
    });
  }, [stop]);

  useEffect(() => () => stop(), [stop]);

  return { speak, stop };
}
