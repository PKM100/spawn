from __future__ import annotations

from .contracts import Model
from .providers import ProviderError


def copilot_profiles(model_ids: list[str] | None = None) -> list[Model]:
    if model_ids is not None and (
        not isinstance(model_ids, list)
        or not model_ids
        or any(not isinstance(item, str) or not item for item in model_ids)
    ):
        raise ValueError("copilot_models must be a nonempty list of model IDs")
    from .copilot_provider import USD_PER_CREDIT, list_models

    try:
        catalog = list_models()
    except ProviderError:
        raise
    except Exception as error:
        raise ProviderError(
            f"Copilot discovery failed ({type(error).__name__}). "
            "Check the CLI installation and sign-in, or choose another provider."
        ) from None
    profiles = []
    for entry in catalog:
        if entry.get("policy", {}).get("state") not in {None, "enabled"}:
            continue
        prices = entry.get("billing", {}).get("tokenPrices", {})
        if not {"inputPrice", "outputPrice", "batchSize"} <= prices.keys():
            continue
        limits = entry.get("capabilities", {}).get("limits", {})
        batch = prices["batchSize"]
        if not isinstance(batch, (int, float)) or batch <= 0:
            continue
        context = min(
            limits.get("max_context_window_tokens", 32000),
            limits.get("max_prompt_tokens", 32000),
            prices.get("contextMax", 32000),
            prices.get("maxPromptTokens", 32000),
        )
        efforts = entry.get("supportedReasoningEfforts", [])
        profiles.append(
            Model(
                name=entry["id"],
                provider="copilot",
                model_id=entry["id"],
                base_url="https://github.com",
                input_usd_per_million=prices["inputPrice"]
                * USD_PER_CREDIT
                * 1_000_000
                / batch,
                output_usd_per_million=prices["outputPrice"]
                * USD_PER_CREDIT
                * 1_000_000
                / batch,
                input_tokens_per_second=2000,
                output_tokens_per_second=100,
                latency_seconds=3,
                context_tokens=context,
                max_output_tokens=min(limits.get("max_output_tokens", 4000), 4000),
                per_call_usd_reserve=0.30,
                reasoning_effort="none"
                if "none" in efforts
                else ("low" if "low" in efforts else None),
            )
        )
    profiles.sort(key=lambda m: m.input_usd_per_million + m.output_usd_per_million)
    if model_ids is not None:
        missing = set(model_ids) - {m.name for m in profiles}
        if missing:
            raise ValueError(
                "Unavailable or unpriced Copilot models: " + ", ".join(sorted(missing))
            )
        profiles = [m for m in profiles if m.name in model_ids]
    if not profiles:
        raise ProviderError(
            "No enabled Copilot models with token pricing were discovered. "
            "Use an explicit model profile or another provider."
        )
    return profiles
