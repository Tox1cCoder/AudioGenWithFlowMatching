import torchaudio
from tangoflux import TangoFluxInference

model = TangoFluxInference(name="declare-lab/TangoFlux")
audio = model.generate("Hammer slowly hitting the wooden table", steps=50, duration=10)

torchaudio.save("output.wav", audio, sample_rate=44100)
