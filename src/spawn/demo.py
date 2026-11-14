import json
import time
from importlib.resources import files
from pathlib import Path

from .contracts import Budget, Criterion, Model, Permissions, Request
from .providers import Response


class DemoProvider:
    is_simulation = True

    def complete(self, model, call, timeout):
        time.sleep(min(0.6, timeout))
        payload = json.loads(call.prompt)
        source = payload["documents"][0]
        paragraphs = source["text"].split("\n\n")
        findings = []
        for criterion in payload["criteria"]:
            prefix = {
                "method": "Method:",
                "evaluation": "Evaluation:",
                "limitations": "Limitations:",
            }[criterion["id"]]
            quote = next(p for p in paragraphs if p.startswith(prefix))
            findings.append(
                {
                    "criterion_id": criterion["id"],
                    "answer": quote,
                    "evidence": [{"source_id": source["source_id"], "quote": quote}],
                }
            )
        return Response(json.dumps({"findings": findings}), 300, 150)


def demo_request(output: Path, seconds: float = 120) -> tuple[Request, list[Model]]:
    source = Path(str(files("spawn.templates").joinpath("source.txt"))).resolve()
    request = Request(
        goal="Explain an agent-scheduling proposal, its evaluation, and limitations.",
        sources=(source,),
        criteria=(
            Criterion("method", "Explain the proposed method", priority=3),
            Criterion("evaluation", "Describe the evaluation", priority=2),
            Criterion("limitations", "Identify limitations and deferred work"),
        ),
        budget=Budget(0.1, seconds),
        permissions=Permissions(
            (source.parent,), output, ("demo-fast", "demo-slow"), True
        ),
        task_class="synthetic-ui-demo",
    )
    common = {
        "provider": "openai",
        "base_url": "http://127.0.0.1:1/v1",
        "input_usd_per_million": 1,
        "output_usd_per_million": 4,
        "input_tokens_per_second": 2000,
        "latency_seconds": 1,
        "max_output_tokens": 750,
    }
    return request, [
        Model(
            name="demo-fast",
            model_id="synthetic-fast",
            output_tokens_per_second=100,
            **common,
        ),
        Model(
            name="demo-slow",
            model_id="synthetic-slow",
            output_tokens_per_second=20,
            **common,
        ),
    ]
