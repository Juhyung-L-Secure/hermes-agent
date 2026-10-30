from copy import deepcopy
from enum import Enum
import re

from ..actions import ACTION_FIELDS
from ..network.urls import validate_url
from ._errors import ActionRejected


class _FormatRule(Enum):
    REQUEST_OBJECT = ("request_object", "request", "Request must be an object.")
    REQUEST_FIELDS = ("request_fields", "request", "Request requires action and args; only optional activity is allowed.")
    ACTION_STRING = ("action_string", "action", "action must be a string.")
    ACTION_SUPPORTED = ("action_supported", "action", "action must name a supported browser_scout action.")
    ARGS_OBJECT = ("args_object", "args", "args must be an object.")
    ARGS_FIELDS = ("args_fields", "args", None)
    ARGS_STRINGS = ("args_strings", "args", "Every value in args must be a string.")
    ACTIVITY_STRING = ("activity_string", "activity", "activity must be a string when supplied.")
    REF_SYNTAX = ("ref_syntax", "args.ref", "args.ref must match @s followed by a positive integer, from a current snapshot.")
    URL_SYNTAX = ("url_syntax", "args.url", "args.url must be an absolute HTTP(S) URL without embedded credentials.")
    KEY_NONEMPTY = ("key_nonempty", "args.key", "args.key must be a nonempty key or chord string.")
    SCROLL_DIRECTION = ("scroll_direction", "args.direction", "args.direction must be up or down.")
    TARGET_ID_NONEMPTY = ("target_id_nonempty", "args.target_id", "args.target_id must be a nonempty ID from list_tabs.")

    def __init__(self, code, field, explanation):
        self.code, self.field, self.explanation = code, field, explanation


class _FormatRejected(ActionRejected):
    def __init__(self, rule, *, action=None):
        self.rule = rule
        explanation = rule.explanation
        if rule is _FormatRule.ARGS_FIELDS:
            fields = sorted(ACTION_FIELDS[action])
            expected = "exactly: " + ", ".join(fields) if fields else "no fields"
            explanation = f"args for {action} must contain {expected}."
        super().__init__(explanation)


def copy_request(request, permissions):
    if type(request) is not dict:
        raise _FormatRejected(_FormatRule.REQUEST_OBJECT)
    if not {"action", "args"} <= request.keys() or request.keys() - {"action", "args", "activity"}:
        raise _FormatRejected(_FormatRule.REQUEST_FIELDS)
    if not isinstance(request["action"], str):
        raise _FormatRejected(_FormatRule.ACTION_STRING)
    if type(request["args"]) is not dict:
        raise _FormatRejected(_FormatRule.ARGS_OBJECT)
    if "activity" in request and not isinstance(request["activity"], str):
        raise _FormatRejected(_FormatRule.ACTIVITY_STRING)
    action, args = request["action"], request["args"]
    if action not in ACTION_FIELDS:
        raise _FormatRejected(_FormatRule.ACTION_SUPPORTED)
    if permissions.get(action, "blocked") == "blocked":
        raise ActionRejected("Browser action is blocked.")
    if set(args) != ACTION_FIELDS[action]:
        raise _FormatRejected(_FormatRule.ARGS_FIELDS, action=action)
    if not all(isinstance(value, str) for value in args.values()):
        raise _FormatRejected(_FormatRule.ARGS_STRINGS)
    if action in {"click", "type"} and not re.fullmatch(r"@s[1-9][0-9]*", args["ref"]):
        raise _FormatRejected(_FormatRule.REF_SYNTAX)
    if action == "keypress" and not args["key"]:
        raise _FormatRejected(_FormatRule.KEY_NONEMPTY)
    if action == "scroll" and args["direction"] not in {"up", "down"}:
        raise _FormatRejected(_FormatRule.SCROLL_DIRECTION)
    if action == "switch_tab" and not args["target_id"]:
        raise _FormatRejected(_FormatRule.TARGET_ID_NONEMPTY)
    if action == "navigate":
        try:
            validate_url(args["url"])
        except ValueError:
            raise _FormatRejected(_FormatRule.URL_SYNTAX) from None
    return deepcopy(request)
