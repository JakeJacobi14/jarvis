from piper.voice import PiperVoice
import sounddevice as sd
import soundfile as sf
import io

voice = PiperVoice.load(
    "en_US-hfc_female-medium.onnx",
    "en_US-hfc_female-medium.onnx.json"
)

text = "Hello, I'm Jarvis. How can I help you today?"

wav_buffer = io.BytesIO()
voice.synthesize(text, wav_buffer)
wav_buffer.seek(0)

audio, sample_rate = sf.read(wav_buffer, dtype="float32")

sd.play(audio, sample_rate)
sd.wait()

print("Done speaking!")