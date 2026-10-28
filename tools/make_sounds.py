"""Synthesizes the little character sounds into sounds/*.wav (no samples needed)."""
import math
import os
import random
import struct
import wave

RATE = 22050
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds")


def voice(dur, pitch, bright, noise=0.0, vibrato=0.0, attack=0.01, release=0.05, harmonics=10, seed=1):
    """pitch(x) and bright(x) take x in [0, 1]; bright is the harmonic roll-off (higher = brighter)."""
    rnd = random.Random(seed)
    n = int(dur * RATE)
    out, phase, lp = [], 0.0, 0.0
    for i in range(n):
        x = i / n
        t = i / RATE
        f = pitch(x) * (1 + vibrato * math.sin(2 * math.pi * 5.5 * t))
        phase += 2 * math.pi * f / RATE
        b = bright(x)
        s = sum(math.sin(phase * h) / h ** (1.6 - b) for h in range(1, harmonics + 1))
        lp += 0.35 * (rnd.uniform(-1, 1) - lp)
        s += noise * lp * 6
        env = min(1, t / attack) * min(1, (dur - t) / release)
        out.append(s * env)
    return out


def silence(dur):
    return [0.0] * int(dur * RATE)


def save(name, samples, gain=0.6):
    peak = max(abs(s) for s in samples) or 1
    with wave.open(os.path.join(OUT, name + ".wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(s / peak * gain * 32767)) for s in samples))


def bark(seed):
    return voice(0.15, lambda x: 420 * (1 - 0.45 * x) * (1 + 0.25 * math.exp(-x * 30)),
                 lambda x: 1.1 - 0.6 * x, noise=0.5, attack=0.004, release=0.06, seed=seed)


def main():
    os.makedirs(OUT, exist_ok=True)
    save("bark", bark(1) + silence(0.07) + bark(2))
    save("yip", voice(0.13, lambda x: 720 + 380 * math.sin(math.pi * x), lambda x: 1.0, noise=0.15,
                      attack=0.004, release=0.05))
    save("awoo", voice(0.85, lambda x: 380 + 300 * min(1, x * 2.2) - 120 * max(0, x - 0.6),
                       lambda x: 0.5 + 0.5 * math.sin(math.pi * x), vibrato=0.012, attack=0.12, release=0.25))
    meow_pitch = lambda x: 480 + 260 * math.sin(math.pi * min(1, x * 1.3))
    meow_bright = lambda x: 0.4 + 0.9 * math.sin(math.pi * x)  # "mee-ow": brightness opens then closes
    save("meow", voice(0.5, meow_pitch, meow_bright, vibrato=0.01, attack=0.03, release=0.12))
    save("mew", voice(0.22, lambda x: 700 + 200 * math.sin(math.pi * x), meow_bright, attack=0.02, release=0.07))
    croak = lambda seed: [s * (0.6 + 0.4 * math.sin(2 * math.pi * 45 * i / RATE)) for i, s in enumerate(
        voice(0.09, lambda x: 150 - 30 * x, lambda x: 1.3, noise=0.2, harmonics=24, attack=0.005,
              release=0.03, seed=seed))]
    save("ribbit", croak(1) + silence(0.05) + croak(2))
    print("wrote", sorted(os.listdir(OUT)))


if __name__ == "__main__":
    main()
