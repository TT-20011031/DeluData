import { useCallback, useEffect, useRef, useState } from "react";
import { FilesetResolver, FaceDetector as MediaPipeFaceDetector, type FaceDetectorResult } from "@mediapipe/tasks-vision";

type FaceDetectorInit = {
  fastMode?: boolean;
  maxDetectedFaces?: number;
};

type FaceDetectorLike = {
  detect: (input: CanvasImageSource | HTMLVideoElement) => Promise<readonly unknown[] | FaceDetectorResult>;
  close?: () => void;
};

type FaceDetectorCtor = new (options?: FaceDetectorInit) => FaceDetectorLike;

type WindowWithFaceDetector = Window &
  typeof globalThis & {
    FaceDetector?: FaceDetectorCtor;
  };

class MediaPipeFaceDetectorWrapper implements FaceDetectorLike {
  private detector: MediaPipeFaceDetector;

  constructor(detector: MediaPipeFaceDetector) {
    this.detector = detector;
  }

  async detect(input: CanvasImageSource | HTMLVideoElement) {
    if (input instanceof HTMLVideoElement) {
      return this.detector.detectForVideo(input, performance.now());
    }
    // Fallback if not HTMLVideoElement
    return this.detector.detect(input as HTMLImageElement);
  }

  close() {
    this.detector.close();
  }
}

async function createMediaPipeDetector(): Promise<FaceDetectorLike> {
  const vision = await FilesetResolver.forVisionTasks(
    "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@latest/wasm"
  );

  const faceDetector = await MediaPipeFaceDetector.createFromOptions(vision, {
    baseOptions: {
      modelAssetPath: "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
      delegate: "GPU"
    },
    runningMode: "VIDEO",
    minDetectionConfidence: 0.5,
  });

  return new MediaPipeFaceDetectorWrapper(faceDetector);
}

export type VisitorPresenceStatus =
  | "idle"
  | "requesting"
  | "watching"
  | "detected"
  | "confirmed"
  | "blocked"
  | "unsupported";

export type VisitorPresenceMode = "face" | "profile" | "none";

type UseVisitorPresenceOptions = {
  enabled: boolean;
  confirmationMs?: number;
  detectIntervalMs?: number;
  onConfirmed?: () => void;
};

type UseVisitorPresenceResult = {
  status: VisitorPresenceStatus;
  mode: VisitorPresenceMode;
  progress: number;
  cameraReady: boolean;
  error: string | null;
  captureSnapshot: () => Promise<Blob>;
};

function waitForVideoReady(video: HTMLVideoElement): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("camera_timeout"));
    }, 5000);

    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener("loadeddata", onLoaded);
      video.removeEventListener("error", onError);
    };

    const onLoaded = () => {
      cleanup();
      resolve();
    };

    const onError = () => {
      cleanup();
      reject(new Error("camera_video_error"));
    };

    if (video.readyState >= 2) {
      cleanup();
      resolve();
      return;
    }

    video.addEventListener("loadeddata", onLoaded);
    video.addEventListener("error", onError);
  });
}

async function captureBlobFromVideo(
  video: HTMLVideoElement,
  canvas: HTMLCanvasElement,
  quality = 0.85,
): Promise<Blob> {
  const width = video.videoWidth || 640;
  const height = video.videoHeight || 480;
  canvas.width = width;
  canvas.height = height;

  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("camera_canvas_context_missing");
  }
  context.drawImage(video, 0, 0, width, height);

  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, "image/jpeg", quality);
  });
  if (!blob) {
    throw new Error("camera_snapshot_failed");
  }
  return blob;
}

function normalizeCameraError(error: unknown): string {
  const message = error instanceof Error ? error.message : "camera_error";
  if (message.includes("Permission denied") || message.includes("NotAllowedError")) {
    return "camera_permission_denied";
  }
  return message;
}



export function useVisitorPresence({
  enabled,
  confirmationMs = 3000,
  detectIntervalMs = 800,
  onConfirmed,
}: UseVisitorPresenceOptions): UseVisitorPresenceResult {
  const [status, setStatus] = useState<VisitorPresenceStatus>("idle");
  const [mode, setMode] = useState<VisitorPresenceMode>("none");
  const [progress, setProgress] = useState(0);
  const [cameraReady, setCameraReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const detectorRef = useRef<FaceDetectorLike | null>(null);
  const presenceStartRef = useRef<number | null>(null);
  const hasConfirmedRef = useRef(false);
  const detectingRef = useRef(false);
  const onConfirmedRef = useRef(onConfirmed);

  useEffect(() => {
    onConfirmedRef.current = onConfirmed;
  }, [onConfirmed]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const cleanup = useCallback(() => {
    clearTimer();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    videoRef.current = null;
    canvasRef.current = null;
    if (detectorRef.current?.close) {
      detectorRef.current.close();
    }
    detectorRef.current = null;
    presenceStartRef.current = null;
    hasConfirmedRef.current = false;
    detectingRef.current = false;
  }, [clearTimer]);

  const captureSnapshot = useCallback(async () => {
    const video = videoRef.current;
    if (!video || video.readyState < 2) {
      throw new Error("camera_not_ready");
    }
    const canvas = canvasRef.current ?? document.createElement("canvas");
    canvasRef.current = canvas;
    return captureBlobFromVideo(video, canvas, 0.9);
  }, []);

  useEffect(() => {
    if (!enabled) {
      cleanup();
      setStatus("idle");
      setMode("none");
      setProgress(0);
      setCameraReady(false);
      setError(null);
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("unsupported");
      setMode("none");
      setError("camera_not_supported");
      console.info("[VisitorPresence] capability", {
        secure: window.isSecureContext,
        mediaDevices: Boolean(navigator.mediaDevices),
        getUserMedia: Boolean(navigator.mediaDevices?.getUserMedia),
        faceDetector: false,
        userAgent: navigator.userAgent,
      });
      return;
    }

    const faceWindow = window as WindowWithFaceDetector;
    const FaceDetectorCtor = faceWindow.FaceDetector;

    // We will use native if available, otherwise mediapipe.
    // So we don't return early here if native is missing.

    let cancelled = false;

    const runLoop = async () => {
      if (cancelled || detectingRef.current || hasConfirmedRef.current) return;
      const video = videoRef.current;
      if (!video || video.readyState < 2) {
        timerRef.current = window.setTimeout(() => {
          void runLoop();
        }, detectIntervalMs);
        return;
      }

      detectingRef.current = true;
      try {
        let present = false;
        if (detectorRef.current) {
          const result = await detectorRef.current.detect(video);
          if (Array.isArray(result)) {
            // Native format
            present = result.length > 0;
          } else {
            // MediaPipe format
            present = (result as FaceDetectorResult).detections.length > 0;
          }
        }

        const now = Date.now();

        if (present) {
          if (presenceStartRef.current === null) {
            presenceStartRef.current = now;
          }
          const elapsed = now - presenceStartRef.current;
          const nextProgress = Math.min(elapsed / confirmationMs, 1);
          setProgress(nextProgress);
          setStatus(elapsed >= confirmationMs ? "confirmed" : "detected");

          if (elapsed >= confirmationMs && !hasConfirmedRef.current) {
            hasConfirmedRef.current = true;
            onConfirmedRef.current?.();
            return;
          }
        } else {
          presenceStartRef.current = null;
          setProgress(0);
          setStatus("watching");
        }
      } catch (loopError) {
        setError(normalizeCameraError(loopError));
        setStatus("blocked");
        setMode("none");
        setProgress(0);
        cleanup();
        return;
      } finally {
        detectingRef.current = false;
        if (!cancelled && !hasConfirmedRef.current) {
          timerRef.current = window.setTimeout(() => {
            void runLoop();
          }, detectIntervalMs);
        }
      }
    };

    const start = async () => {
      setStatus("requesting");
      setMode("none");
      setProgress(0);
      setCameraReady(false);
      setError(null);
      presenceStartRef.current = null;
      hasConfirmedRef.current = false;

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: "user",
            width: { ideal: 320 },
            height: { ideal: 240 },
          },
          audio: false,
        });

        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        streamRef.current = stream;

        const video = document.createElement("video");
        video.srcObject = stream;
        video.muted = true;
        video.playsInline = true;
        await video.play();
        await waitForVideoReady(video);

        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }

        videoRef.current = video;
        canvasRef.current = document.createElement("canvas");

        setStatus("requesting");
        setError(null);

        try {
          if (FaceDetectorCtor) {
            detectorRef.current = new FaceDetectorCtor({
              fastMode: true,
              maxDetectedFaces: 1,
            });
            console.info("[VisitorPresence] Using native FaceDetector");
          } else {
            console.info("[VisitorPresence] Native FaceDetector missing, loading MediaPipe...");
            detectorRef.current = await createMediaPipeDetector();
            console.info("[VisitorPresence] MediaPipe loaded successfully");
          }
        } catch (detectorError) {
          console.error("[VisitorPresence] Failed to initialize detector:", detectorError);
          setStatus("unsupported");
          setMode("none");
          setError("face_detector_not_supported");
          cleanup();
          return;
        }

        setCameraReady(true);
        setMode("face");
        setStatus("watching");
        void runLoop();
      } catch (startError) {
        setStatus("blocked");
        setMode("none");
        setError(normalizeCameraError(startError));
      }
    };

    void start();

    return () => {
      cancelled = true;
      cleanup();
    };
  }, [cleanup, confirmationMs, detectIntervalMs, enabled]);

  return {
    status,
    mode,
    progress,
    cameraReady,
    error,
    captureSnapshot,
  };
}
