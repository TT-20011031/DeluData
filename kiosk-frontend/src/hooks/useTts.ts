import { useCallback, useRef, useState } from 'react';

import { base64ToUint8Array, decodeAudioBase64, pcmBytesToWavBlob } from '../utils/audio';
import {
  getSharedAudioContext,
  unlockSharedAudioContext,
} from '../utils/audioContext';

export type TtsPlayReason =
  | 'ok'
  | 'audio_context_locked'
  | 'decode_failed'
  | 'empty_payload'
  | 'playback_error'
  | 'stopped'
  | 'context_closed';

export type TtsPlayResult = {
  played: boolean;
  reason: TtsPlayReason;
};

export function useTts() {
  const [isPlaying, setIsPlaying] = useState(false);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const htmlAudioRef = useRef<HTMLAudioElement | null>(null);
  const htmlAudioUrlRef = useRef<string | null>(null);
  const pendingResolveRef = useRef<((result: TtsPlayResult) => void) | null>(null);

  const resolvePending = useCallback((result: TtsPlayResult) => {
    pendingResolveRef.current?.(result);
    pendingResolveRef.current = null;
  }, []);

  const cleanupHtmlAudio = useCallback(() => {
    if (htmlAudioRef.current) {
      htmlAudioRef.current.onended = null;
      htmlAudioRef.current.onerror = null;
      htmlAudioRef.current.pause();
      htmlAudioRef.current.src = '';
      htmlAudioRef.current = null;
    }

    if (htmlAudioUrlRef.current) {
      URL.revokeObjectURL(htmlAudioUrlRef.current);
      htmlAudioUrlRef.current = null;
    }
  }, []);

  /** 只停掉当前 source，不碰共享 AudioContext */
  const stopSource = useCallback(() => {
    if (sourceRef.current) {
      try {
        sourceRef.current.stop();
      } catch {
        // already stopped
      }
      sourceRef.current = null;
    }
    cleanupHtmlAudio();
  }, [cleanupHtmlAudio]);

  /** 对外暴露的 stop：停止播放，保留共享 AudioContext */
  const stop = useCallback(() => {
    stopSource();
    setIsPlaying(false);
    resolvePending({ played: false, reason: 'stopped' });
  }, [stopSource, resolvePending]);

  /**
   * 在用户手势上下文中调用，解锁共享 AudioContext 单例。
   * 幂等：已 running 则跳过。
   */
  const prime = useCallback(() => {
    unlockSharedAudioContext();
  }, []);

  const playHtmlAudio = useCallback(
    async (bytes: Uint8Array): Promise<TtsPlayResult> => {
      if (typeof window.Audio === 'undefined') {
        return { played: false, reason: 'playback_error' };
      }

      cleanupHtmlAudio();

      return new Promise<TtsPlayResult>((resolve) => {
        const audio = new window.Audio();
        const wavBlob = pcmBytesToWavBlob(bytes);
        const url = URL.createObjectURL(wavBlob);
        let settled = false;

        const finish = (result: TtsPlayResult) => {
          if (settled) {
            return;
          }
          settled = true;

          if (htmlAudioRef.current === audio) {
            htmlAudioRef.current = null;
          }
          if (htmlAudioUrlRef.current === url) {
            URL.revokeObjectURL(url);
            htmlAudioUrlRef.current = null;
          }

          audio.onended = null;
          audio.onerror = null;
          audio.pause();
          audio.src = '';
          resolve(result);
        };

        audio.preload = 'auto';
        audio.src = url;
        audio.onended = () => finish({ played: true, reason: 'ok' });
        audio.onerror = () => finish({ played: false, reason: 'playback_error' });

        htmlAudioRef.current = audio;
        htmlAudioUrlRef.current = url;

        const playPromise = audio.play();
        if (playPromise && typeof playPromise.catch === 'function') {
          void playPromise.catch((error) => {
            console.warn('[TTS] HTMLAudio fallback failed:', error);
            finish({ played: false, reason: 'audio_context_locked' });
          });
        }
      });
    },
    [cleanupHtmlAudio],
  );

  const play = useCallback(
    async (base64Data: string) => {
      if (!base64Data) {
        console.warn('[TTS] empty audio payload');
        return { played: false, reason: 'empty_payload' } satisfies TtsPlayResult;
      }

      // 停掉上一个 source，不影响共享 AudioContext
      stopSource();
      resolvePending({ played: false, reason: 'stopped' });
      setIsPlaying(true);

      return new Promise<TtsPlayResult>((resolve) => {
        pendingResolveRef.current = resolve;

        void (async () => {
          try {
            const audioCtx = getSharedAudioContext();
            const pcmBytes = base64ToUint8Array(base64Data);
            if (!audioCtx) {
              console.warn('[TTS] AudioContext not ready. Trying HTMLAudio fallback.');
              const htmlAudioResult = await playHtmlAudio(pcmBytes);
              setIsPlaying(false);
              resolvePending(htmlAudioResult);
              return;
            }

            // 安全网：decode 前确保 context 处于 running 状态
            if (audioCtx.state === 'suspended') {
              await audioCtx.resume();
            }

            let audioBuffer: Awaited<ReturnType<typeof decodeAudioBase64>>['audioBuffer'];
            let bytes: Awaited<ReturnType<typeof decodeAudioBase64>>['bytes'];
            let usedFallbackDecoder = false;

            try {
              ({ audioBuffer, bytes, usedFallbackDecoder } = await decodeAudioBase64(
                audioCtx,
                base64Data,
              ));
            } catch (error) {
              console.error('[TTS] decode failed:', error);
              const htmlAudioResult = await playHtmlAudio(pcmBytes);
              setIsPlaying(false);
              resolvePending(
                htmlAudioResult.played ? htmlAudioResult : { played: false, reason: 'decode_failed' },
              );
              return;
            }

            console.log(
              `[TTS] decoded ${bytes.byteLength} bytes via ${
                usedFallbackDecoder ? 'pcm-fallback' : 'browser-decoder'
              }`,
            );

            // decode 期间若 context 已被关闭（不应发生，做防御）
            if (audioCtx.state === 'closed') {
              setIsPlaying(false);
              resolvePending({ played: false, reason: 'context_closed' });
              return;
            }

            try {
              const source = audioCtx.createBufferSource();
              source.buffer = audioBuffer;
              source.connect(audioCtx.destination);
              source.onended = () => {
                if (sourceRef.current === source) {
                  sourceRef.current = null;
                }
                setIsPlaying(false);
                resolvePending({ played: true, reason: 'ok' });
              };
              source.start(0);
              sourceRef.current = source;
            } catch (error) {
              console.error('[TTS] playback failed:', error);
              const htmlAudioResult = await playHtmlAudio(bytes);
              setIsPlaying(false);
              resolvePending(
                htmlAudioResult.played ? htmlAudioResult : { played: false, reason: 'playback_error' },
              );
            }
          } catch (error) {
            console.error('[TTS] playback failed:', error);
            setIsPlaying(false);
            resolvePending({ played: false, reason: 'playback_error' });
          }
        })();
      });
    },
    [playHtmlAudio, resolvePending, stopSource],
  );

  return { play, stop, prime, isPlaying };
}
