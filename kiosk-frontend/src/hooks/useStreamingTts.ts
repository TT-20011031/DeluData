import { useCallback, useEffect, useRef, useState } from "react";

import { decodeAudioBase64 } from "../utils/audio";
import { getSharedAudioContext, unlockSharedAudioContext } from "../utils/audioContext";

type QueueItem = {
  audioBase64: string;
  isFinal: boolean;
};

export function useStreamingTts() {
  const [isPlaying, setIsPlaying] = useState(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const queueRef = useRef<QueueItem[]>([]);
  const processingRef = useRef(false);
  const nextStartRef = useRef(0);
  const waitersRef = useRef<Array<() => void>>([]);

  const resolveWaiters = useCallback(() => {
    const waiters = waitersRef.current.splice(0);
    waiters.forEach((resolve) => resolve());
  }, []);

  const ensureAudioContext = useCallback(async () => {
    // 尝试解锁共享单例（若已 running 则幂等跳过）
    unlockSharedAudioContext();
    const ctx = getSharedAudioContext();
    if (!ctx) {
      throw new Error('[StreamingTTS] AudioContext not ready. Call prime/unlock within a user gesture first.');
    }
    if (ctx.state === "suspended") {
      await ctx.resume();
    }
    // 同步 ref 供 processQueue 内部引用 currentTime
    audioContextRef.current = ctx;
    return ctx;
  }, []);

  const processQueue = useCallback(async () => {
    if (processingRef.current) return;
    processingRef.current = true;
    setIsPlaying(true);

    try {
      const ctx = await ensureAudioContext();

      while (queueRef.current.length > 0) {
        const item = queueRef.current.shift();
        if (!item) continue;
        if (!item.audioBase64) {
          if (item.isFinal) break;
          continue;
        }

        const { audioBuffer, bytes } = await decodeAudioBase64(ctx, item.audioBase64);
        if (!bytes.byteLength) {
          if (item.isFinal) break;
          continue;
        }

        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(ctx.destination);

        const startAt = Math.max(ctx.currentTime, nextStartRef.current);
        source.start(startAt);
        nextStartRef.current = startAt + audioBuffer.duration;

        if (item.isFinal) {
          break;
        }
      }

      const remainingMs = Math.max(
        (nextStartRef.current - (audioContextRef.current?.currentTime || 0)) * 1000,
        0,
      );
      if (remainingMs > 0) {
        await new Promise((resolve) => setTimeout(resolve, remainingMs));
      }
    } finally {
      processingRef.current = false;
      setIsPlaying(false);

      if (queueRef.current.length > 0) {
        void processQueue();
      } else {
        resolveWaiters();
      }
    }
  }, [ensureAudioContext, resolveWaiters]);

  const enqueueChunk = useCallback(
    (audioBase64: string, isFinal = false) => {
      queueRef.current.push({ audioBase64, isFinal });
      if (!processingRef.current) {
        void processQueue();
      }
    },
    [processQueue],
  );

  const waitForDrain = useCallback(() => {
    if (!processingRef.current && queueRef.current.length === 0) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      waitersRef.current.push(resolve);
    });
  }, []);

  const stop = useCallback(() => {
    queueRef.current = [];
    nextStartRef.current = 0;
    // 只清空 ref，不 close 共享 AudioContext
    audioContextRef.current = null;
    setIsPlaying(false);
    resolveWaiters();
  }, [resolveWaiters]);

  useEffect(() => () => stop(), [stop]);

  return {
    enqueueChunk,
    waitForDrain,
    stop,
    isPlaying,
  };
}
