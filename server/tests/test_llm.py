"""Vérifie la forme des requêtes envoyées à l'API Claude et l'analyse des réponses (sans réseau)."""

import json

import anthropic
import httpx2
import pytest

from app.llm import ClaudeTutor, LLMError


def sse(blocks: list[dict], stop_reason: str = "end_turn") -> bytes:
    events = [("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 0}}})]
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            events.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "text", "text": ""}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "text_delta", "text": block["text"]}}))
        else:  # tool_use
            events.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}}))
            events.append(("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(block["input"])}}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    events.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason,
                                                                        "stop_sequence": None},
                                     "usage": {"output_tokens": 5}}))
    events.append(("message_stop", {"type": "message_stop"}))
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


class FakeAPI:
    def __init__(self, responses: list[bytes]):
        self.responses = responses
        self.requests: list[dict] = []
        self.headers: list = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return httpx2.Response(200, content=self.responses.pop(0), headers={"content-type": "text/event-stream"})

    def tutor(self) -> ClaudeTutor:
        client = anthropic.AsyncAnthropic(
            api_key="test", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.handler))
        )
        return ClaudeTutor("claude-opus-5-5", "MPSI", 45, client=client)


ASSIGNMENT = {"titre": "T", "notions": ["n"], "enonce": "E", "corrige": "C",
              "criteres_validation": ["c"], "duree_estimee_min": 40}


async def test_generate_assignment_request():
    api = FakeAPI([sse([{"type": "text", "text": json.dumps(ASSIGNMENT)}])])
    result = await api.tutor().generate_assignment("Chapitre : suites", [], consignes="court")
    assert result == ASSIGNMENT
    body = api.requests[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "server-side-fallback-2026-07-01" in api.headers[0]["anthropic-beta"]
    assert "Chapitre : suites" in body["system"]
    assert "court" in body["messages"][0]["content"]


async def test_grade_sends_images():
    report = {"verdict": "valide", "note_sur_20": 14, "resume_parent": "r", "retour_eleve": "b", "points_a_revoir": []}
    api = FakeAPI([sse([{"type": "text", "text": json.dumps(report)}])])
    result = await api.tutor().grade({"enonce": "E", "corrige": "C", "criteres": "[]"}, [b"\xff\xd8a", b"\xff\xd8b"])
    assert result == report
    images = [c for c in api.requests[0]["messages"][0]["content"] if c["type"] == "image"]
    assert len(images) == 2 and images[0]["source"]["media_type"] == "image/jpeg"


async def test_supervisor_tool_loop():
    api = FakeAPI([
        sse([{"type": "text", "text": "Je modifie."},
             {"type": "tool_use", "id": "tu1", "name": "modifier_programme", "input": {"texte": "Séries"}}],
            stop_reason="tool_use"),
        sse([{"type": "text", "text": "C'est fait."}]),
    ])
    calls = []

    async def run_tool(name, args):
        calls.append((name, args))
        return "Programme mis à jour."

    new_messages, answer = await api.tutor().supervisor_turn([], "Passe aux séries", run_tool)
    assert calls == [("modifier_programme", {"texte": "Séries"})]
    assert answer == "C'est fait."
    assert [m["role"] for m in new_messages] == ["user", "assistant", "user", "assistant"]
    second = api.requests[1]["messages"]
    assert second[-1]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "Programme mis à jour."}
    assert all(t["strict"] for t in api.requests[0]["tools"])
    json.dumps(new_messages)  # stockable tel quel en base


async def test_refusal_raises():
    api = FakeAPI([sse([], stop_reason="refusal")])
    with pytest.raises(LLMError):
        await api.tutor().generate_assignment("", [])
