"""
Audio I/O: wake-word detection (forward-capture VAD) and voice recording (VAD silence detection).
"""
import os, time, threading, tempfile, wave, logging
from collections import deque

import numpy as np
import sounddevice as sd
from openai import OpenAI

from constants import NUM_BARS


class WakeWordListener:
    SR            = 16000
    ENERGY_THRESH = 0.035   # RMS threshold to start capture
    CAPTURE_SECS  = 2.0     # record this many seconds after spike
    COOLDOWN_SECS = 4.0
    PRE_MS        = 250     # prepend this many ms to catch word onset

    def __init__(self, get_phrase, get_key, on_wake):
        self.get_phrase      = get_phrase
        self.get_key         = get_key
        self.on_wake         = on_wake
        self._active         = False
        self._last_wake      = 0.0
        self._capturing      = False
        self._capture_buf    = []
        self._capture_frames = 0
        self._ring           = deque(maxlen=int(self.SR * self.PRE_MS / 1000))
        self._stream         = None

    def start(self):
        if self._active:
            return
        self._active = True
        self._ring.clear()
        try:
            self._stream = sd.InputStream(
                samplerate=self.SR, channels=1, dtype="float32",
                blocksize=1024, callback=self._cb)
            self._stream.start()
            logging.info("WakeWordListener started, phrase=%r", self.get_phrase())
        except Exception as e:
            self._active = False
            logging.error("WakeWordListener failed to start: %s", e)

    def stop(self):
        self._active = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _cb(self, indata, frames, t, status):
        self._ring.extend(indata[:, 0])
        if not self._active:
            return
        if time.monotonic() - self._last_wake < self.COOLDOWN_SECS:
            return

        rms = float(np.sqrt(np.mean(indata ** 2)))

        if self._capturing:
            self._capture_buf.append(indata[:, 0].copy())
            self._capture_frames += frames
            if self._capture_frames >= int(self.CAPTURE_SECS * self.SR):
                self._capturing = False
                self._last_wake = time.monotonic()
                audio = np.concatenate(self._capture_buf)
                threading.Thread(target=self._check, args=(audio,), daemon=True).start()
        elif rms >= self.ENERGY_THRESH:
            logging.debug("Wake energy spike rms=%.4f, start forward capture", rms)
            self._capturing      = True
            pre                  = np.array(list(self._ring))
            self._capture_buf    = [pre]
            self._capture_frames = len(pre)

    def _check(self, audio):
        tmp = None
        try:
            key    = self.get_key()
            phrase = self.get_phrase().lower().strip()
            if not key or not phrase:
                return
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.SR)
                wf.writeframes((audio * 32767).astype(np.int16).tobytes())
            client = OpenAI(api_key=key)
            with open(tmp.name, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1", file=f,
                    prompt=f"Wake word: {phrase}. BashIn overlay.")
            transcript = result.text.lower().strip()
            logging.info("Wake check: transcript=%r  phrase=%r  match=%s",
                         transcript, phrase, phrase in transcript)
            if phrase in transcript:
                self.on_wake()
        except Exception as e:
            logging.error("WakeWordListener._check failed: %s", e)
        finally:
            if tmp:
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass


class VoiceRecorder:
    SR               = 16000
    SILENCE_THRESH   = 0.012   # slightly lower so quiet speech counts
    SILENCE_SECS     = 1.5
    MIN_SPEECH_SECS  = 0.4

    def __init__(self, on_levels, on_silence):
        self.on_levels  = on_levels
        self.on_silence = on_silence
        self._chunks    = []
        self._stream    = None
        self._speech_f  = 0
        self._silence_f = 0
        self._triggered = False

    def start(self):
        self._chunks    = []
        self._speech_f  = 0
        self._silence_f = 0
        self._triggered = False
        self._stream = sd.InputStream(
            samplerate=self.SR, channels=1, dtype="float32",
            blocksize=512, callback=self._cb)
        self._stream.start()

    def _cb(self, indata, n_frames, _t, _status):
        self._chunks.append(indata.copy())
        fft    = np.abs(np.fft.rfft(indata[:, 0], n=512))
        levels = [min(float(np.mean(b)) * 10, 1.0)
                  for b in np.array_split(fft[:128], NUM_BARS)]
        self.on_levels(levels)
        rms = float(np.sqrt(np.mean(indata ** 2)))
        if rms >= self.SILENCE_THRESH:
            self._speech_f += n_frames
            self._silence_f = 0
        else:
            self._silence_f += n_frames
        if (not self._triggered
                and self._speech_f  >= int(self.MIN_SPEECH_SECS * self.SR)
                and self._silence_f >= int(self.SILENCE_SECS    * self.SR)):
            self._triggered = True
            self.on_silence()

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks)
        tmp   = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.SR)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        return tmp.name
