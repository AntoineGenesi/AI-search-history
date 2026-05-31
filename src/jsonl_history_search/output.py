from __future__ import annotations

import json


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def render_text(payload: dict) -> str:
    if "install_validation" in payload:
        return render_install_validation_text(payload)
    if "forensics" in payload:
        return render_forensics_text(payload)
    if "manifest_report" in payload:
        return render_manifest_text(payload)
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_text(payload)
    if "agent_find" in payload:
        return render_agent_find_text(payload)
    if "project_lineage" in payload:
        return render_project_lineage_text(payload)
    if "skill_audit" in payload:
        return render_skill_audit_text(payload)
    if "structured_tool_events" in payload:
        return render_structured_tool_events_text(payload)
    if "workflow_repeat_finder" in payload:
        return render_workflow_repeat_text(payload)
    plan = payload["source_plan"]
    query = plan["query"]
    lines = [
        f"Query: {query['original']}",
        f"Terms: {', '.join(query['terms']) if query['terms'] else '(none)'}",
        f"Role: {query['role'] or '(any)'}",
    ]
    window = query["date_window"]
    if window["start"] or window["end"]:
        lines.append(f"Date window: {window['start']} to {window['end']} ({window['reason']})")
    lines.append(f"Roots: {len(plan['sources']['roots'])} selected, {len(plan['sources']['rejected_roots'])} rejected")
    for root in plan["sources"]["roots"]:
        lines.append(f"- {root['kind']}: {root['path']} ({root['reason']})")
    for rejected in plan["sources"]["rejected_roots"]:
        lines.append(f"- rejected {rejected['path']}: {rejected['reason']}")
    lines.append(
        f"Searched: {payload.get('searched_files', 0)} files, {payload.get('searched_bytes', 0)} bytes"
    )
    for warning in payload.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    masking = payload.get("masking")
    if masking:
        redaction = masking.get("redaction") or {}
        lines.append(
            "Masking: "
            f"{masking.get('mode')} "
            f"masked={masking.get('active_context_masked', 0)} "
            f"collapsed={masking.get('active_context_collapsed', 0)} "
            f"debug_visible={masking.get('debug_visible', 0)} "
            f"transcript_retained={masking.get('transcript_retained', 0)} "
            f"redacted={redaction.get('redacted_total', 0)}"
        )
    if payload.get("groups"):
        lines.append("Matches:")
        for group in payload["groups"]:
            lines.append(f"- {group['path']} (score {group['top_score']})")
            for match in group["matches"][:3]:
                where = f"line {match['line']}"
                stamp = f", {match['timestamp']}" if match["timestamp"] else ""
                role = f", {match['role']}" if match["role"] else ""
                lines.append(f"  {where}{stamp}{role}: {match['snippet']}")
    return "\n".join(lines)


def render_markdown(payload: dict) -> str:
    if "install_validation" in payload:
        return render_install_validation_markdown(payload)
    if "forensics" in payload:
        return render_forensics_markdown(payload)
    if "manifest_report" in payload:
        return render_manifest_markdown(payload)
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_markdown(payload)
    if "agent_find" in payload:
        return render_agent_find_markdown(payload)
    if "project_lineage" in payload:
        return render_project_lineage_markdown(payload)
    if "skill_audit" in payload:
        return render_skill_audit_markdown(payload)
    if "structured_tool_events" in payload:
        return render_structured_tool_events_markdown(payload)
    if "workflow_repeat_finder" in payload:
        return render_workflow_repeat_markdown(payload)
    text = render_text(payload)
    lines = ["# JSONL History Search", ""]
    for line in text.splitlines():
        if line.startswith("- "):
            lines.append(line)
        elif line.startswith("  "):
            lines.append(line)
        else:
            key, sep, value = line.partition(": ")
            if sep and key in {"Query", "Terms", "Role", "Date window", "Roots", "Searched"}:
                lines.append(f"**{key}:** {value}")
            else:
                lines.append(line)
    return "\n".join(lines)


def render(payload: dict, output_format: str) -> str:
    if output_format == "json":
        return render_json(payload)
    if output_format == "markdown":
        return render_markdown(payload)
    return render_text(payload)


def render_forensics_text(payload: dict) -> str:
    forensic = payload["forensics"]
    git = payload.get("git", {})
    jsonl = payload.get("jsonl_evidence", {})
    lines = [
        "Forensics",
        f"Query: {forensic.get('query') or '(none)'}",
        f"Confidence: {forensic.get('confidence')} ({', '.join(forensic.get('confidence_reasons', []))})",
    ]
    hints = forensic.get("hints", {})
    hint_text = ", ".join(f"{key}={value}" for key, value in hints.items() if value)
    lines.append(f"Hints: {hint_text or '(none)'}")
    if forensic.get("around_window_minutes") is not None:
        lines.append(f"Around window: +/- {forensic['around_window_minutes']} minutes")

    lines.append(f"Git repo: {git.get('repo') or '(none)'}")
    if git.get("current_branch"):
        lines.append(f"Git current branch: {git['current_branch']}")
    commit = git.get("commit")
    if commit:
        lines.append(f"Commit: {commit['commit']} {commit['commit_time']} {commit['subject']}")
        for changed in commit.get("changed_files", []):
            lines.append(f"- changed {changed.get('status')}: {changed.get('path')}")
    if git.get("path_last_change"):
        change = git["path_last_change"]
        lines.append(f"Path last change: {change['commit']} {change['commit_time']} {change['path']} {change['subject']}")
    if git.get("branch"):
        branch = git["branch"]
        lines.append(f"Branch: {branch['name']} -> {branch['head']}")

    lines.append(
        f"JSONL searched: {jsonl.get('searched_files', 0)} files, {jsonl.get('searched_bytes', 0)} bytes"
    )
    for group in jsonl.get("groups", []):
        lines.append(f"- JSONL {group['cluster_key']} score {group['top_score']}")
        for match in group.get("matches", [])[:3]:
            action = (match.get("claim_action") or {}).get("action", "unknown")
            stamp = f", {match['timestamp']}" if match.get("timestamp") else ""
            role = f", {match['role']}" if match.get("role") else ""
            lines.append(f"  line {match['line']}{stamp}{role}, action={action}: {match['snippet']}")

    if payload.get("artifact_checks"):
        lines.append("Artifact checks:")
        for item in payload["artifact_checks"]:
            state = "exists" if item.get("exists") else "missing"
            reason = f", {item['reason']}" if item.get("reason") else ""
            lines.append(f"- {item['path']}: {state}{reason}")

    if payload.get("review_evidence"):
        lines.append("Review evidence:")
        for item in payload["review_evidence"]:
            verdict = item.get("review_verdict") or {}
            action = (item.get("claim_action") or {}).get("action", "unknown")
            label = verdict.get("action") or action
            lines.append(f"- {item['path']}: {label}")

    for warning in payload.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_forensics_markdown(payload: dict) -> str:
    text = render_forensics_text(payload)
    lines = ["# JSONL History Forensics", ""]
    for line in text.splitlines()[1:]:
        if line.startswith("- ") or line.startswith("  "):
            lines.append(line)
            continue
        key, sep, value = line.partition(": ")
        if sep:
            lines.append(f"**{key}:** {value}")
        else:
            lines.append(line)
    return "\n".join(lines)


def render_project_lineage_text(payload: dict) -> str:
    lineage = payload["project_lineage"]
    subject = lineage.get("subject_project", {})
    lines = [
        "Project Lineage",
        f"Status: {lineage.get('status')}",
        f"Subject: {subject.get('name')} ({subject.get('path')})",
        f"Query: {lineage.get('query') or '(none)'}",
        f"Candidates: {len(lineage.get('candidate_projects', []))}",
    ]
    if lineage.get("report_written"):
        lines.append(f"Report written: {lineage['report_written']}")
    for candidate in lineage.get("candidate_projects", [])[:8]:
        lines.append(
            "- "
            f"{candidate.get('candidate_id') or '?'} {candidate.get('name')} "
            f"score={candidate.get('score')} "
            f"confidence={candidate.get('confidence')} "
            f"relationship={candidate.get('relationship_hypothesis')}"
        )
        reason_codes = ", ".join(candidate.get("reason_codes", []))
        if reason_codes:
            lines.append(f"  Reasons: {reason_codes}")
        if candidate.get("jsonl_evidence_refs"):
            ref = candidate["jsonl_evidence_refs"][0]
            lines.append(f"  First JSONL ref: {ref.get('path')}:{ref.get('line')} {ref.get('timestamp')}")
    if lineage.get("timeline"):
        lines.append("Timeline:")
        for item in lineage["timeline"][:10]:
            lines.append(
                f"- {item.get('timestamp_utc') or item.get('timestamp') or '(no timestamp)'} "
                f"{item.get('path')}:{item.get('line')} {item.get('snippet_or_summary')}"
            )
    for warning in payload.get("warnings", []) + lineage.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    for item in lineage.get("next_read", [])[:5]:
        lines.append(f"Next read: {item}")
    return "\n".join(lines)


def render_project_lineage_markdown(payload: dict) -> str:
    text = render_project_lineage_text(payload)
    lines = ["# Project Lineage", ""]
    for line in text.splitlines()[1:]:
        if line.startswith("- ") or line.startswith("  "):
            lines.append(line)
            continue
        key, sep, value = line.partition(": ")
        if sep:
            lines.append(f"**{key}:** {value}")
        else:
            lines.append(line)
    return "\n".join(lines)


def render_skill_audit_text(payload: dict) -> str:
    report = payload["skill_audit"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Skill Audit",
        f"Status: {report.get('status')}",
        f"Skill: {report.get('skill')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('usage_instance_count', 0)} actual-use instances, "
            f"{efficiency.get('non_use_mention_count', 0)} non-use mentions"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for instance in report.get("usage_instances", [])[:8]:
        evidence = instance.get("skill_evidence", [{}])[0]
        ref = evidence.get("source_ref", {})
        lines.append(
            "- "
            f"{instance.get('usage_id')} {instance.get('confidence')} "
            f"{','.join(instance.get('signal_types', []))} "
            f"{ref.get('path')}:{ref.get('line')} "
            f"labels={','.join(instance.get('outcome_labels', []))}"
        )
    for mention in report.get("non_use_mentions", [])[:5]:
        ref = mention.get("source_ref", {})
        lines.append(f"Non-use: {ref.get('path')}:{ref.get('line')} {mention.get('snippet')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_skill_audit_markdown(payload: dict) -> str:
    report = payload["skill_audit"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Skill Audit",
        "",
        f"**Status:** {report.get('status')}",
        f"**Skill:** {report.get('skill')}",
        (
            f"**Scope:** {efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Evidence:** {efficiency.get('usage_instance_count', 0)} actual-use instances, "
            f"{efficiency.get('non_use_mention_count', 0)} non-use mentions"
        ),
        "",
        "## Usage Instances",
        "",
        "| ID | Confidence | Signals | Source | Labels |",
        "| --- | --- | --- | --- | --- |",
    ]
    for instance in report.get("usage_instances", []):
        evidence = instance.get("skill_evidence", [{}])[0]
        ref = evidence.get("source_ref", {})
        source = f"{ref.get('path')}:{ref.get('line')}"
        lines.append(
            "| "
            f"{_escape_markdown_table(str(instance.get('usage_id', '')))} | "
            f"{_escape_markdown_table(str(instance.get('confidence', '')))} | "
            f"{_escape_markdown_table(', '.join(instance.get('signal_types', [])))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(', '.join(instance.get('outcome_labels', [])))} |"
        )
    if report.get("non_use_mentions"):
        lines.extend(["", "## Non-Use Mentions", ""])
        for mention in report["non_use_mentions"][:12]:
            ref = mention.get("source_ref", {})
            lines.append(f"- `{ref.get('path')}:{ref.get('line')}` {mention.get('snippet')}")
    return "\n".join(lines)


def render_workflow_repeat_text(payload: dict) -> str:
    report = payload["workflow_repeat_finder"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Workflow Repeat Finder",
        f"Status: {report.get('status')}",
        f"Query: {report.get('query') or '(none)'}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('cluster_count', 0)} clusters, "
            f"{efficiency.get('suggestion_count', 0)} suggestions, "
            f"{efficiency.get('rejected_one_off_count', 0)} one-offs rejected"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for cluster in report.get("workflow_clusters", [])[:8]:
        first = (cluster.get("representative_instances") or [{}])[0].get("source_ref", {})
        lines.append(
            "- "
            f"{cluster.get('cluster_id')} rank={cluster.get('rank')} "
            f"score={cluster.get('score')} confidence={cluster.get('confidence')} "
            f"instances={cluster.get('instance_count')} kind={cluster.get('workflow_kind')} "
            f"{first.get('path')}:{first.get('line')} {cluster.get('label')}"
        )
        reasons = ", ".join(cluster.get("reason_codes", [])[:5])
        if reasons:
            lines.append(f"  Reasons: {reasons}")
    for suggestion in report.get("automation_suggestions", [])[:5]:
        ref = suggestion.get("source_ref", {})
        lines.append(
            f"Suggestion: {suggestion.get('classification')} "
            f"{ref.get('path')}:{ref.get('line')} {suggestion.get('snippet')}"
        )
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_workflow_repeat_markdown(payload: dict) -> str:
    report = payload["workflow_repeat_finder"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Workflow Repeat Finder",
        "",
        f"**Status:** {report.get('status')}",
        f"**Query:** {report.get('query') or '(none)'}",
        (
            f"**Scope:** {efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Evidence:** {efficiency.get('cluster_count', 0)} clusters, "
            f"{efficiency.get('suggestion_count', 0)} suggestions, "
            f"{efficiency.get('rejected_one_off_count', 0)} one-offs rejected"
        ),
        "",
        "## Workflow Clusters",
        "",
        "| Rank | ID | Score | Confidence | Kind | Instances | First Source | Label |",
        "| ---: | --- | ---: | --- | --- | ---: | --- | --- |",
    ]
    for cluster in report.get("workflow_clusters", []):
        first = (cluster.get("representative_instances") or [{}])[0].get("source_ref", {})
        source = f"{first.get('path')}:{first.get('line')}"
        lines.append(
            "| "
            f"{cluster.get('rank')} | "
            f"{_escape_markdown_table(str(cluster.get('cluster_id', '')))} | "
            f"{cluster.get('score')} | "
            f"{_escape_markdown_table(str(cluster.get('confidence', '')))} | "
            f"{_escape_markdown_table(str(cluster.get('workflow_kind', '')))} | "
            f"{cluster.get('instance_count')} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(cluster.get('label', '')))} |"
        )
    if report.get("automation_suggestions"):
        lines.extend(["", "## Automation Suggestions", ""])
        for suggestion in report["automation_suggestions"][:12]:
            ref = suggestion.get("source_ref", {})
            lines.append(
                f"- `{suggestion.get('classification')}` `{ref.get('path')}:{ref.get('line')}` "
                f"{suggestion.get('snippet')}"
            )
    if report.get("rejected_one_offs"):
        lines.extend(["", "## Rejected One-Offs", ""])
        for one_off in report["rejected_one_offs"][:8]:
            ref = one_off.get("first_source_ref") or {}
            lines.append(f"- `{one_off.get('cluster_key')}` `{ref.get('path')}:{ref.get('line')}` {one_off.get('reason')}")
    return "\n".join(lines)


def render_install_validation_text(payload: dict) -> str:
    validation = payload["install_validation"]
    summary = validation.get("summary", {})
    lines = [
        f"Install validation: {str(validation.get('overall_status', 'unknown')).upper()}",
        (
            "Checks: "
            f"{summary.get('pass', 0)} pass, "
            f"{summary.get('warn', 0)} warn, "
            f"{summary.get('fail', 0)} fail"
        ),
        f"Package root: {validation.get('package_root')}",
        f"Read-only: {validation.get('read_only')}",
    ]
    if validation.get("codex_home"):
        lines.append(f"Codex home: {validation['codex_home']}")
    lines.append("Details:")
    for check in validation.get("checks", []):
        lines.append(f"- {str(check.get('status', '')).upper()} {check.get('name')}: {check.get('detail')}")
        for recovery in check.get("recovery", []):
            lines.append(f"  Recovery: {recovery}")
    return "\n".join(lines)


def render_install_validation_markdown(payload: dict) -> str:
    validation = payload["install_validation"]
    summary = validation.get("summary", {})
    lines = [
        "# JSONL History Search Install Validation",
        "",
        f"**Status:** {str(validation.get('overall_status', 'unknown')).upper()}",
        (
            f"**Checks:** {summary.get('pass', 0)} pass, "
            f"{summary.get('warn', 0)} warn, {summary.get('fail', 0)} fail"
        ),
        f"**Package root:** `{validation.get('package_root')}`",
        f"**Read-only:** {validation.get('read_only')}",
        "",
        "| Status | Check | Detail |",
        "| --- | --- | --- |",
    ]
    for check in validation.get("checks", []):
        lines.append(
            "| "
            f"{str(check.get('status', '')).upper()} | "
            f"{_escape_markdown_table(str(check.get('name', '')))} | "
            f"{_escape_markdown_table(str(check.get('detail', '')))} |"
        )
    suggestions = validation.get("recovery_suggestions") or []
    if suggestions:
        lines.extend(["", "## Recovery Suggestions", ""])
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def _escape_markdown_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_agent_find_text(payload: dict) -> str:
    report = payload["agent_find"]
    lines = [
        "JSONL History Find",
        f"Status: {report.get('status')}",
        f"Query: {report.get('query', {}).get('original', '')}",
        (
            "Scope: "
            f"{report.get('plan_sources', {}).get('searched_files', 0)} files, "
            f"{report.get('plan_sources', {}).get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Candidates: "
            f"{report.get('token_efficiency', {}).get('candidate_count', 0)} "
            f"({report.get('context_budget', {}).get('payload_bytes', 0)} bytes inline)"
        ),
    ]
    for candidate in report.get("find_candidates", [])[:8]:
        ref = candidate.get("source_ref", {})
        lines.append(
            "- "
            f"{candidate.get('candidate_id')} {candidate.get('confidence')} "
            f"{ref.get('path')}:{ref.get('line')} "
            f"{candidate.get('timestamp') or '(no timestamp)'} "
            f"{candidate.get('role') or ''}: {candidate.get('snippet')}"
        )
    if report.get("status") == "no_evidence":
        no_evidence = report.get("no_evidence_report", {})
        lines.append(f"No evidence: {no_evidence.get('why_not_confident')}")
    suggestions = report.get("next_search_suggestions") or []
    if suggestions:
        lines.append("Next searches:")
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def render_agent_find_markdown(payload: dict) -> str:
    report = payload["agent_find"]
    lines = [
        "# JSONL History Find",
        "",
        f"**Status:** {report.get('status')}",
        f"**Query:** {report.get('query', {}).get('original', '')}",
        (
            f"**Scope:** {report.get('plan_sources', {}).get('searched_files', 0)} files, "
            f"{report.get('plan_sources', {}).get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Context budget:** {report.get('context_budget', {}).get('payload_bytes', 0)} bytes, "
            f"~{report.get('context_budget', {}).get('estimated_tokens', 0)} tokens"
        ),
        "",
        "## Candidates",
        "",
        "| ID | Confidence | Source | Timestamp | Role | Stage | Snippet |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in report.get("find_candidates", []):
        ref = candidate.get("source_ref", {})
        source = f"{ref.get('path')}:{ref.get('line')}"
        lines.append(
            "| "
            f"{_escape_markdown_table(str(candidate.get('candidate_id', '')))} | "
            f"{_escape_markdown_table(str(candidate.get('confidence', '')))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(candidate.get('timestamp') or ''))} | "
            f"{_escape_markdown_table(str(candidate.get('role') or ''))} | "
            f"{_escape_markdown_table(str(candidate.get('stage') or ''))} | "
            f"{_escape_markdown_table(str(candidate.get('snippet') or ''))} |"
        )
    if report.get("status") == "no_evidence":
        no_evidence = report.get("no_evidence_report", {})
        lines.extend(["", "## No Evidence", "", no_evidence.get("why_not_confident", "")])
    suggestions = report.get("next_search_suggestions") or []
    if suggestions:
        lines.extend(["", "## Next Searches", ""])
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def render_manifest_text(payload: dict) -> str:
    report = payload["manifest_report"]
    manifest = report["manifest"]
    shortlist = report["shortlist"]
    cost = report["cost_model"]
    metrics = shortlist["metrics"]
    lines = [
        "JSONL History Manifest",
        f"Entries: {manifest['entry_count']}",
        f"Build: {cost['build_time_ms']} ms, {manifest['build_metrics'].get('bytes_parsed', 0)} bytes parsed",
        f"Refresh: {cost['refresh_time_ms']} ms, {manifest['no_change_refresh_metrics'].get('files_reused', 0)} files reused",
        f"Estimated manifest size: {cost['manifest_disk_bytes']} bytes",
        (
            "Shortlist: "
            f"{metrics['selected_files']}/{metrics['total_files']} files, "
            f"{metrics['selected_bytes']}/{metrics['total_bytes']} bytes, "
            f"file reduction={metrics['file_reduction_ratio']}, "
            f"byte reduction={metrics['byte_reduction_ratio']}"
        ),
    ]
    for entry in shortlist.get("selected_entries", [])[:10]:
        path = entry["entry"]["path"]
        reasons = ", ".join(entry.get("reasons") or ["weak-candidate"])
        lines.append(f"- {path} score={entry['score']} reasons={reasons}")
    for warning in manifest.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_manifest_markdown(payload: dict) -> str:
    report = payload["manifest_report"]
    manifest = report["manifest"]
    shortlist = report["shortlist"]
    cost = report["cost_model"]
    metrics = shortlist["metrics"]
    lines = [
        "# JSONL History Manifest",
        "",
        f"**Entries:** {manifest['entry_count']}",
        f"**Build:** {cost['build_time_ms']} ms, {manifest['build_metrics'].get('bytes_parsed', 0)} bytes parsed",
        f"**Refresh:** {cost['refresh_time_ms']} ms, {manifest['no_change_refresh_metrics'].get('files_reused', 0)} files reused",
        f"**Estimated manifest size:** {cost['manifest_disk_bytes']} bytes",
        (
            f"**Shortlist:** {metrics['selected_files']}/{metrics['total_files']} files, "
            f"{metrics['selected_bytes']}/{metrics['total_bytes']} bytes, "
            f"file reduction {metrics['file_reduction_ratio']}, "
            f"byte reduction {metrics['byte_reduction_ratio']}"
        ),
        "",
        "## Cache Policy",
        "",
    ]
    policy = report.get("cache_policy", {})
    for key in ("local_only", "optional", "default_search_uses_cache", "invalidation", "privacy"):
        lines.append(f"- **{key}:** {policy.get(key)}")

    lines.extend(
        [
            "",
            "## Selected Entries",
            "",
            "| Score | Path | Date span | Roles | Reasons |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for item in shortlist.get("selected_entries", [])[:25]:
        entry = item["entry"]
        date_span = " to ".join(part for part in [entry.get("date_start"), entry.get("date_end")] if part) or "(unknown)"
        lines.append(
            "| "
            f"{item.get('score')} | "
            f"{_escape_markdown_table(entry.get('path', ''))} | "
            f"{_escape_markdown_table(date_span)} | "
            f"{_escape_markdown_table(', '.join(entry.get('roles') or []) or '(none)')} | "
            f"{_escape_markdown_table(', '.join(item.get('reasons') or []) or 'weak-candidate')} |"
        )

    guidance = report.get("guidance", {})
    lines.extend(["", "## Guidance", ""])
    for item in guidance.get("ultra_valuable_when", []):
        lines.append(f"- Ultra valuable: {item}")
    for item in guidance.get("unnecessary_when", []):
        lines.append(f"- Usually unnecessary: {item}")
    if guidance.get("shortlist_note"):
        lines.append(f"- Note: {guidance['shortlist_note']}")
    return "\n".join(lines)


def render_efficiency_eval_text(payload: dict) -> str:
    report = payload["retrieval_efficiency_eval"]
    if report.get("status") == "invalid_fixture":
        lines = [
            "Retrieval efficiency benchmark: INVALID FIXTURE",
            f"Fixture: {report.get('fixture')}",
        ]
        lines.extend(f"ERROR: {error}" for error in report.get("fixture_errors", []))
        return "\n".join(lines)

    thresholds = report.get("large_impact_thresholds", {})
    lines = [
        "Retrieval efficiency benchmark",
        f"Fixture: {report.get('fixture')}",
        f"Data source: {report.get('data_source')}",
        f"Threshold status: {str(thresholds.get('status', 'unknown')).upper()}",
        f"Note: {report.get('measurement_note')}",
        "Summary:",
    ]
    for name, summary in report.get("summary", {}).items():
        lines.append(
            "- "
            f"{name}: hit_rate={summary.get('first_pass_hit_rate')} "
            f"context_bytes={summary.get('median_prompt_context_bytes')} "
            f"files={summary.get('median_files_opened')} "
            f"tool_calls={summary.get('median_tool_calls')} "
            f"recall@10={summary.get('mean_recall_at_10')}"
        )
    if thresholds.get("items"):
        lines.append("Thresholds:")
        for item in thresholds["items"]:
            status = "PASS" if item.get("passed") else "FAIL"
            lines.append(
                f"- {status} {item.get('metric')} {item.get('operator')} "
                f"{item.get('target')} (actual {item.get('actual')})"
            )
    followup = report.get("coverage_needs_followup") or {}
    stages = followup.get("candidate_generation_stages") or []
    modes = followup.get("failure_modes") or []
    if stages or modes:
        lines.append(f"Follow-up stages: {', '.join(stages) if stages else '(none)'}")
        lines.append(f"Follow-up failure modes: {', '.join(modes) if modes else '(none)'}")
    return "\n".join(lines)


def render_efficiency_eval_markdown(payload: dict) -> str:
    report = payload["retrieval_efficiency_eval"]
    if report.get("status") == "invalid_fixture":
        lines = [
            "# Retrieval Efficiency Benchmark",
            "",
            "**Status:** INVALID FIXTURE",
            f"**Fixture:** `{report.get('fixture')}`",
            "",
            "## Fixture Errors",
            "",
        ]
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
        return "\n".join(lines)

    thresholds = report.get("large_impact_thresholds", {})
    lines = [
        "# Retrieval Efficiency Benchmark",
        "",
        f"**Status:** {str(report.get('status', 'unknown')).upper()}",
        f"**Fixture:** `{report.get('fixture')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Threshold status:** {str(thresholds.get('status', 'unknown')).upper()}",
        "",
        report.get("measurement_note", ""),
        "",
        "## Strategy Summary",
        "",
        "| Strategy | First-pass hit rate | Median context bytes | Median tokens | Median files | Median tool calls | Mean recall@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, summary in report.get("summary", {}).items():
        lines.append(
            "| "
            f"{_escape_markdown_table(name)} | "
            f"{summary.get('first_pass_hit_rate')} | "
            f"{summary.get('median_prompt_context_bytes')} | "
            f"{summary.get('median_estimated_tokens_returned')} | "
            f"{summary.get('median_files_opened')} | "
            f"{summary.get('median_tool_calls')} | "
            f"{summary.get('mean_recall_at_10')} |"
        )

    lines.extend(
        [
            "",
            "## Large Impact Thresholds",
            "",
            "| Status | Metric | Target | Actual |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for item in thresholds.get("items", []):
        status = "PASS" if item.get("passed") else "FAIL"
        target = f"{item.get('operator')} {item.get('target')}"
        lines.append(
            "| "
            f"{status} | "
            f"{_escape_markdown_table(str(item.get('metric')))} | "
            f"{_escape_markdown_table(target)} | "
            f"{item.get('actual')} |"
        )

    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case | Category | Strategy | Fit | Basis | Context bytes | Files | Tool calls | Missing stages | Unguarded failures |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for case in report.get("cases", []):
        for result in case.get("strategy_results", []):
            lines.append(
                "| "
                f"{_escape_markdown_table(case.get('case_id', ''))} | "
                f"{_escape_markdown_table(case.get('category', ''))} | "
                f"{_escape_markdown_table(result.get('strategy', ''))} | "
                f"{_escape_markdown_table(result.get('fit', ''))} | "
                f"{_escape_markdown_table(result.get('metric_basis', ''))} | "
                f"{result.get('prompt_context_bytes')} | "
                f"{result.get('files_opened')} | "
                f"{result.get('tool_calls')} | "
                f"{_escape_markdown_table(', '.join(result.get('missing_stages') or []) or '(none)')} | "
                f"{_escape_markdown_table(', '.join(result.get('unguarded_failure_modes') or []) or '(none)')} |"
            )

    followup = report.get("coverage_needs_followup") or {}
    stages = followup.get("candidate_generation_stages") or []
    modes = followup.get("failure_modes") or []
    if stages or modes:
        lines.extend(["", "## Follow-Up Coverage", ""])
        if stages:
            lines.append(f"- Candidate generation stages: {', '.join(stages)}")
        if modes:
            lines.append(f"- Failure modes: {', '.join(modes)}")
        if followup.get("notes"):
            lines.append(f"- Notes: {followup['notes']}")
    return "\n".join(lines)


def render_structured_tool_events_text(payload: dict) -> str:
    report = payload["structured_tool_events"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Structured Tool Events",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('event_count', 0)} events, "
            f"{efficiency.get('excluded_mention_count', 0)} excluded mentions"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for event in report.get("events", [])[:12]:
        ref = event.get("source_ref") or {}
        target_values = [target.get("value") for target in event.get("targets", [])[:3]]
        suffix = f" targets={', '.join(target_values)}" if target_values else ""
        command = f" command={event.get('command')}" if event.get("command") else ""
        lines.append(
            "- "
            f"{event.get('event_id')} {event.get('event_type')} "
            f"{event.get('confidence')} "
            f"{ref.get('path')}:{ref.get('line')}"
            f"{command}{suffix}"
        )
    for item in report.get("excluded_mentions", [])[:5]:
        ref = item.get("source_ref") or {}
        lines.append(f"- excluded {ref.get('path')}:{ref.get('line')} {item.get('reason')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_structured_tool_events_markdown(payload: dict) -> str:
    report = payload["structured_tool_events"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Structured Tool Events",
        "",
        f"**Status:** {report.get('status')}",
        (
            "**Scope:** "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "**Evidence:** "
            f"{efficiency.get('event_count', 0)} events, "
            f"{efficiency.get('excluded_mention_count', 0)} excluded mentions"
        ),
        "",
        "## Events",
        "",
        "| ID | Type | Confidence | Source | Tool | Command/Targets |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for event in report.get("events", [])[:24]:
        ref = event.get("source_ref") or {}
        source = f"{ref.get('path')}:{ref.get('line')}"
        targets = ", ".join(target.get("value", "") for target in event.get("targets", [])[:3])
        command = event.get("command") or targets
        lines.append(
            "| "
            f"{_escape_markdown_table(str(event.get('event_id')))} | "
            f"{_escape_markdown_table(str(event.get('event_type')))} | "
            f"{_escape_markdown_table(str(event.get('confidence')))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(event.get('tool_name') or ''))} | "
            f"{_escape_markdown_table(str(command or ''))} |"
        )
    excluded = report.get("excluded_mentions", [])
    if excluded:
        lines.extend(["", "## Excluded Mentions", ""])
        for item in excluded[:8]:
            ref = item.get("source_ref") or {}
            lines.append(f"- {ref.get('path')}:{ref.get('line')} {item.get('reason')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)
