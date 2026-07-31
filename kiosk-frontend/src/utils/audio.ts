export const TTS_SAMPLE_RATE = 24000;

export function base64ToUint8Array(base64Data: string): Uint8Array {
  const base64String = base64Data.replace(/^data:audio\/\w+;base64,/, "");
  const binaryString = window.atob(base64String);
  const len = binaryString.length;
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i += 1) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes;
}

function toContiguousBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  ) as ArrayBuffer;
}

function looksLikeFloat32Pcm(float32View: Float32Array): boolean {
  if (float32View.length === 0) {
    return false;
  }

  const samplesToCheck = Math.min(64, float32View.length);
  let energy = 0;

  for (let i = 0; i < samplesToCheck; i += 1) {
    const sample = float32View[i];
    if (!Number.isFinite(sample) || Math.abs(sample) > 1.25) {
      return false;
    }
    energy += Math.abs(sample);
  }

  const averageEnergy = energy / samplesToCheck;
  return averageEnergy > 0.0001 && averageEnergy < 0.95;
}

export function pcmBytesToFloat32(bytes: Uint8Array): Float32Array {
  const contiguousBuffer = toContiguousBuffer(bytes);

  // Realtime dialogue 的 `format: "pcm"` 在当前链路下返回 Float32 PCM。
  if (contiguousBuffer.byteLength % 4 === 0 && contiguousBuffer.byteLength > 0) {
    const float32View = new Float32Array(contiguousBuffer);
    if (looksLikeFloat32Pcm(float32View)) {
      return float32View;
    }
  }

  const sampleCount = Math.floor(bytes.byteLength / 2);
  const channel = new Float32Array(sampleCount);

  for (let i = 0; i < sampleCount; i += 1) {
    const lo = bytes[i * 2];
    const hi = bytes[i * 2 + 1];
    let sample = (hi << 8) | lo;
    if (sample >= 0x8000) {
      sample -= 0x10000;
    }
    channel[i] = sample / 32768;
  }

  return channel;
}

export function decodePcmMono(
  audioCtx: AudioContext,
  bytes: Uint8Array,
  sampleRate: number = TTS_SAMPLE_RATE,
): AudioBuffer {
  const channel = pcmBytesToFloat32(bytes);
  const audioBuffer = audioCtx.createBuffer(1, channel.length, sampleRate);
  audioBuffer.copyToChannel(new Float32Array(channel), 0);
  return audioBuffer;
}

export function pcmBytesToWavBlob(
  bytes: Uint8Array,
  sampleRate: number = TTS_SAMPLE_RATE,
): Blob {
  const channel = pcmBytesToFloat32(bytes);
  const wavBuffer = new ArrayBuffer(44 + channel.length * 2);
  const view = new DataView(wavBuffer);
  let offset = 0;

  const writeAscii = (value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset, value.charCodeAt(index));
      offset += 1;
    }
  };

  writeAscii('RIFF');
  view.setUint32(offset, 36 + channel.length * 2, true);
  offset += 4;
  writeAscii('WAVE');
  writeAscii('fmt ');
  view.setUint32(offset, 16, true);
  offset += 4;
  view.setUint16(offset, 1, true);
  offset += 2;
  view.setUint16(offset, 1, true);
  offset += 2;
  view.setUint32(offset, sampleRate, true);
  offset += 4;
  view.setUint32(offset, sampleRate * 2, true);
  offset += 4;
  view.setUint16(offset, 2, true);
  offset += 2;
  view.setUint16(offset, 16, true);
  offset += 2;
  writeAscii('data');
  view.setUint32(offset, channel.length * 2, true);
  offset += 4;

  for (let index = 0; index < channel.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, channel[index]));
    const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(offset, int16, true);
    offset += 2;
  }

  return new Blob([wavBuffer], { type: 'audio/wav' });
}

export async function decodeAudioBase64(
  audioCtx: AudioContext,
  base64Data: string,
): Promise<{
  audioBuffer: AudioBuffer;
  bytes: Uint8Array;
  usedFallbackDecoder: boolean;
}> {
  const bytes = base64ToUint8Array(base64Data);

  try {
    const encodedBuffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer;
    const audioBuffer = await audioCtx.decodeAudioData(encodedBuffer);
    return {
      audioBuffer,
      bytes,
      usedFallbackDecoder: false,
    };
  } catch {
    return {
      audioBuffer: decodePcmMono(audioCtx, bytes),
      bytes,
      usedFallbackDecoder: true,
    };
  }
}
