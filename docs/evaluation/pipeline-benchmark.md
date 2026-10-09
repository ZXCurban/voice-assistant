# Assistant pipeline benchmark

Measurements were taken locally on Python 3.14.7, x86_64, using 5,000
iterations per pure component. These are warm in-process averages; they do
not include model loading, database/network work, or audio I/O.

| Target stage | Average latency |
|---|---:|
| Rules NLU parser | 0.179 ms |
| Slot normalization | 0.015 ms |
| Jinja2 response rendering | 0.064 ms |
| Parser + normalizer + response | 0.258 ms |
| Backend | Not measured against a live DB in this environment |
| STT / TTS | Not present in this repository |
| End-to-end voice request | Not measurable without STT, model runtime, DB and TTS |

## Comparison with the previous path

The previous `/chat` implementation could spend up to six LLM tool-loop
iterations, with `LLM_TIMEOUT_S=300` per generation and up to 512 output
tokens. It made at least one generation for non-smalltalk requests and
another after backend tools to verbalize results. No local chat-completions
server was available for a valid latency run, so a numeric before/after
comparison would be fabricated. The only grounded comparison is structural:
the new primary path makes zero LLM calls and its measured pure stages total
0.258 ms on the stated fixture.

To obtain an operational end-to-end number, benchmark with the intended
clinic database and measure STT/TTS separately.
