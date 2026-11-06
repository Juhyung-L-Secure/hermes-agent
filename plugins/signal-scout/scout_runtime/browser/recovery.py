from dataclasses import dataclass
import json
import logging
import math
import os
import re
import socket
import struct
import time
import uuid

from ._errors import ControlUnavailable, OwnershipFailure
from .control import validate_endpoint


logger = logging.getLogger(__name__)


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
        began, phase = time.monotonic(), 'connect'
        deadline = time.monotonic() + timeout
        request = {'nonce': self.nonce, 'operation': operation, 'generation': generation,
                   'request_id': uuid.uuid5(uuid.NAMESPACE_OID, self.nonce + operation + authority.call_id).hex, 'deadline': time.time() + timeout}
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
            phase = 'send'
            remaining()
            connection.sendall(struct.pack('!I', len(payload)) + payload)
            phase = 'response_header'
            size = struct.unpack('!I', receive(4))[0]
            if not 0 < size <= 4096:
                raise ValueError
            phase = 'response_body'
            response = json.loads(receive(size))
            if (not isinstance(response, dict)
                    or set(response) not in ({'status', 'generation', 'endpoint', 'restarted', 'reason'},
                        {'status', 'generation', 'endpoint', 'restarted', 'reason', 'budget'})
                    or response['status'] not in {'ready', 'unavailable', 'busy', 'failed', 'rejected'}
                    or type(response['generation']) is not int or response['generation'] < generation
                    or type(response['restarted']) is not bool
                    or response['reason'] not in {None, 'ownership_mismatch', 'stale_generation',
                        'attempt_expired', 'repair_in_progress', 'invalid_request', 'request_conflict',
                        'expired_or_closed', 'repair_failed', 'closing', 'allowance_exhausted'}):
                raise ValueError
            if 'budget' in response:
                budget = response['budget']
                if (not isinstance(budget, dict) or set(budget) != {'remaining_replacements', 'repair_seconds_remaining', 'pending'}
                        or type(budget['pending']) is not bool
                        or type(budget['remaining_replacements']) is not int or budget['remaining_replacements'] < 0
                        or type(budget['repair_seconds_remaining']) not in (int, float)
                        or not math.isfinite(budget['repair_seconds_remaining']) or budget['repair_seconds_remaining'] < 0):
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
            logger.exception('Browser owner RPC rejected operation=%s phase=%s generation=%s elapsed_ms=%s',
                             operation, phase, generation, int((time.monotonic() - began) * 1000))
            raise OwnershipFailure('Invalid browser owner response.') from None
        except OSError:
            logger.exception('Browser owner RPC failed operation=%s phase=%s generation=%s elapsed_ms=%s',
                             operation, phase, generation, int((time.monotonic() - began) * 1000))
            authority.check()
            raise ControlUnavailable('Browser owner channel unavailable.') from None
        finally:
            connection.close()
