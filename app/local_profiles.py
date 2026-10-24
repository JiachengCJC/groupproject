"""Local model profiles with explicit boundaries and reproducible routing."""
from copy import deepcopy
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .providers import Providers, ProviderError


POLICY_VERSION = "local-profiles-v1"
LocalProfile = Literal["custom", "fast", "quality", "auto"]
NamedProfile = Literal["fast", "quality", "auto"]


class LocalProfileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    policy_version: Literal["local-profiles-v1"] = POLICY_VERSION
    profile: NamedProfile
    fast_model: str = Field(min_length=1, max_length=160)
    quality_model: str = Field(min_length=1, max_length=160)
    model_digests: dict[str, str] = Field(default_factory=dict, max_length=2)
    thinking: Literal["off", "on", "default"] = "off"
    context_tokens: int = Field(default=8192, ge=512, le=262144)
    max_output_tokens: int = Field(default=2400, ge=256, le=8192)


def planner_model(snapshot):
    return snapshot["quality_model"] if snapshot["profile"] == "quality" else snapshot["fast_model"]


def required_models(snapshot):
    if snapshot["profile"] == "auto":
        return list(dict.fromkeys([snapshot["fast_model"], snapshot["quality_model"]]))
    return [planner_model(snapshot)]


def configured_snapshot(profile, settings):
    return LocalProfileSnapshot(profile=profile, fast_model=settings.ollama_fast_model,
                                quality_model=settings.ollama_quality_model, thinking=settings.ollama_thinking,
                                context_tokens=settings.ollama_context, max_output_tokens=settings.ollama_num_predict).model_dump()


def enforce_local_profile_policy(request, settings, snapshot):
    if request.get("agent_mode") != "ollama" or request.get("decision_mode") != "llm":
        raise ProviderError("Local Fast, Quality and Auto require Ollama analysis and Model recommendation decisions.")
    if request.get("judge_mode") == "live":
        raise ProviderError("Local profiles cannot use a cloud evaluation judge. Choose a local judge or no judge.")
    if settings.orchestration != "langgraph":
        raise ProviderError("Local profiles require LangGraph orchestration.")
    local = Providers(replace(settings, local_only=True))
    for model in required_models(snapshot):
        local.assert_mode("ollama", model)
    if request.get("judge_mode") == "ollama":
        local.assert_mode("ollama", request.get("judge_model") or settings.ollama_model)
    if settings.retrieval_mode != "lexical":
        if settings.embedding_provider != "ollama":
            raise ProviderError("Local profiles require Ollama embeddings or lexical retrieval; cloud embeddings are not allowed.")
        local.assert_mode("ollama", settings.embedding_model)
        if settings.qdrant_url:
            local.require_local_endpoint(settings.qdrant_url)


def profile_catalog(settings):
    descriptions = {
        "fast": ("Local Fast", "Use the smaller local model for planning, analysis and recommendations."),
        "quality": ("Local Quality", "Use the larger local model throughout the analysis; allow more time and memory."),
        "auto": ("Local Auto", "Plan with the smaller model, then use the larger model for complex evidence tasks. Routing reasons are saved."),
    }
    catalog = {}
    for profile, (label, description) in descriptions.items():
        snapshot = configured_snapshot(profile, settings)
        reason = None
        try:
            enforce_local_profile_policy({"agent_mode": "ollama", "decision_mode": "llm"}, settings, snapshot)
        except ProviderError as exc:
            reason = str(exc)
        catalog[profile] = {
            "label": label, "description": description,
            "models": {"planner": planner_model(snapshot),
                       "analysis": snapshot["quality_model"] if profile in {"quality", "auto"} else snapshot["fast_model"]},
            "required_models": required_models(snapshot), "available": reason is None,
            "unavailable_reason": reason,
        }
    return catalog


def prepare_local_profile(request, settings, providers, *, refresh=False):
    """Freeze a new request, or check a worker's saved snapshot before execution.

    HTTP validation always refreshes the snapshot from trusted server settings.
    A queued run retains that snapshot even when profile defaults subsequently
    change; changed or missing installed weights fail visibly instead of silently
    substituting another model.
    """
    profile = request.get("local_profile", "custom")
    if profile not in {"custom", "fast", "quality", "auto"}:
        raise ProviderError("Unknown local execution profile.")
    result = deepcopy(request)
    if profile == "custom":
        result.pop("local_profile_snapshot", None)
        return result
    old_snapshot = request.get("local_profile_snapshot")
    if isinstance(old_snapshot, LocalProfileSnapshot):
        old_snapshot = old_snapshot.model_dump()
    snapshot = (configured_snapshot(profile, settings) if refresh or old_snapshot is None
                else LocalProfileSnapshot.model_validate(old_snapshot).model_dump())
    if snapshot["profile"] != profile:
        raise ProviderError("The saved local profile does not match the requested profile.")
    if (snapshot["thinking"], snapshot["context_tokens"], snapshot["max_output_tokens"]) != (
            settings.ollama_thinking, settings.ollama_context, settings.ollama_num_predict):
        raise ProviderError("Local generation settings changed after this run was queued. Create a new analysis to use the updated settings.")
    enforce_local_profile_policy(result, settings, snapshot)
    expected = planner_model(snapshot)
    explicit = request.get("model")
    # A saved schedule can be revalidated after an administrator changes profile
    # defaults. Its former server-selected model is not a manual override.
    previous_planner = None
    if refresh and old_snapshot:
        previous = LocalProfileSnapshot.model_validate(old_snapshot).model_dump()
        if previous["profile"] == profile:
            previous_planner = planner_model(previous)
    if explicit and explicit not in {expected, previous_planner}:
        raise ProviderError("A named local profile selects its own models. Clear the custom model or choose Custom.")
    inventory = providers.ollama_models()
    if not inventory.get("available"):
        raise ProviderError(inventory.get("error") or "Cannot reach the local Ollama service.")
    available = set(inventory.get("models", []))
    missing = [name for name in required_models(snapshot) if name not in available]
    if missing:
        raise ProviderError("This local profile needs installed models: " + ", ".join(missing) + ". Download them with ollama pull first.")
    details = inventory.get("details", {})
    current_digests = {name: details[name]["digest"] for name in required_models(snapshot)
                       if isinstance(details.get(name), dict) and isinstance(details[name].get("digest"), str)
                       and details[name]["digest"]}
    if refresh or old_snapshot is None:
        snapshot["model_digests"] = current_digests
    elif any(current_digests.get(name) != digest for name, digest in snapshot["model_digests"].items()):
        raise ProviderError("Installed model weights changed after this run was queued. Create a new analysis to use the updated models.")
    result.update(local_profile=profile, model=expected, local_profile_snapshot=snapshot)
    return result


def initial_routing(snapshot, settings):
    profile = snapshot["profile"]
    reasons = {"fast": "Local Fast uses the configured smaller model for every text-analysis stage.",
               "quality": "Local Quality uses the configured larger model for every text-analysis stage.",
               "auto": "Local Auto starts with the smaller planner and selects an analysis model from the validated plan."}
    return {"profile": profile, "planner_model": planner_model(snapshot), "analysis_model": planner_model(snapshot),
            "reason": reasons[profile], "policy_version": snapshot["policy_version"], "stages": {},
            "vision_model": settings.ollama_vision_model or None,
            "embedding_model": settings.embedding_model if settings.retrieval_mode != "lexical" else None,
            "model_digests": deepcopy(snapshot["model_digests"])}


def choose_auto_analysis(snapshot, request, plan, dataset_ids):
    """Route using observed task structure, never a model's confidence score."""
    reasons = []
    tasks = plan.get("tasks", [])
    routes = {task["route"] for task in tasks}
    if len(set(dataset_ids)) > 1:
        reasons.append("multiple selected data tables")
    if "hybrid" in routes or {"sql", "rag"} <= routes:
        reasons.append("combined table and document evidence")
    if len(tasks) > 1:
        reasons.append("multiple specialist tasks")
    if request.get("reviewer_notes", "").strip():
        reasons.append("a revision with reviewer instructions")
    if request.get("candidate_actions"):
        reasons.append("comparison of explicit candidate actions")
    if reasons:
        return snapshot["quality_model"], "Local Auto selected the larger model for " + "; ".join(reasons) + "."
    return snapshot["fast_model"], "Local Auto retained the smaller model for one specialist using a single evidence route."
