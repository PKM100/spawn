from __future__ import annotations

import asyncio
import math
import os
import shutil
import tempfile
from importlib.metadata import version

from .contracts import SYSTEM, Call, Model
from .providers import ProviderError, Response

USD_PER_CREDIT = 0.01
USD_PER_LEGACY_REQUEST = 0.04


def _sdk():
    try:
        from copilot import CopilotClient, RuntimeConnection
    except ImportError:
        raise ProviderError(
            "Install Copilot support from the checkout: pip install '.[copilot]'"
        ) from None
    executable = shutil.which("copilot")
    if not executable:
        raise ProviderError("Install and sign in to the GitHub Copilot CLI first")
    return CopilotClient, RuntimeConnection.for_stdio(
        path=executable, args=("--no-auto-update", "--disable-client-telemetry")
    )


def normalize_billing(metrics) -> tuple[float | None, dict]:
    nano = getattr(metrics, "total_nano_aiu", None)
    premium = getattr(metrics, "total_premium_request_cost", None)
    status = getattr(metrics, "ai_credits_status", None)
    status = getattr(status, "value", status)
    if status == "partial":
        return None, {"basis": "unavailable", "ai_credits_status": status}
    if (
        status == "complete"
        and isinstance(nano, (int, float))
        and math.isfinite(nano)
        and nano >= 0
    ):
        credits = nano / 1_000_000_000
        return credits * USD_PER_CREDIT, {
            "basis": "copilot_ai_credits_equivalent",
            "ai_credits": credits,
            "usd_per_credit": USD_PER_CREDIT,
            "note": "Usage equivalent, not incremental invoice spending",
        }
    if isinstance(premium, (int, float)) and math.isfinite(premium) and premium > 0:
        return premium * USD_PER_LEGACY_REQUEST, {
            "basis": "copilot_legacy_request_equivalent",
            "premium_requests": premium,
            "usd_per_legacy_request": USD_PER_LEGACY_REQUEST,
            "note": "Legacy request equivalent, not token-priced API spending",
        }
    return None, {"basis": "unavailable"}


class CopilotProvider:
    """Fresh, tool-free SDK sessions using the installed CLI's signed-in account."""

    def complete(self, model: Model, call: Call, timeout: float) -> Response:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ProviderError(
                "Use a worker thread for the synchronous runtime in async applications"
            )
        try:
            return asyncio.run(
                asyncio.wait_for(self._complete(model, call, timeout), timeout)
            )
        except TimeoutError:
            raise ProviderError(
                "Copilot timed out; its usage may still be billed"
            ) from None
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                f"Copilot failed ({type(error).__name__}); no automatic retry"
            ) from None

    async def _complete(self, model: Model, call: Call, timeout: float) -> Response:
        client_type, connection = _sdk()
        from copilot.generated.rpc import PermissionDecisionReject

        with tempfile.TemporaryDirectory(prefix="spawn-copilot-") as directory:
            async with client_type(
                connection=connection,
                working_directory=directory,
                base_directory=directory,
                mode="empty",
                log_level="error",
                env={**os.environ, "SPAWN_ACTIVE": "1"},
            ) as client:
                session = await client.create_session(
                    model=model.model_id,
                    reasoning_effort=model.reasoning_effort,
                    system_message={"mode": "replace", "content": SYSTEM},
                    available_tools=[],
                    on_permission_request=lambda *_: PermissionDecisionReject(),
                    skip_custom_instructions=True,
                    enable_config_discovery=False,
                    enable_skills=False,
                    enable_file_hooks=False,
                    enable_host_git_operations=False,
                    enable_session_store=False,
                    enable_session_telemetry=False,
                    infinite_sessions={"enabled": False},
                    session_limits={
                        "max_ai_credits": max(
                            30, model.per_call_usd_reserve / USD_PER_CREDIT
                        )
                    },
                )
                async with session:
                    reply = await session.send_and_wait(
                        call.prompt
                        + "\nKeep the entire response under approximately "
                        + f"{call.max_output_tokens} tokens.",
                        timeout=timeout,
                    )
                    if reply is None:
                        raise ProviderError("Copilot returned no final answer")
                    metrics = await session.rpc.usage.get_metrics()
                    cost, billing = normalize_billing(metrics)
                    billing["sdk_version"] = version("github-copilot-sdk")
                    billing["model_id"] = model.model_id
                    billing["model_usage"] = {
                        name: metric.usage.to_dict()
                        for name, metric in metrics.model_metrics.items()
                    }
                    return Response(
                        reply.data.content,
                        sum(
                            m.usage.input_tokens for m in metrics.model_metrics.values()
                        ),
                        sum(
                            m.usage.output_tokens
                            for m in metrics.model_metrics.values()
                        ),
                        reported_cost_usd=cost,
                        billing=billing,
                    )


def list_models() -> list[dict]:
    async def discover():
        client_type, connection = _sdk()
        with tempfile.TemporaryDirectory(prefix="spawn-models-") as directory:
            async with client_type(
                connection=connection,
                working_directory=directory,
                base_directory=directory,
                mode="empty",
                log_level="error",
            ) as client:
                return [m.to_dict() for m in await client.list_models()]

    return asyncio.run(asyncio.wait_for(discover(), timeout=60))
