"""Read-only AliceState recognition parser. No socket, commands or logging.

Field contract: v3rt3p/quasar-protos, yandex_io/protos/alice_state.proto.
BUSY (state=3) alone is not evidence of final recognition.
Only the separately enabled VoiceBridge opens a connection; this module has no I/O.
"""
from collections import deque
import re
import unicodedata
from native_alarms import fields, one


def normalized_phrase(text):
    text = unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())


class FinalRecognition:
    def __init__(self, limit=256):
        if not 1 <= limit <= 4096:
            raise ValueError('Invalid request history limit')
        self.seen = deque(maxlen=limit)

    def read(self, message):
        """Return one final user input per request; unknown/malformed -> None."""
        try:
            envelope = fields(message)
            if envelope.get(253) != [3]:
                return None
            state = fields(one(envelope, 252, bytes))
            request_id = one(state, 7, bytes).decode('utf8')
            if not request_id or len(request_id) > 256 or request_id in self.seen:
                return None
            request = fields(one(state, 15, bytes))
            if request.get(2) != [1]:
                return None
            text = one(request, 1, bytes).decode('utf8')
            if not text.strip() or len(text) > 4096:
                return None
            # The explicit RequestState text is authoritative; recognized_phrase
            # can be partial or absent. Never treat response_state as user input.
            self.seen.append(request_id)
            return {'request_id': request_id, 'text': text,
                    'normalized': normalized_phrase(text)}
        except (ValueError, UnicodeError, TypeError):
            return None


def pause_candidate(event, active_source):
    """Diagnostic allowlist only. This function never dispatches an action."""
    if not event or active_source != 'library':
        return None
    return 'pause' if event['normalized'] in {
        'пауза', 'поставь на паузу', 'поставь музыку на паузу',
        'останови музыку', 'поставь мой плеер на паузу',
    } else None


def _rpc_frame(request_id, method, silent=None):
    import struct
    if not isinstance(request_id, str) or not request_id or len(request_id.encode('utf8')) > 256:
        raise ValueError('Invalid request ID')
    def varint(value):
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        return bytes(result) + bytes([value])
    def field(number, value):
        if isinstance(value, int):
            return varint(number << 3) + varint(value)
        if isinstance(value, str):value = value.encode('utf8')
        return varint(number << 3 | 2) + varint(len(value)) + value
    payload = field(1, method) + field(10, request_id)
    if silent is not None:payload += field(7, int(silent))
    call = field(7, 'AliceCapability') + field(8, payload)
    envelope = field(252, call) + field(253, 210)
    return struct.pack('<I', len(envelope)) + envelope


def stop_conversation_frame(request_id):
    """Legacy experimental STOP=3; proved insufficient for cloud media cancel.

    Micro's proxy signature is stopConversation(), so its request_id field
    should not be assumed to have an effect. Retained for diagnostic history.
    """
    return _rpc_frame(request_id, 3)


def cancel_request_frame(request_id):
    """Build CANCEL_DIALOG=1, silent=true, request_id; no I/O.

    Published Remoting fields: method=1, silent=7, request_id=10. Micro binary
    independently contains AliceCapabilityProxy::cancelDialog(bool,string).
    Actual cancellation semantics still require a device test.
    """
    return _rpc_frame(request_id, 1, silent=True)
