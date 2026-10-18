"""Send-path image eviction policy shared by both stateless outbound passes.

Two passes retire old tool-result images from the per-request copy of the conversation:
``agent.context_compressor.evict_stale_outbound_tool_images`` on the OpenAI-shaped list and
``agent.anthropic_message_convert._evict_old_screenshots`` on the Anthropic wire list. Both run
from scratch on a fresh clone every request, so they must agree on one policy or the second
pass re-evicts on a different frontier than the first (#113517). This module is stdlib-only so
the wire converter, a leaf, can import it without dragging in the compaction stack.

Why the trigger is a provider limit, not a keep-newest count: retiring an image edits a message
the provider has already cached, and Anthropic matches its prompt cache on an exact byte prefix.
A keep-newest-N window retires one more message on every new image, so every turn is a
full-prefix miss. Holding images until the request would cross a real API limit and then
retiring a batch costs one slower turn per batch and nothing below the limit.

20 is the documented threshold at which Anthropic applies a stricter per-image dimension cap
(2000 px) to EVERY image in the request, counting images nested in tool_result content. The hard
ceilings are higher (100 images per request on 200K-context models, 600 otherwise) but the 32 MB
request-size limit usually binds first, which the byte budget guards with headroom for text.
"""

from __future__ import annotations

import struct
from typing import Optional, Sequence

OUTBOUND_IMAGE_LIMIT = 20
OUTBOUND_IMAGE_BUDGET_BYTES = 24_000_000
IMAGE_EVICTION_BATCH = 8
# Satisfiability floor — see outbound_image_retire_count.
OUTBOUND_IMAGE_FLOOR = 3

_JPEG_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def image_dimensions_from_bytes(raw: bytes) -> Optional[tuple[int, int]]:
    """(width, height) for PNG / JPEG bytes, or None when unreadable. PNG: IHDR. JPEG: walk
    segments (skipping 0xFF fill bytes) to the first SOF marker; stop at SOS. Lives here so both
    the send-path passes and the tool layer share one header-only reader."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])  # cannot fail: 8 bytes are guaranteed present
        return int(width), int(height)
    if raw.startswith(b"\xff\xd8") and len(raw) > 4:
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 0xFF:
                i += 1
                continue
            marker, i = raw[i + 1], i + 2
            while marker == 0xFF and i < len(raw):
                marker, i = raw[i], i + 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or i + 2 > len(raw):
                break
            segment_len = int.from_bytes(raw[i:i + 2], "big")
            if segment_len < 2 or i + segment_len > len(raw):
                break
            if marker in _JPEG_SOF_MARKERS and segment_len >= 7:
                return int.from_bytes(raw[i + 5:i + 7], "big"), int.from_bytes(raw[i + 3:i + 5], "big")
            i += segment_len
    return None


def outbound_image_retire_count(
    carrier_blocks_newest_first: Sequence[int],
    reserved_blocks: int,
    *,
    carrier_bytes_newest_first: Optional[Sequence[int]] = None,
    reserved_bytes: int = 0,
    limit: int = OUTBOUND_IMAGE_LIMIT,
    budget: int = OUTBOUND_IMAGE_BUDGET_BYTES,
    batch: int = IMAGE_EVICTION_BATCH,
    floor: int = OUTBOUND_IMAGE_FLOOR,
) -> int:
    """How many of the OLDEST image-bearing tool results to retire.

    ``carrier_blocks_newest_first`` is the image-block count per image-bearing tool result,
    newest first; ``reserved_blocks`` counts images the pass must never rewrite (user
    uploads). The byte dimension is active only when ``carrier_bytes_newest_first`` is
    given (the wire pass has no sizes).

    The retire count must be a STEP FUNCTION of the overshoot, because the pass is recomputed
    on every request: an exact ``count - limit`` target moves the frontier on every new image,
    and a fixed one-batch retire stops enforcing the limit after the first batch. So the count
    advances in quanta until the request fits.

    The quantum is ``batch`` capped at ``window - floor``, where ``window`` is how many newest
    carriers fit under the ceiling. A quantum wider than that would step past the newest frames
    on every advance; cutting each step back to exactly ``total - floor`` instead makes the
    retire count track ``total`` again — the per-image frontier this policy exists to avoid,
    visible whenever a tool result carries several images or uploads fill most of the ceiling.
    Holding for ``window - floor`` turns per advance is the most the floor allows.

    The floor is a SATISFIABILITY floor: it shelters the newest frames only when reserved
    uploads alone breach the block ceiling (no retirement can fix that), and never under byte
    pressure — the request-size limit is hard and the provider answers 413.
    """
    total = len(carrier_blocks_newest_first)
    sizes = carrier_bytes_newest_first
    blocks_kept = [0] * (total + 1)
    bytes_kept = [0] * (total + 1)
    for i in range(total):
        blocks_kept[i + 1] = blocks_kept[i] + carrier_blocks_newest_first[i]
        bytes_kept[i + 1] = bytes_kept[i] + (sizes[i] if sizes is not None else 0)

    def _bytes_fit(kept: int) -> bool:
        return sizes is None or reserved_bytes + bytes_kept[kept] <= budget

    def _fits(kept: int) -> bool:
        return reserved_blocks + blocks_kept[kept] <= limit and _bytes_fit(kept)

    if _fits(total):
        return 0

    floor = min(max(floor, 0), total)
    max_retire = total - floor if not _fits(0) and _bytes_fit(floor) else total
    if max_retire <= 0:
        return 0
    window = max(k for k in range(total + 1) if _fits(k)) if _fits(0) else 0
    quantum = max(1, min(batch, window - floor))

    retire = 0
    while retire < max_retire:
        retire = min(retire + quantum, max_retire)
        if _fits(total - retire):
            break
    return retire
