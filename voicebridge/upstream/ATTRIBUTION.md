# Upstream provenance — retired vendored modules

The Qubit-native modules `voicebridge/textutil.py` (output sanitizing)
and `voicebridge/sqlstore.py` (SQLite helpers) replace two previously
vendored upstream files, which were removed so normal Qubit execution
contains no third-party product code:

- `voicebridge/upstream/hermes_ansi_strip.py` ← `tools/ansi_strip.py`
- `voicebridge/upstream/hermes_sqlite_util.py` ← `hermes_cli/sqlite_util.py`

Former source repository: https://github.com/NousResearch/hermes-agent
Former pin: `2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`)
Former license: MIT License, Copyright (c) 2025 Nous Research
(Retired: no code from that repository remains in the product. The
replacements above are independent Qubit implementations with their own
adversarial tests in `tests/test_textutil8u.py`. Differential comparison
fixtures against the former pin live outside the product tree.)

OpenClaw capability behavior was studied read-only at
https://github.com/openclaw/openclaw @
`3928bad9badfcb6c7d140530435e806fb8092190` (tag `v2026.9.2`);
no OpenClaw source file was ever copied into this repository.

Former license text (applied to the retired files above while present):

```text
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
