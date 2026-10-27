from types import SimpleNamespace

from job_agent.llm import plain_response, structured_response


class FakeResponses:
    def __init__(self) -> None:
        self.options: dict = {}

    def create(self, **options):
        self.options = options
        return SimpleNamespace(status="completed", output_text="{}")


def test_sol_has_reasoning_room_for_structured_output(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6.1-sol")
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)

    structured_response(client, "Score this job", schema_name="score", schema={"type": "object"}, max_output_tokens=700)

    assert responses.options["model"] == "gpt-6.1-sol"
    assert responses.options["reasoning"] == {"effort": "low"}
    assert responses.options["max_output_tokens"] == 4096
    assert responses.options["text"]["format"]["strict"] is True
    assert responses.options["store"] is False


def test_legacy_model_keeps_original_request_shape(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)

    plain_response(client, "Draft answer", max_output_tokens=800)

    assert responses.options["model"] == "gpt-4o-mini"
    assert responses.options["max_output_tokens"] == 800
    assert "reasoning" not in responses.options

