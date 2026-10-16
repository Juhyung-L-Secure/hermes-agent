from copy import deepcopy
import json
import logging
import signal
import time

import httpx

_native_send = httpx.Client.send
_main_calls = 0
_review_calls = 0
_scenario = None
_retry_body = None


class BrokenStream(httpx.SyncByteStream):
    def __init__(self, request):
        self.request = request

    def __iter__(self):
        yield b'data: {"type":"response.created","response":{"id":"broken-fixture"}}\n\n'
        raise httpx.ReadError("[Errno 32] Broken pipe", request=self.request)


def _broken(request):
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream(request), request=request)


def _reply(request, body, items):
    final = {"id": "cli-fixture", "model": body["model"], "status": "completed", "output": items,
             "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}}
    events = [{"type": "response.output_item.done", "output_index": index, "item": item}
              for index, item in enumerate(items)]
    events.append({"type": "response.completed", "response": final})
    data = "".join("data: " + json.dumps(item) + "\n\n" for item in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data, request=request)


def _message(text):
    return {"type": "message", "id": "fixture-answer", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _tool(action, args, activity):
    return {"type": "function_call", "id": "item-" + action, "call_id": "call-" + action,
            "name": "browser_scout", "arguments": json.dumps({"action": action, "args": args, "activity": activity}),
            "status": "completed"}


def _result(body):
    outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
    assert outputs, "Native browser result missing from next inference"
    output = outputs[-1]["output"]
    if isinstance(output, list):
        output = output[0]["text"]
    assert output.startswith('<untrusted_tool_result source="browser_scout">\n')
    return json.loads(output.split("\n\n", 1)[1].rsplit("\n</untrusted_tool_result>", 1)[0])


def _send(self, request, **kwargs):
    global _main_calls, _review_calls, _scenario, _retry_body
    if request.url.host != "chatgpt.com" or request.url.path != "/backend-api/codex/responses":
        if request.url.host in {"127.0.0.1", "localhost"}:
            return _native_send(self, request, **kwargs)
        raise AssertionError("Unexpected external HTTP request in offline CLI fixture")
    body = json.loads(request.content)
    if body["model"] == "gpt-6-luna":
        _review_calls += 1
        assert "tools" not in body
        if _scenario in {"cancel", "headed"}:
            time.sleep(90)
        if _scenario == "fatal" or _review_calls == 1:
            text = "malformed technical response"
        elif _scenario == "deny":
            text = '{"verdict":"deny"}'
        else:
            text = '{"verdict":"approve"}'
        return _reply(request, body, [_message(text)])
    assert body["model"] == "gpt-5.6-luna"
    _main_calls += 1
    names = [item["name"] for item in body["tools"]]
    assert names == ["browser_scout"], names
    if _main_calls == 1:
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "Research inherited a process alarm"
        initial = json.dumps(body["input"], ensure_ascii=False)
        for scenario in ("success", "exhaust", "fatal", "deny", "cancel", "headed", "startupfail"):
            if "CLI fixture " + scenario in initial:
                _scenario = scenario
                break
        assert _scenario is not None
        assert initial.count("Start with Google search unless user supplies URL.") == 1
        assert "Follow links actually observed. Don’t invent deep URLs." in initial
        if _scenario == "startupfail":
            return httpx.Response(401, json={"error": {"message": "Offline fixture authentication failure", "type": "invalid_request_error"}}, request=request)
        logger = logging.getLogger("tools.cli_fixture")
        logger.debug("fixture-debug-must-stay-hidden")
        try:
            raise ValueError("fixture native multiline detail " + "glpat-" + "Zx9AbCdEfGhIjKlMnOpQ")
        except ValueError:
            logger.exception("fixture native diagnostic")
        return _reply(request, body, [_tool("navigate", {"url": "http://public.test/cli-mission"}, "Read controlled research source")])
    latest = _result(body)
    if _scenario == "deny":
        assert latest["status"] == "rejected" and latest["execution_started"] is False
        assert _review_calls == 2
        return _reply(request, body, [_message("Controlled denial preserved healthy conversation.")])
    assert latest["status"] == "success"
    if _main_calls == 2:
        snapshot = latest["data"]["snapshot"]
        matches = [row for row in snapshot.splitlines() if '"Inspect source"' in row and "[@s" in row]
        assert len(matches) == 1
        reference = matches[0].rsplit("[", 1)[1][:-1]
        return _reply(request, body, [_tool("click", {"ref": reference}, "Inspect controlled source control")])
    outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
    assert len(outputs) == (3 if _main_calls == 5 else 2) and _review_calls == 3
    if _main_calls == 3:
        _retry_body = deepcopy(body)
        return _broken(request)
    if _main_calls == 5:
        assert _scenario == "success"
        return _reply(request, body, [_message("Controlled research complete. One navigation and one click retained across native retry.")])
    assert _main_calls == 4 and body == _retry_body, "Native retry changed completed history"
    if _scenario == "exhaust":
        return _broken(request)
    return _reply(request, body, [_tool("snapshot", {}, "Observe research result after native retry")])


httpx.Client.send = _send
