"""Optional user-configured API pricing. Unknown usage is never reported as free."""
import math


def apply_cost(result, settings):
    measurement = result["measurement"]
    usage = measurement.get("provider_usage", [])
    input_rate = getattr(settings, "openai_input_usd_per_million", -1)
    output_rate = getattr(settings, "openai_output_usd_per_million", -1)
    version = getattr(settings, "price_version", "")
    configured = (bool(version) and all(isinstance(v, (float, int)) and not isinstance(v, bool)
                                       and math.isfinite(v) and v >= 0 for v in (input_rate, output_rate)))
    decision_rate = getattr(settings, "openai_decisions_input_usd_per_million", -1)
    decision_version = getattr(settings, "openai_decisions_price_version", "")
    decisions_configured = (bool(decision_version) and isinstance(decision_rate, (float, int))
                            and not isinstance(decision_rate, bool) and math.isfinite(decision_rate) and decision_rate >= 0)
    components, known_total, unknown = [], 0.0, False
    for item in usage:
        provider = item.get("provider", result.get("agent_mode"))
        model = item.get("model", result.get("model"))
        if provider in {"ollama", "local"}:
            continue
        inputs = item.get("input_tokens", item.get("prompt_tokens"))
        outputs = item.get("output_tokens", item.get("completion_tokens"))
        valid_tokens = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (inputs, outputs))
        cost = None
        component_version = None
        if provider in {"live", "cloud", "openai"} and configured and model == settings.openai_model and valid_tokens:
            cost = (inputs*input_rate+outputs*output_rate)/1_000_000
            component_version = version
        elif (provider == "openai_decisions" and decisions_configured
              and model == settings.openai_decision_model and valid_tokens):
            # The Decisions API charges input tokens, with no output-token charge.
            # Cache discounts are excluded; this uses the explicit standard input rate.
            cost = inputs*decision_rate/1_000_000
            component_version = decision_version
        if cost is not None:
            known_total += cost
        else:
            unknown = True
        components.append({"provider": provider, "model": model, "input_tokens": inputs, "output_tokens": outputs,
                           "estimated_usd": cost, "price_version": component_version})
    cloud_run = result.get("agent_mode") == "live" or result.get("decision_mode") in {"openai_decisions", "jev"}
    if cloud_run and not components:
        unknown = True
    embedding_calls = any(t.get("tool") == "retrieval" for t in result.get("tool_trace", []))
    if not result.get("tool_trace"):
        embedding_calls = any(e.get("kind") == "document" for e in result.get("evidence", []))
    unpriced_embeddings = (result.get("agent_mode") != "local" and embedding_calls
                           and getattr(settings, "retrieval_mode", "lexical") != "lexical"
                           and getattr(settings, "embedding_provider", "lexical") in {"live", "cloud"})
    if unpriced_embeddings:
        unknown = True
        components.append({"provider": "cloud_embeddings", "model": getattr(settings, "embedding_model", ""),
                           "estimated_usd": None, "note": "Embedding pricing and usage are not included in chat-model rates."})
    measurement["estimated_cost"] = None if unknown else round(known_total, 10)
    measurement["cost"] = {"currency": "USD", "price_version": version or None,
                           "complete": not unknown, "priced_subtotal": round(known_total, 10), "components": components,
                           "note": "Configured API estimates only; excludes hardware, hosting, taxes and provider discounts. Decisions uses separately configured input pricing; output is not charged."}
    return result
