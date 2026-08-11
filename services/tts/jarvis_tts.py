from kokoro_onnx import Kokoro
import sounddevice as sd
import time


class JarvisTTS:
    def __init__(
        self,
        model_path="models/kokoro-v1.0.fp16.onnx",
        voices_path="models/voices-v1.0.bin",
        voice="bm_george",
        speed=1.25,
        lang="en-gb",
    ):
        self.tts = Kokoro(
            model_path=model_path,
            voices_path=voices_path,
        )

        self.voice = voice
        self.speed = speed
        self.lang = lang

    def say(self, text: str):
        """Synthesize and play. Returns (synth_seconds, play_seconds)."""
        t0 = time.perf_counter()
        samples, sample_rate = self.tts.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
            trim=False,
        )
        synth_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        sd.play(samples, sample_rate)
        sd.wait()
        play_s = time.perf_counter() - t1
        return synth_s, play_s
