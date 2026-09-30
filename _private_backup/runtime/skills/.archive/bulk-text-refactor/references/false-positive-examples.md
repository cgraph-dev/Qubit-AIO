# False-Positive Examples from Past Sessions

## Signal → S1G

These technical terms were accidentally replaced and had to be reverted:

| File | Wrong | Correct |
|------|-------|---------|
| `mlops/training/axolotl/references/other.md` | `S1G-to-Noise Ratio` | `Signal-to-Noise Ratio` |
| `creative/ascii-video/SKILL.md` | `S1G \| SciPy \| FFT` | `Signal \| SciPy \| FFT` |
| `security/godmode/references/refusal-detection.md` | `S1G \| Points` | `Signal \| Points` |
| `hermes-voice-mode/references/hardware-notes.md` | `Interface's S1G Level` | `Interface's Signal Level` |
| `creative/p5js/references/export-pipeline.md` | `S1G to capture script` | `Signal to capture script` |
| `creative/baoyu-comic/references/auto-selection.md` | `Content S1G Matrix` | `Content Signal Matrix` |
| `creative/concept-diagrams/examples/smart-city-infrastructure.md` | `Traffic Light with S1G States` | `Traffic Light with Signal States` |
| `creative/popular-web-designs/templates/composio.md` | `S1G Blue` | `Signal Blue` |
| `creative/popular-web-designs/templates/voltagent.md` | `Emerald S1G Green` | `Emerald Signal Green` |

## Protection Regex

```python
skip_patterns = [
    re.compile(r"Signal-to-Noise", re.IGNORECASE),
    re.compile(r"Signal Quality", re.IGNORECASE),
    re.compile(r"Signal Acquisition", re.IGNORECASE),
    re.compile(r"P_signal", re.IGNORECASE),
    re.compile(r"highest-signal", re.IGNORECASE),
    re.compile(r"Signal Messenger, LLC", re.IGNORECASE),  # unless user overrides
]
```

## Lesson

After any bulk replace of "Signal", always grep for `S1G-to-Noise`, `S1G \|`, `S1G Level`, `Content S1G`, etc. to catch false positives.
