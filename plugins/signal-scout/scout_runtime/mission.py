from dataclasses import dataclass
from enum import IntEnum


@dataclass(frozen=True)
class MissionRequest:
    text: str

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("Research request must be nonempty.")


class MissionOutcome(IntEnum):
    SUCCESS = 0
    FAILURE = 1
    CANCELLED = 130

    @classmethod
    def from_native(cls, result):
        if (result.get("completed") is True and not result.get("failed")
                and not result.get("interrupted")
                and isinstance(result.get("final_response"), str)
                and result["final_response"].strip()):
            return cls.SUCCESS
        return cls.FAILURE
