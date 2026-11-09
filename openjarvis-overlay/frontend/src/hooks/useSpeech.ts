import { useState, useCallback, useRef, useEffect } from 'react';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

export interface StartRecordingOptions {
  /**
   * Called once when the speaker goes quiet. `heardSpeech` is false when the
   * recording ended without any detected voice (nothing worth transcribing).
   */
  onSilence?: (heardSpeech: boolean) => void;
}

// [Nau] Voice-activity detection for hands-free conversation mode.
// The noise floor adapts continuously (drops instantly, rises slowly), so a
// constant background hum (fan, music) is not mistaken for speech.
const VAD_TICK_MS = 100;
const VAD_CALIBRATION_MS = 300;
const VAD_MIN_SPEECH_RMS = 0.012;
const VAD_NOISE_MULTIPLIER = 2.5;
const VAD_FLOOR_RISE = 0.02;
const VAD_MIN_SPEECH_TICKS = 3;
const VAD_SILENCE_MS = 1200;
const VAD_NO_SPEECH_MS = 8000;
const VAD_MAX_RECORDING_MS = 20000;

function watchForSilence(
  stream: MediaStream,
  onSilence: (heardSpeech: boolean) => void,
): () => void {
  const ctx = new AudioContext();
  void ctx.resume();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);

  const startedAt = performance.now();
  let noiseSum = 0;
  let noiseTicks = 0;
  let floor = 0;
  let speechTicks = 0;
  let heardSpeech = false;
  let lastSpeechAt = startedAt;
  let done = false;

  const cleanup = () => {
    if (done) return;
    done = true;
    clearInterval(timer);
    void ctx.close();
  };

  const timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    let sumSquares = 0;
    for (const s of samples) sumSquares += s * s;
    const rms = Math.sqrt(sumSquares / samples.length);
    const now = performance.now();
    const elapsed = now - startedAt;

    if (elapsed < VAD_CALIBRATION_MS) {
      noiseSum += rms;
      noiseTicks += 1;
      floor = noiseSum / noiseTicks;
      return;
    }
    const threshold = Math.max(VAD_MIN_SPEECH_RMS, floor * VAD_NOISE_MULTIPLIER);
    if (rms > threshold) {
      speechTicks += 1;
      if (speechTicks >= VAD_MIN_SPEECH_TICKS) heardSpeech = true;
      lastSpeechAt = now;
    } else {
      speechTicks = 0;
    }
    floor = rms < floor ? rms : floor + (rms - floor) * VAD_FLOOR_RISE;

    const quietAfterSpeech = heardSpeech && now - lastSpeechAt > VAD_SILENCE_MS;
    const neverSpoke = !heardSpeech && elapsed > VAD_NO_SPEECH_MS;
    if (quietAfterSpeech || neverSpoke || elapsed > VAD_MAX_RECORDING_MS) {
      cleanup();
      onSilence(heardSpeech);
    }
  }, VAD_TICK_MS);

  return cleanup;
}

export function useSpeech() {
  const [state, setState] = useState<SpeechState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const stopVadRef = useRef<(() => void) | null>(null);

  // Check if speech backend is available on mount
  useEffect(() => {
    fetchSpeechHealth()
      .then((health) => setAvailable(health.available))
      .catch(() => setAvailable(false));
  }, []);

  const startRecording = useCallback(async (options?: StartRecordingOptions): Promise<void> => {
    setError(null);

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('Microphone not supported in this browser');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setState('recording');
      if (options?.onSilence) {
        stopVadRef.current = watchForSilence(stream, options.onSilence);
      }
    } catch (err) {
      setError('Microphone access denied');
      setState('idle');
    }
  }, []);

  const stopRecording = useCallback(async (): Promise<string> => {
    stopVadRef.current?.();
    stopVadRef.current = null;
    return new Promise((resolve, reject) => {
      const recorder = mediaRecorderRef.current;
      if (!recorder || recorder.state !== 'recording') {
        reject(new Error('Not recording'));
        return;
      }

      recorder.onstop = async () => {
        setState('transcribing');

        // Stop all audio tracks
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        chunksRef.current = [];

        try {
          const result = await transcribeAudio(blob);
          setState('idle');
          resolve(result.text);
        } catch (err) {
          setState('idle');
          const msg = err instanceof Error ? err.message : 'Transcription failed';
          setError(msg);
          reject(err);
        }
      };

      recorder.stop();
    });
  }, []);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
