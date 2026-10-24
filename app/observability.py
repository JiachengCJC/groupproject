"""Explicit MLflow experiment and trace export; failures never erase an analysis."""
import hashlib
import json
import math
import os
import time


def log_analysis(result, settings, run_id=None):
    uri = getattr(settings, "mlflow_tracking_uri", "")
    if not uri:
        return {"enabled": False, "status": "disabled"}
    client,run,root=None,None,None
    try:
        if getattr(settings, "local_only", False) and uri.startswith(("http://", "https://")):
            from .providers import Providers
            Providers(settings).require_local_endpoint(uri)
            # MLflow owns its HTTP transport; reject proxy configuration rather
            # than allowing local-only traces to leave through an external proxy.
            if any(os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")):
                return {"enabled": True, "status": "failed", "error": "Local-only MLflow requires proxy environment variables to be cleared."}
        import mlflow
        from mlflow import MlflowClient
        # Tracing's SDK destination is process-wide even when the tracking client has a URI.
        # Each worker has one administrator-configured destination.
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        workspace = result.get("workspace_id", "default")
        experiment_name = "market-lens-" + workspace
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is None:
            try:
                experiment_id = client.create_experiment(experiment_name)
            except Exception:
                experiment_id = client.get_experiment_by_name(experiment_name).experiment_id
        else:
            experiment_id = experiment.experiment_id
        run = client.create_run(experiment_id, tags={"market_lens.run_id": run_id or "direct",
                                                     "market_lens.agent_mode": result.get("agent_mode", ""),
                                                     "market_lens.decision_mode": result.get("decision_mode", "")})
        measurement = result.get("measurement", {})
        for key, value in measurement.items():
            if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                client.log_metric(run.info.run_id, key, value)
        client.log_param(run.info.run_id, "model", result.get("model") or "baseline")
        client.log_param(run.info.run_id, "question_sha256", hashlib.sha256(result["question"].encode()).hexdigest())
        # Source text is opt-in. The application itself retains the complete evidence snapshot.
        include_content = getattr(settings, "trace_content", False)
        evidence_ids = [e["id"] for e in result.get("evidence", [])]
        tools = result.get("tool_trace", [])
        redacted_tools = [{k: v for k, v in t.items() if k in {"id", "agent", "specialist", "tool", "kind", "status", "elapsed_ms", "step", "evidence_ids"}} for t in tools]
        safe_measurement={key:value for key,value in measurement.items()
                          if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)}
        safe_measurement["provider_usage"]=[{key:value for key,value in usage.items()
                                             if key in {"provider","model","operation","input_tokens","output_tokens","prompt_tokens","completion_tokens","total_tokens","request_count"}}
                                            for usage in measurement.get("provider_usage",[]) if isinstance(usage,dict)]
        snapshot = result if include_content else {"plan": {"task_count": len(result.get("plan", {}).get("tasks", []))},
                                                  "evidence_ids": evidence_ids, "tool_trace": redacted_tools,
                                                  "measurement": safe_measurement}
        client.log_dict(run.info.run_id, snapshot, "analysis.json")
        end_ns = time.time_ns()
        start_ns = end_ns - int(max(0, measurement.get("elapsed_ms", 0)) * 1_000_000)
        root = client.start_trace("Market Lens analysis", span_type="AGENT", experiment_id=experiment_id,
                                  run_id=run.info.run_id, start_time_ns=start_ns,
                                  inputs={"question": result["question"]} if include_content else {"question_sha256": hashlib.sha256(result["question"].encode()).hexdigest()},
                                  attributes={"recording_mode": "post_execution", "run_id": run_id or "direct"})
        if "NO_OP" in root.trace_id:
            raise RuntimeError("MLflow did not create a real trace.")
        for index, tool in enumerate(tools):
            duration_ns = min(end_ns-start_ns, int(max(0, tool.get("elapsed_ms", 0)) * 1_000_000))
            span = client.start_span(f"{index+1}. {tool.get('tool', tool.get('kind', 'tool'))}", root.trace_id, root.span_id,
                                     span_type="TOOL", start_time_ns=end_ns-duration_ns,
                                     inputs=tool if include_content else redacted_tools[index],
                                     attributes={"timing_note": "Recorded duration; tool start alignment is reconstructed."})
            client.end_span(root.trace_id, span.span_id, outputs={"status": tool.get("status", "completed")}, end_time_ns=end_ns)
        client.end_trace(root.trace_id, outputs={"choice": result["decision"]["choice"], "evidence_ids": evidence_ids}, end_time_ns=end_ns)
        mlflow.flush_trace_async_logging()
        client.get_trace(root.trace_id)
        client.set_terminated(run.info.run_id)
        return {"enabled": True, "status": "recorded", "run_id": run.info.run_id, "trace_id": root.trace_id}
    except Exception as exc:
        # A partial export must not remain a permanently "running" experiment.
        if client is not None and root is not None:
            try:client.end_trace(root.trace_id,status="ERROR",outputs={"export_status":"failed"})
            except Exception:pass
        if client is not None and run is not None:
            try:client.set_terminated(run.info.run_id,status="FAILED")
            except Exception:pass
        return {"enabled": True, "status": "failed", "error": f"MLflow export failed ({type(exc).__name__}); analysis remains saved locally."}
