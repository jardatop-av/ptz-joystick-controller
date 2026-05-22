from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ViscaPacketEncoder:
    """Encodes VISCA command payloads into a VISCA-over-IP-style packet.

    This encoder is intentionally transport agnostic. It does not open sockets.
    The 8 byte header uses a stable offline format suitable for testing:
    payload_type(2), payload_length(2), sequence_number(4).
    """

    sequence_number: int = 0
    payload_type: bytes = field(default=b"\x01\x00")

    def encode(self, payload: bytes) -> bytes:
        if not payload:
            raise ValueError("VISCA payload cannot be empty")
        if len(self.payload_type) != 2:
            raise ValueError("payload_type must be exactly 2 bytes")
        length = len(payload).to_bytes(2, "big")
        sequence = self.sequence_number.to_bytes(4, "big")
        self.sequence_number = (self.sequence_number + 1) & 0xFFFFFFFF
        return self.payload_type + length + sequence + payload
