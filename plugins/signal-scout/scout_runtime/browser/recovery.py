from dataclasses import dataclass
import json
import os
import re
import socket
import struct
import time
import uuid

from ._errors import ControlUnavailable, OwnershipFailure
from .control import validate_endpoint


@dataclass(frozen=True)
class OwnedEndpoint:
    generation: int
    endpoint: str


class RecoveryClient:
    def __init__(self, path, nonce, settings):
        if path != '/tmp/scout-recovery/owner.sock' or not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{64}', nonce):
            raise ValueError('Invalid Scout recovery grant.')
        self.path, self.nonce, self.settings = path, nonce, settings

    @classmethod
    def from_environment(cls, settings):
        path, nonce = os.environ.get('SCOUT_RECOVERY_SOCKET'), os.environ.get('SCOUT_RECOVERY_NONCE')
        generation = os.environ.get('SCOUT_BROWSER_GENERATION')
        if path is nonce is generation is None:
            return None, 1
        if not isinstance(generation, str) or not re.fullmatch('[1-9][0-9]*', generation):
            raise ValueError('Invalid Scout recovery generation.')
        return cls(path, nonce or '', settings), int(generation)

    def request(self, operation, generation, authority, timeout):
        authority.check()
        deadline = time.monotonic() + timeout
        request = {'nonce': self.nonce, 'operation': operation, 'generation': generation,
                   'request_id': uuid.uuid4().hex, 'deadline': time.time() + timeout}
        payload = json.dumps(request, separators=(',', ':')).encode()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        authority.own_socket(connection)

        def remaining():
            authority.check()
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError('Browser owner response deadline reached.')
            connection.settimeout(value)

        def receive(size):
            result = bytearray()
            while len(result) < size:
                remaining()
                part = connection.recv(size - len(result))
                if not part:
                    authority.check()
                    raise ControlUnavailable('Browser owner channel closed.')
                result.extend(part)
            return result

        try:
            remaining()
            connection.connect(self.path)
            remaining()
            connection.sendall(struct.pack('!I', len(payload)) + payload)
            size = struct.unpack('!I', receive(4))[0]
            if not 0 < size <= 4096:
                raise ValueError
            response = json.loads(receive(size))
            if (not isinstance(response, dict)
                    or set(response) != {'status', 'generation', 'endpoint', 'restarted', 'reason'}
                    or response['status'] not in {'ready', 'unavailable', 'busy', 'failed', 'rejected'}
                    or type(response['generation']) is not int or response['generation'] < generation
                    or type(response['restarted']) is not bool
                    or response['reason'] not in {None, 'ownership_mismatch', 'stale_generation',
                        'attempt_expired', 'repair_in_progress', 'invalid_request', 'request_conflict',
                        'expired_or_closed', 'repair_failed', 'closing'}):
                raise ValueError
            if response['status'] == 'ready':
                network = self.settings['scout_network']
                validate_endpoint(response['endpoint'], network['browser_address'], network['relay_port'])
                response['owned'] = OwnedEndpoint(response['generation'], response['endpoint'])
            elif response['endpoint'] is not None:
                raise ValueError
            if response['reason'] in {'ownership_mismatch', 'invalid_request', 'request_conflict'}:
                raise OwnershipFailure('Browser owner grant or resource identity failed.')
            authority.check()
            return response
        except (ValueError, KeyError, TypeError, struct.error):
            raise OwnershipFailure('Invalid browser owner response.') from None
        except OSError:
            authority.check()
            raise ControlUnavailable('Browser owner channel unavailable.') from None
        finally:
            connection.close()
