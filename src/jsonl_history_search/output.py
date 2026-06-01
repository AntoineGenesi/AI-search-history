from __future__ import annotations

import json


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def render_text(payload: dict) -> str:
    if "jsonl_candidate_discovery" in payload:
        return render_jsonl_candidate_discovery_text(payload)
    if "capability_diagnostic" in payload:
        return render_capability_diagnostic_text(payload)
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
    if "private_benchmark_mine" in payload:
        return render_private_benchmark_mine_text(payload)
    if "measured_benchmark" in payload:
        return render_measured_benchmark_text(payload)
    if "impact_benchmark" in payload:
        return render_impact_benchmark_text(payload)
    if "embedding_experiment" in payload:
        return render_embedding_experiment_text(payload)
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
    if "jsonl_candidate_discovery" in payload:
        return render_jsonl_candidate_discovery_markdown(payload)
    if "capability_diagnostic" in payload:
        return render_capability_diagnostic_markdown(payload)
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
    if "private_benchmark_mine" in payload:
        return render_private_benchmark_mine_markdown(payload)
    if "measured_benchmark" in payload:
        return render_measured_benchmark_markdown(payload)
    if "impact_benchmark" in payload:
        return render_impact_benchmark_markdown(payload)
    if "embedding_experiment" in payload:
        return render_embedding_experiment_markdown(payload)
    text = render_text(payload)
    lines = ["# JSONL", ""]
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


def render_jsonl_candidate_discovery_text(payload: dict) -> str:
    report = payload["jsonl_candidate_discovery"]
    summary = report.get("summary", {})
    cap_hits = summary.get("cap_hits", {})
    lines = [
        "JSONL Candidate Discovery",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{summary.get('selected_root_count', 0)} selected roots, "
            f"{summary.get('rejected_root_count', 0)} rejected roots"
        ),
        (
            "Candidates: "
            f"{summary.get('candidate_count', 0)} files, "
            f"{summary.get('candidate_bytes', 0)} bytes"
        ),
        (
            "Cap hits: "
            f"file={cap_hits.get('file', False)} "
            f"byte={cap_hits.get('byte', False)} "
            f"elapsed={cap_hits.get('elapsed', False)}"
        ),
    ]
    for root in report.get("scope", {}).get("selected_roots", []):
        lines.append(
            "- selected "
            f"{root.get('root_label')} {root.get('kind')} "
            f"known={root.get('known')} recursive={root.get('recursive')} "
            f"hash={root.get('root_hash')}"
        )
    for rejected in report.get("scope", {}).get("rejected_roots", []):
        lines.append(f"- rejected {rejected.get('root_label')}: {rejected.get('reason')}")
    for candidate in report.get("candidates", [])[:12]:
        lines.append(
            "- "
            f"{candidate.get('candidate_id')} {candidate.get('root_label')} "
            f"{candidate.get('source_kind')} {candidate.get('suffix')} "
            f"size={candidate.get('file_size_bytes')} "
            f"mtime={candidate.get('mtime_utc')} "
            f"{candidate.get('path_label')}"
        )
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for warning in report.get("privacy_warnings", []):
        lines.append(f"Privacy: {warning}")
    for item in report.get("scope", {}).get("not_searched_scope", []):
        lines.append(f"Not searched: {item}")
    return "\n".join(lines)


def render_jsonl_candidate_discovery_markdown(payload: dict) -> str:
    report = payload["jsonl_candidate_discovery"]
    summary = report.get("summary", {})
    cap_hits = summary.get("cap_hits", {})
    lines = [
        "# JSONL Candidate Discovery",
        "",
        f"**Status:** `{report.get('status')}`",
        (
            "**Scope:** "
            f"{summary.get('selected_root_count', 0)} selected roots, "
            f"{summary.get('rejected_root_count', 0)} rejected roots"
        ),
        (
            "**Candidates:** "
            f"{summary.get('candidate_count', 0)} files, "
            f"{summary.get('candidate_bytes', 0)} bytes"
        ),
        (
            "**Cap hits:** "
            f"file=`{cap_hits.get('file', False)}`, "
            f"byte=`{cap_hits.get('byte', False)}`, "
            f"elapsed=`{cap_hits.get('elapsed', False)}`"
        ),
        "",
        "## Roots",
        "",
    ]
    selected_roots = report.get("scope", {}).get("selected_roots", [])
    rejected_roots = report.get("scope", {}).get("rejected_roots", [])
    if selected_roots:
        lines.extend(["| Label | Kind | Known | Recursive | Hash |", "| --- | --- | --- | --- | --- |"])
        for root in selected_roots:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(root.get('root_label')))} | "
                f"{_escape_markdown_table(str(root.get('kind')))} | "
                f"{root.get('known')} | "
                f"{root.get('recursive')} | "
                f"`{_escape_markdown_table(str(root.get('root_hash')))}` |"
            )
    else:
        lines.append("- No selected roots.")
    if rejected_roots:
        lines.extend(["", "### Rejected Roots", ""])
        for rejected in rejected_roots:
            lines.append(f"- `{rejected.get('root_label')}`: {rejected.get('reason')}")
    lines.extend(["", "## Candidates", ""])
    candidates = report.get("candidates", [])
    if candidates:
        lines.extend(
            [
                "| ID | Root | Source | Type | Size | Mtime UTC | Path label |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for candidate in candidates[:50]:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(candidate.get('candidate_id')))} | "
                f"{_escape_markdown_table(str(candidate.get('root_label')))} | "
                f"{_escape_markdown_table(str(candidate.get('source_kind')))} | "
                f"{_escape_markdown_table(str(candidate.get('file_type')))} | "
                f"{candidate.get('file_size_bytes')} | "
                f"{_escape_markdown_table(str(candidate.get('mtime_utc')))} | "
                f"`{_escape_markdown_table(str(candidate.get('path_label')))}` |"
            )
    else:
        lines.append("- No candidate files found in selected roots.")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.get("warnings", []))
    lines.extend(["", "## Privacy And Limits", ""])
    lines.extend(f"- {warning}" for warning in report.get("privacy_warnings", []))
    lines.extend(f"- {item}" for item in report.get("scope", {}).get("not_searched_scope", []))
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    return "\n".join(lines)


def render_capability_diagnostic_text(payload: dict) -> str:
    report = payload["capability_diagnostic"]
    summary = report.get("summary", {})
    lines = [
        "Capability Diagnostic",
        f"Backend: {report.get('backend_selected')} (tier {report.get('fallback_tier')})",
        "Summary: "
        f"available={summary.get('available', 0)} "
        f"degraded={summary.get('degraded', 0)} "
        f"missing={summary.get('missing', 0)} "
        f"blocked={summary.get('blocked', 0)} "
        f"not_implemented={summary.get('not_implemented', 0)}",
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    source_plan = report.get("source_plan", {})
    lines.append(
        "Source plan: "
        f"{source_plan.get('selected_root_count', 0)} selected, "
        f"{source_plan.get('rejected_root_count', 0)} rejected, "
        f"{source_plan.get('missing_root_count', 0)} missing"
    )
    for root in source_plan.get("roots", [])[:5]:
        state = "readable" if root.get("readable") else "missing/unreadable"
        lines.append(f"- root {root.get('kind')}: {root.get('label')} ({state})")
    for rejected in source_plan.get("rejected_roots", [])[:5]:
        lines.append(f"- rejected {rejected.get('label')}: {rejected.get('reason')}")
    archive = report.get("archive_protection", {})
    if archive:
        cap_status = archive.get("cap_status", {})
        lines.append("Archive protection:")
        lines.append(
            "- "
            f"status={archive.get('status')} "
            f"protection={archive.get('protection_status')} "
            f"configured={archive.get('archive_configured')}"
        )
        lines.append(
            "- "
            f"selected_labels={', '.join(archive.get('selected_source_labels', [])) or '(none)'} "
            f"estimated_files={archive.get('estimated_selected_file_count', 0)} "
            f"estimated_bytes={archive.get('estimated_selected_bytes', 0)}"
        )
        lines.append(
            "- "
            f"caps file={cap_status.get('file', False)} "
            f"byte={cap_status.get('byte', False)} "
            f"elapsed={cap_status.get('elapsed', False)}"
        )
        for warning in archive.get("mounted_drive_warnings", []):
            lines.append(f"  Mounted-drive warning: {warning}")
        for warning in archive.get("privacy_warnings", []):
            lines.append(f"  Privacy: {warning}")
        for note in archive.get("duplicate_risk_notes", []):
            lines.append(f"  Duplicate risk: {note}")

    capabilities = report.get("capabilities", {})
    for status in ("available", "degraded", "missing", "blocked", "not_implemented"):
        items = [item for item in capabilities.values() if item.get("status") == status]
        if not items:
            continue
        heading = "Missing/not implemented" if status == "not_implemented" else status.replace("_", " ").title()
        lines.append(f"{heading}:")
        for item in items:
            lines.append(
                "- "
                f"{item.get('id')}: {item.get('name')} | "
                f"fallback={item.get('fallback')} | "
                f"next={item.get('suggested_next_action')}"
            )
            loss = item.get("expected_slowdown_or_lost_feature")
            if loss and status != "available":
                lines.append(f"  Impact: {loss}")
    gates = report.get("benchmark_gates", {})
    if gates:
        lines.append(f"Benchmark gates: default_promotion_ready={gates.get('default_promotion_ready')}")
        lines.append(f"Gate reason: {gates.get('reason')}")
    archive_gate = report.get("archive_protection_gate", {})
    if archive_gate:
        lines.append(
            "Archive protection gate: "
            f"{archive_gate.get('label')} status={archive_gate.get('status')} "
            f"promotion_ready={archive_gate.get('promotion_ready')}"
        )
    return "\n".join(lines)


def render_capability_diagnostic_markdown(payload: dict) -> str:
    report = payload["capability_diagnostic"]
    summary = report.get("summary", {})
    lines = [
        "# Capability Diagnostic",
        "",
        f"**Backend:** {report.get('backend_selected')} (tier {report.get('fallback_tier')})",
        (
            "**Summary:** "
            f"available={summary.get('available', 0)}, "
            f"degraded={summary.get('degraded', 0)}, "
            f"missing={summary.get('missing', 0)}, "
            f"blocked={summary.get('blocked', 0)}, "
            f"not_implemented={summary.get('not_implemented', 0)}"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"**Report written:** {report['report_written']}")
    lines.extend(["", "## Capabilities", ""])
    for item in report.get("capabilities", {}).values():
        lines.append(
            f"- **{item.get('id')}**: `{item.get('status')}`; "
            f"fallback `{item.get('fallback')}`; {item.get('suggested_next_action')}"
        )
    lines.extend(["", "## Source Plan", ""])
    source_plan = report.get("source_plan", {})
    lines.append(
        f"- selected={source_plan.get('selected_root_count', 0)}, "
        f"rejected={source_plan.get('rejected_root_count', 0)}, "
        f"missing={source_plan.get('missing_root_count', 0)}"
    )
    for root in source_plan.get("roots", [])[:5]:
        lines.append(f"- {root.get('kind')}: `{root.get('label')}` readable={root.get('readable')}")
    for rejected in source_plan.get("rejected_roots", [])[:5]:
        lines.append(f"- rejected `{rejected.get('label')}`: {rejected.get('reason')}")
    archive = report.get("archive_protection", {})
    if archive:
        cap_status = archive.get("cap_status", {})
        lines.extend(
            [
                "",
                "## Archive Protection",
                "",
                f"- status=`{archive.get('status')}`; protection=`{archive.get('protection_status')}`; configured=`{archive.get('archive_configured')}`",
                f"- selected source labels: {', '.join(archive.get('selected_source_labels', [])) or '(none)'}",
                f"- estimated selected files={archive.get('estimated_selected_file_count', 0)}; estimated selected bytes={archive.get('estimated_selected_bytes', 0)}",
                f"- caps: file=`{cap_status.get('file', False)}`, byte=`{cap_status.get('byte', False)}`, elapsed=`{cap_status.get('elapsed', False)}`",
            ]
        )
        for warning in archive.get("mounted_drive_warnings", []):
            lines.append(f"- mounted-drive warning: {warning}")
        for warning in archive.get("privacy_warnings", []):
            lines.append(f"- privacy: {warning}")
        for note in archive.get("duplicate_risk_notes", []):
            lines.append(f"- duplicate risk: {note}")
    archive_gate = report.get("archive_protection_gate", {})
    if archive_gate:
        lines.extend(
            [
                "",
                "## Archive Protection Gate",
                "",
                f"- label=`{archive_gate.get('label')}`; status=`{archive_gate.get('status')}`; promotion_ready=`{archive_gate.get('promotion_ready')}`",
                f"- restore/export smoke: {archive_gate.get('restore_export_acceptance_test')}",
            ]
        )
    return "\n".join(lines)


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
    lines = ["# JSONL Forensics", ""]
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


def render_private_benchmark_mine_text(payload: dict) -> str:
    report = payload["private_benchmark_mine"]
    summary = report.get("candidate_summary", {})
    coverage = report.get("coverage_gap_report", {})
    suggestions = report.get("sanitized_fixture_suggestions", {})
    efficiency = report.get("token_efficiency", {})
    files = report.get("output_files", {})
    lines = [
        "Private Benchmark Mine",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        f"Candidates: {summary.get('candidate_count', 0)}",
        f"Suggestions: {suggestions.get('suggested_case_count', 0)}",
        f"Private triage CSV: {files.get('private_triage_csv')}",
        f"Coverage gap report: {files.get('coverage_gap_report')}",
        f"Private report: {files.get('private_report')}",
    ]
    if coverage.get("missing_stages"):
        lines.append(f"Missing stages: {', '.join(coverage['missing_stages'])}")
    if coverage.get("missing_failure_modes"):
        lines.append(f"Missing failure modes: {', '.join(coverage['missing_failure_modes'])}")
    checker = report.get("checker", {})
    if checker.get("command"):
        lines.append(f"Checker: {checker['command']}")
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_private_benchmark_mine_markdown(payload: dict) -> str:
    report = payload["private_benchmark_mine"]
    summary = report.get("candidate_summary", {})
    coverage = report.get("coverage_gap_report", {})
    files = report.get("output_files", {})
    lines = [
        "# Private Benchmark Mine",
        "",
        f"**Status:** {report.get('status')}",
        f"**Candidates:** {summary.get('candidate_count', 0)}",
        f"**Suggestions:** {report.get('sanitized_fixture_suggestions', {}).get('suggested_case_count', 0)}",
        f"**Private triage CSV:** `{files.get('private_triage_csv')}`",
        f"**Coverage gap report:** `{files.get('coverage_gap_report')}`",
        f"**Private report:** `{files.get('private_report')}`",
        "",
        "## Coverage Gaps",
        "",
    ]
    for label, values in (
        ("Missing categories", coverage.get("missing_categories", [])),
        ("Missing stages", coverage.get("missing_stages", [])),
        ("Missing failure modes", coverage.get("missing_failure_modes", [])),
        ("Weak categories", coverage.get("weak_categories", [])),
    ):
        lines.append(f"- **{label}:** {', '.join(values) if values else '(none)'}")
    checker = report.get("checker", {})
    if checker.get("command"):
        lines.extend(["", "## Checker", "", f"`{checker['command']}`"])
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
        "# JSONL Install Validation",
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
    plan_sources = report.get("plan_sources", {})
    source_scope = plan_sources.get("source_scope", {})
    cap_hit = plan_sources.get("cap_hit", {})
    lines = [
        "JSONL Find",
        f"Status: {report.get('status')}",
        f"Query: {report.get('query', {}).get('original', '')}",
        (
            "Scope: "
            f"{plan_sources.get('searched_files', 0)} files, "
            f"{plan_sources.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Candidates: "
            f"{report.get('token_efficiency', {}).get('candidate_count', 0)} "
            f"({report.get('context_budget', {}).get('payload_bytes', 0)} bytes inline)"
        ),
    ]
    if source_scope:
        lines.insert(
            4,
            "Source roots: "
            f"{len(source_scope.get('selected_roots', []))} selected/private, "
            f"{source_scope.get('excluded_scope', {}).get('rejected_root_count', 0)} excluded, "
            f"{source_scope.get('not_searched_scope', {}).get('missing_root_count', 0)} missing",
        )
    if cap_hit.get("hit"):
        lines.append(f"Cap hit: {', '.join(cap_hit.get('caps_hit', []))}")
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
        not_searched = no_evidence.get("not_searched_scope", {})
        searched = no_evidence.get("searched_scope", {})
        lines.append(
            "No-evidence scope: "
            f"not found in {searched.get('searched_files', 0)} searched files; "
            f"{not_searched.get('missing_root_count', 0)} missing selected roots; "
            f"{len(no_evidence.get('excluded_scope', []))} excluded roots"
        )
        lines.append(f"No evidence: {no_evidence.get('why_not_confident')}")
    suggestions = report.get("next_search_suggestions") or []
    if suggestions:
        lines.append("Next searches:")
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def render_agent_find_markdown(payload: dict) -> str:
    report = payload["agent_find"]
    plan_sources = report.get("plan_sources", {})
    source_scope = plan_sources.get("source_scope", {})
    cap_hit = plan_sources.get("cap_hit", {})
    lines = [
        "# JSONL Find",
        "",
        f"**Status:** {report.get('status')}",
        f"**Query:** {report.get('query', {}).get('original', '')}",
        (
            f"**Scope:** {plan_sources.get('searched_files', 0)} files, "
            f"{plan_sources.get('searched_bytes', 0)} bytes searched"
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
    if source_scope:
        lines.insert(
            5,
            f"**Source roots:** {len(source_scope.get('selected_roots', []))} selected/private, "
            f"{source_scope.get('excluded_scope', {}).get('rejected_root_count', 0)} excluded, "
            f"{source_scope.get('not_searched_scope', {}).get('missing_root_count', 0)} missing",
        )
    if cap_hit.get("hit"):
        lines.extend(["", f"**Cap hit:** {', '.join(cap_hit.get('caps_hit', []))}"])
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
        not_searched = no_evidence.get("not_searched_scope", {})
        searched = no_evidence.get("searched_scope", {})
        lines.extend(
            [
                "",
                "## No Evidence",
                "",
                (
                    f"Not found in {searched.get('searched_files', 0)} searched files. "
                    f"Missing selected roots: {not_searched.get('missing_root_count', 0)}. "
                    f"Excluded roots: {len(no_evidence.get('excluded_scope', []))}."
                ),
                "",
                no_evidence.get("why_not_confident", ""),
            ]
        )
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
        "JSONL Manifest",
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
    duplicate_risk = report.get("duplicate_risk", {})
    duplicate_summary = duplicate_risk.get("summary", {})
    lines.append(
        "Duplicate risk: "
        f"{duplicate_summary.get('group_count', 0)} groups "
        f"({duplicate_summary.get('exact_duplicate_groups', 0)} exact, "
        f"{duplicate_summary.get('likely_mirror_groups', 0)} likely mirror)"
    )
    for group in duplicate_risk.get("groups", [])[:5]:
        labels = ", ".join(group.get("path_labels", [])[:4])
        lines.append(
            "- "
            f"{group.get('risk_type')} {group.get('confidence')} "
            f"members={group.get('member_count')} paths={labels}"
        )
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
        "# JSONL Manifest",
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

    duplicate_risk = report.get("duplicate_risk", {})
    duplicate_summary = duplicate_risk.get("summary", {})
    lines.extend(
        [
            "",
            "## Duplicate Risk",
            "",
            (
                f"- **Groups:** {duplicate_summary.get('group_count', 0)} "
                f"({duplicate_summary.get('exact_duplicate_groups', 0)} exact, "
                f"{duplicate_summary.get('likely_mirror_groups', 0)} likely mirror)"
            ),
        ]
    )
    for group in duplicate_risk.get("groups", [])[:10]:
        labels = ", ".join(group.get("path_labels", [])[:6])
        lines.append(
            f"- **{group.get('risk_type')}** `{group.get('confidence')}`; "
            f"members {group.get('member_count')}; paths {labels}"
        )
    for limitation in duplicate_risk.get("limitations", []):
        lines.append(f"- Limitation: {limitation}")

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
    embedding_spike = report.get("embedding_spike")
    if embedding_spike:
        lines.extend(render_embedding_spike_text_lines(embedding_spike))
    return "\n".join(lines)


def render_measured_benchmark_text(payload: dict) -> str:
    report = payload["measured_benchmark"]
    aggregate = report.get("aggregate", {})
    lines = [
        "Measured local benchmark",
        f"Status: {report.get('status')}",
        f"Run: {report.get('run_id')}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        (
            "Totals: "
            f"elapsed_ms={aggregate.get('total_elapsed_ms', 0)} "
            f"searched_files={aggregate.get('searched_files', 0)} "
            f"searched_bytes={aggregate.get('searched_bytes', 0)} "
            f"candidates={aggregate.get('candidate_count', 0)} "
            f"output_context_bytes={aggregate.get('output_context_bytes', 0)}"
        ),
    ]
    statuses = aggregate.get("statuses", {})
    if statuses:
        lines.append("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    for gate_name in ("ranking_quality_gate", "rg_prefilter_gate", "manifest_shortlist_gate"):
        gate = report.get(gate_name) or {}
        if gate:
            lines.append(
                f"{gate_name}: status={gate.get('status')} "
                f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
            )
    prefilter = aggregate.get("rg_prefilter") or {}
    if prefilter.get("enabled_rows"):
        lines.append(
            "rg_prefilter: "
            f"files_reduced={prefilter.get('files_reduced', 0)} "
            f"bytes_reduced={prefilter.get('bytes_reduced', 0)} "
            f"statuses={prefilter.get('statuses', {})}"
        )
    manifest = aggregate.get("manifest_shortlist") or {}
    if manifest.get("enabled_rows"):
        lines.append(
            "manifest_shortlist: "
            f"planned_rows={manifest.get('planned_rows', 0)} "
            f"fallback_rows={manifest.get('fallback_rows', 0)} "
            f"files_reduction_ratio={manifest.get('file_reduction_ratio', 0.0)} "
            f"bytes_reduction_ratio={manifest.get('byte_reduction_ratio', 0.0)} "
            f"python_opened_files={manifest.get('python_opened_files', 0)} "
            f"parsed_bytes={manifest.get('parsed_bytes', 0)} "
            f"source_ref_equivalence={_compact_counter_map(manifest.get('source_ref_equivalence') or {})} "
            f"no_evidence_scope_equivalence={_compact_counter_map(manifest.get('no_evidence_scope_equivalence') or {})}"
        )
    for warning in aggregate.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for row in report.get("rows", []):
        row_id = row.get("case_id") or "(unknown)"
        lines.append(
            "- "
            f"{row_id}: {row.get('result_status')} "
            f"elapsed_ms={row.get('elapsed_ms')} "
            f"files={row.get('searched_files')} "
            f"bytes={row.get('searched_bytes')} "
            f"candidates={row.get('candidate_count')} "
            f"context_bytes={row.get('output_context_bytes')}"
        )
        manifest_row = row.get("manifest_shortlist") or {}
        if manifest_row.get("enabled"):
            lines[-1] += (
                f" manifest_source_ref_equivalence={manifest_row.get('source_ref_equivalence')} "
                f"manifest_no_evidence_scope_equivalence={manifest_row.get('no_evidence_scope_equivalence')}"
            )
    return "\n".join(lines)


def render_measured_benchmark_markdown(payload: dict) -> str:
    report = payload["measured_benchmark"]
    aggregate = report.get("aggregate", {})
    lines = [
        "# Measured Local Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Run:** `{report.get('run_id')}`",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        "",
        "## Aggregate",
        "",
        f"- Total elapsed ms: {aggregate.get('total_elapsed_ms', 0)}",
        f"- Searched files: {aggregate.get('searched_files', 0)}",
        f"- Searched bytes: {aggregate.get('searched_bytes', 0)}",
        f"- Candidate count: {aggregate.get('candidate_count', 0)}",
        f"- Output context bytes: {aggregate.get('output_context_bytes', 0)}",
        f"- Statuses: {', '.join(f'{key}={value}' for key, value in sorted((aggregate.get('statuses') or {}).items())) or '(none)'}",
    ]
    lines.extend(_benchmark_gate_markdown_lines(report))
    prefilter = aggregate.get("rg_prefilter") or {}
    if prefilter.get("enabled_rows"):
        lines.extend(
            [
                f"- rg prefilter files reduced: {prefilter.get('files_reduced', 0)}",
                f"- rg prefilter bytes reduced: {prefilter.get('bytes_reduced', 0)}",
                f"- rg prefilter statuses: {', '.join(f'{key}={value}' for key, value in sorted((prefilter.get('statuses') or {}).items())) or '(none)'}",
            ]
        )
    manifest = aggregate.get("manifest_shortlist") or {}
    if manifest.get("enabled_rows"):
        lines.extend(
            [
                f"- manifest shortlist planned rows: {manifest.get('planned_rows', 0)}",
                f"- manifest shortlist fallback rows: {manifest.get('fallback_rows', 0)}",
                f"- manifest shortlist files reduction ratio: {manifest.get('file_reduction_ratio', 0.0)}",
                f"- manifest shortlist bytes reduction ratio: {manifest.get('byte_reduction_ratio', 0.0)}",
                f"- manifest shortlist Python-opened files: {manifest.get('python_opened_files', 0)}",
                f"- manifest shortlist parsed bytes: {manifest.get('parsed_bytes', 0)}",
                f"- manifest shortlist source-ref equivalence: `{_compact_counter_map(manifest.get('source_ref_equivalence') or {})}`",
                f"- manifest shortlist no-evidence scope equivalence: `{_compact_counter_map(manifest.get('no_evidence_scope_equivalence') or {})}`",
            ]
        )
    if aggregate.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in aggregate.get("warnings", []))
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Runs",
                "",
                "| Probe | Source | Status | Elapsed ms | Files | Bytes | Candidates | Context bytes | Manifest source refs | Manifest no-evidence scope | Warnings |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
            ]
        )
        for row in report.get("rows", []):
            row_id = row.get("case_id") or ""
            manifest_row = row.get("manifest_shortlist") or {}
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row_id))} | "
                f"{_escape_markdown_table(str(row.get('query_source', '')))} | "
                f"{_escape_markdown_table(str(row.get('result_status', '')))} | "
                f"{row.get('elapsed_ms', 0)} | "
                f"{row.get('searched_files', 0)} | "
                f"{row.get('searched_bytes', 0)} | "
                f"{row.get('candidate_count', 0)} | "
                f"{row.get('output_context_bytes', 0)} | "
                f"{_escape_markdown_table(str(manifest_row.get('source_ref_equivalence', '')))} | "
                f"{_escape_markdown_table(str(manifest_row.get('no_evidence_scope_equivalence', '')))} | "
                f"{_escape_markdown_table(', '.join(row.get('warnings') or []))} |"
            )
    return "\n".join(lines)


def _compact_counter_map(values: dict) -> str:
    if not values:
        return "(none)"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def render_embedding_experiment_text(payload: dict) -> str:
    report = payload["embedding_experiment"]
    aggregate = report.get("aggregate", {})
    index_metrics = report.get("index_metrics") or aggregate.get("index_metrics", {})
    query_metrics = report.get("query_metrics") or aggregate.get("query_metrics", {})
    backend = report.get("backend", {})
    gate = report.get("embedding_recall_gate", {})
    lines = [
        "Embedding experiment",
        f"Status: {report.get('status')}",
        f"Mode: {report.get('experiment_mode')}",
        f"Backend: {backend.get('selected')} requested={backend.get('requested')} fallback={backend.get('fallback_used')}",
        f"Production status: {report.get('production_status')}",
        (
            "Index: "
            f"documents={index_metrics.get('document_count', 0)} "
            f"build_ms={index_metrics.get('index_build_time_ms', 0)} "
            f"peak_memory_bytes={index_metrics.get('peak_memory_bytes', 0)} "
            f"disk_bytes={index_metrics.get('disk_index_size_bytes', 0)} "
            f"refresh={index_metrics.get('refresh_status')}"
        ),
        (
            "Queries: "
            f"cases={query_metrics.get('case_count', 0)} "
            f"median_ms={query_metrics.get('median_query_latency_ms', 0)} "
            f"p90_ms={query_metrics.get('p90_query_latency_ms', 0)} "
            f"embedding_candidates={query_metrics.get('candidate_count_total', 0)} "
            f"baseline_candidates={query_metrics.get('baseline_candidate_count_total', 0)}"
        ),
        f"embedding_recall_gate: status={gate.get('status')} reasons={', '.join(gate.get('reasons') or []) or '(none)'}",
    ]
    if aggregate.get("cap_hit_labels"):
        lines.append("Cap hits: " + ", ".join(aggregate.get("cap_hit_labels", [])))
    for failure in aggregate.get("failure_modes", []):
        lines.append(f"Failure mode: {failure}")
    for row in report.get("rows", []):
        lines.append(
            "- "
            f"{row.get('case_id')}: {row.get('case_family')} "
            f"embedding_candidates={row.get('candidate_count')} "
            f"baseline_candidates={row.get('baseline_candidate_count')} "
            f"latency_ms={row.get('query_latency_ms')} "
            f"protected_regression={row.get('protected_regression')}"
        )
    return "\n".join(lines)


def render_embedding_experiment_markdown(payload: dict) -> str:
    report = payload["embedding_experiment"]
    aggregate = report.get("aggregate", {})
    index_metrics = report.get("index_metrics") or aggregate.get("index_metrics", {})
    query_metrics = report.get("query_metrics") or aggregate.get("query_metrics", {})
    backend = report.get("backend", {})
    gate = report.get("embedding_recall_gate", {})
    lines = [
        "# Embedding Experiment",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Mode:** `{report.get('experiment_mode')}`",
        f"**Backend:** `{backend.get('selected')}` requested=`{backend.get('requested')}` fallback=`{backend.get('fallback_used')}`",
        f"**Production status:** `{report.get('production_status')}`",
        f"**Default live search changed:** `{report.get('default_live_search_changed')}`",
        "",
        "## Metrics",
        "",
        f"- Documents indexed: {index_metrics.get('document_count', 0)}",
        f"- Index build ms: {index_metrics.get('index_build_time_ms', 0)}",
        f"- Peak memory bytes: {index_metrics.get('peak_memory_bytes', 0)}",
        f"- Disk index bytes: {index_metrics.get('disk_index_size_bytes', 0)}",
        f"- Refresh status: `{index_metrics.get('refresh_status')}`",
        f"- Refresh/no-change cost ms: {index_metrics.get('refresh_no_change_cost_ms', 0)}",
        f"- Query cases: {query_metrics.get('case_count', 0)}",
        f"- Median query ms: {query_metrics.get('median_query_latency_ms', 0)}",
        f"- P90 query ms: {query_metrics.get('p90_query_latency_ms', 0)}",
        f"- Embedding candidate count: {query_metrics.get('candidate_count_total', 0)}",
        f"- Baseline candidate count: {query_metrics.get('baseline_candidate_count_total', 0)}",
        f"- Protected regressions: {query_metrics.get('protected_regressions', 0)}",
        "",
        "## Gate Status",
        "",
        "| Gate | Status | Reasons |",
        "| --- | --- | --- |",
        (
            "| `embedding_recall_gate` | "
            f"`{gate.get('status')}` | "
            f"{_escape_markdown_table(', '.join(gate.get('reasons') or [])) or '(none)'} |"
        ),
    ]
    if aggregate.get("cap_hit_labels"):
        lines.extend(["", "## Caps", ""])
        lines.extend(f"- `{label}`" for label in aggregate.get("cap_hit_labels", []))
    if aggregate.get("failure_modes"):
        lines.extend(["", "## Failure Modes", ""])
        lines.extend(f"- `{failure}`" for failure in aggregate.get("failure_modes", []))
    selected_roots = report.get("scope", {}).get("selected_roots") or []
    if selected_roots:
        lines.extend(["", "## Scope", ""])
        lines.extend(
            f"- `{root.get('root_label')}` `{root.get('kind')}` known=`{root.get('known')}`"
            for root in selected_roots
        )
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Rows",
                "",
                "| Case | Family | Source | Embedding candidates | Baseline candidates | Latency ms | Protected regression |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in report.get("rows", []):
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('case_family', '')))} | "
                f"{_escape_markdown_table(str(row.get('query_source', '')))} | "
                f"{row.get('candidate_count', 0)} | "
                f"{row.get('baseline_candidate_count', 0)} | "
                f"{row.get('query_latency_ms', 0)} | "
                f"`{row.get('protected_regression')}` |"
            )
    lines.extend(["", "## Privacy", ""])
    privacy = report.get("privacy_contract", {})
    for key in (
        "raw_private_text_committed",
        "private_vectors_committed",
        "private_indexes_committed",
        "raw_query_text_committed",
        "raw_local_paths_committed",
        "remote_upload",
        "default_search_promotion",
    ):
        lines.append(f"- {key}: `{privacy.get(key)}`")
    return "\n".join(lines)


def render_impact_benchmark_text(payload: dict) -> str:
    report = payload["impact_benchmark"]
    lines = [
        "Impact benchmark",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Run: {report.get('run_id', '(none)')}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        f"Total wall time ms: {report.get('total_wall_time_ms', 0)}",
    ]
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    if report.get("fixture_errors"):
        lines.append("Fixture errors:")
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
    for gate_name in ("ranking_quality_gate", "rg_prefilter_gate"):
        gate = report.get(gate_name) or {}
        if gate:
            lines.append(
                f"{gate_name}: status={gate.get('status')} "
                f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
            )
    summary = report.get("summary", {})
    if summary:
        lines.append("Summary:")
        for name, row in summary.items():
            lines.append(
                "- "
                f"{name}: hit_rate={row.get('first_pass_hit_rate')} "
                f"recall@10={row.get('mean_recall_at_10')} "
                f"no_evidence={row.get('no_evidence_correct')}/{row.get('no_evidence_cases')} "
                f"tool_calls={row.get('median_tool_calls')} "
                f"subprocess_calls={row.get('median_subprocess_calls')} "
                f"files={row.get('median_files_opened')} "
                f"prefilter_files_reduced={row.get('median_prefilter_files_reduced', 0)} "
                f"context_bytes={row.get('median_output_context_bytes')}"
            )
    for row in report.get("rows", []):
        lines.append(
            "- "
            f"{row.get('case_id')}/{row.get('baseline')}: "
            f"first_pass={row.get('first_pass_hit')} "
            f"wall_ms={row.get('wall_time_ms')} "
            f"subprocess_calls={row.get('subprocess_calls')} "
            f"recall@10={row.get('recall_at_10')} "
            f"no_evidence_correct={row.get('no_evidence_correct')} "
            f"candidates={row.get('candidate_count')} "
            f"context_bytes={row.get('output_context_bytes')}"
        )
    return "\n".join(lines)


def render_impact_benchmark_markdown(payload: dict) -> str:
    report = payload["impact_benchmark"]
    lines = [
        "# JSH-026 Impact Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Run:** `{report.get('run_id', '(none)')}`",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        f"**Total wall time ms:** {report.get('total_wall_time_ms', 0)}",
        "",
        "## Privacy Contract",
        "",
    ]
    privacy = report.get("privacy_contract", {})
    for key, value in privacy.items():
        lines.append(f"- `{key}`: `{value}`")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in report.get("warnings", []))
    if report.get("fixture_errors"):
        lines.extend(["", "## Fixture Errors", ""])
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
    lines.extend(_benchmark_gate_markdown_lines(report))
    if report.get("summary"):
        lines.extend(
            [
                "",
                "## Baseline Summary",
                "",
                "| Baseline | Hit rate | Recall@1 | Recall@3 | Recall@10 | No-evidence | Wall ms | Tool calls | Subprocess calls | Files | Bytes scanned | Bytes parsed | Prefilter files reduced | Prefilter bytes reduced | Context bytes | Candidates |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, row in report.get("summary", {}).items():
            no_evidence = f"{row.get('no_evidence_correct')}/{row.get('no_evidence_cases')}"
            lines.append(
                "| "
                f"{_escape_markdown_table(str(name))} | "
                f"{row.get('first_pass_hit_rate')} | "
                f"{row.get('mean_recall_at_1')} | "
                f"{row.get('mean_recall_at_3')} | "
                f"{row.get('mean_recall_at_10')} | "
                f"{no_evidence} | "
                f"{row.get('median_wall_time_ms')} | "
                f"{row.get('median_tool_calls')} | "
                f"{row.get('median_subprocess_calls')} | "
                f"{row.get('median_files_opened')} | "
                f"{row.get('median_bytes_scanned')} | "
                f"{row.get('median_bytes_parsed')} | "
                f"{row.get('median_prefilter_files_reduced', 0)} | "
                f"{row.get('median_prefilter_bytes_reduced', 0)} | "
                f"{row.get('median_output_context_bytes')} | "
                f"{row.get('median_candidate_count')} |"
            )
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Case Runs",
                "",
                "| Case | Family | Baseline | Outcome | First pass | Recall@1 | Recall@3 | Recall@10 | No-evidence correct | Wall ms | Tool calls | Subprocess calls | Files | Context bytes | Candidates |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in report.get("rows", []):
            no_evidence = row.get("no_evidence_correct")
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('protected_feature_family', '')))} | "
                f"{_escape_markdown_table(str(row.get('baseline', '')))} | "
                f"{_escape_markdown_table(str(row.get('expected_outcome', '')))} | "
                f"{row.get('first_pass_hit')} | "
                f"{row.get('recall_at_1')} | "
                f"{row.get('recall_at_3')} | "
                f"{row.get('recall_at_10')} | "
                f"{'' if no_evidence is None else no_evidence} | "
                f"{row.get('wall_time_ms')} | "
                f"{row.get('tool_calls')} | "
                f"{row.get('subprocess_calls')} | "
                f"{row.get('files_opened')} | "
                f"{row.get('output_context_bytes')} | "
                f"{row.get('candidate_count')} |"
            )
    return "\n".join(lines)


def _benchmark_gate_markdown_lines(report: dict) -> list[str]:
    gates = {
        "ranking_quality_gate": report.get("ranking_quality_gate"),
        "rg_prefilter_gate": report.get("rg_prefilter_gate"),
        "manifest_shortlist_gate": report.get("manifest_shortlist_gate"),
    }
    if not any(gates.values()):
        return []
    lines = ["", "## Gate Status", ""]
    lines.extend(["| Gate | Status | Reasons |", "| --- | --- | --- |"])
    for name, gate in gates.items():
        if not gate:
            continue
        reasons = ", ".join(f"`{reason}`" for reason in gate.get("reasons", [])) or "(none)"
        lines.append(f"| `{name}` | `{gate.get('status')}` | {reasons} |")
    return lines


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
    embedding_spike = report.get("embedding_spike")
    if embedding_spike:
        lines.extend(render_embedding_spike_markdown_lines(embedding_spike))
    return "\n".join(lines)


def render_embedding_spike_text_lines(report: dict) -> list[str]:
    if report.get("status") == "invalid_fixture":
        lines = [
            "Embedding spike: INVALID FIXTURE",
            f"Fixture: {report.get('fixture')}",
        ]
        lines.extend(f"ERROR: {error}" for error in report.get("fixture_errors", []))
        return lines
    summary = report.get("summary", {})
    index_metrics = report.get("index_metrics", {})
    query_metrics = report.get("query_metrics", {})
    lines = [
        "Embedding spike:",
        f"- Vectorizer: {report.get('vectorizer_id')} ({report.get('vector_kind')})",
        f"- Production status: {report.get('production_status')}",
        (
            "- Index: "
            f"docs={index_metrics.get('document_count')} "
            f"features={index_metrics.get('feature_count')} "
            f"bytes={index_metrics.get('serialized_index_bytes')} "
            f"index_ms={index_metrics.get('indexing_time_ms')} "
            f"peak_mem={index_metrics.get('tracemalloc_peak_bytes')}"
        ),
        (
            "- Retrieval: "
            f"vector_hit_rate={summary.get('vector_first_pass_hit_rate')} "
            f"baseline_hit_rate={summary.get('baseline_first_pass_hit_rate')} "
            f"recall@1={summary.get('vector_mean_recall_at_1')} "
            f"recall@3={summary.get('vector_mean_recall_at_3')} "
            f"recall@10={summary.get('vector_mean_recall_at_10')} "
            f"median_query_ms={query_metrics.get('median_query_latency_ms')}"
        ),
        (
            "- Family flags: "
            f"valuable_vector_better={summary.get('valuable_family_vector_better')} "
            f"worse_vector_worse={summary.get('worse_family_vector_worse')} "
            f"expected_matches={summary.get('expected_behavior_matches')}"
        ),
    ]
    return lines


def render_embedding_spike_markdown_lines(report: dict) -> list[str]:
    lines = ["", "## Embedding Spike", ""]
    if report.get("status") == "invalid_fixture":
        lines.extend(
            [
                "**Status:** INVALID FIXTURE",
                f"**Fixture:** `{report.get('fixture')}`",
                "",
                "### Fixture Errors",
                "",
            ]
        )
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
        return lines
    summary = report.get("summary", {})
    index_metrics = report.get("index_metrics", {})
    query_metrics = report.get("query_metrics", {})
    lines.extend(
        [
            f"**Vectorizer:** `{report.get('vectorizer_id')}` / `{report.get('vector_kind')}`",
            f"**Production status:** `{report.get('production_status')}`",
            "",
            report.get("measurement_note", ""),
            "",
            "### Cost Metrics",
            "",
            "| Documents | Features | Serialized bytes | Index ms | Peak memory bytes | Median query ms | Max query ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                "| "
                f"{index_metrics.get('document_count')} | "
                f"{index_metrics.get('feature_count')} | "
                f"{index_metrics.get('serialized_index_bytes')} | "
                f"{index_metrics.get('indexing_time_ms')} | "
                f"{index_metrics.get('tracemalloc_peak_bytes')} | "
                f"{query_metrics.get('median_query_latency_ms')} | "
                f"{query_metrics.get('max_query_latency_ms')} |"
            ),
            "",
            "### Retrieval Summary",
            "",
            "| Vector hit rate | Baseline hit rate | Recall@1 | Recall@3 | Recall@10 | Valuable vector better | Worse vector worse | Expected matches |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                "| "
                f"{summary.get('vector_first_pass_hit_rate')} | "
                f"{summary.get('baseline_first_pass_hit_rate')} | "
                f"{summary.get('vector_mean_recall_at_1')} | "
                f"{summary.get('vector_mean_recall_at_3')} | "
                f"{summary.get('vector_mean_recall_at_10')} | "
                f"{summary.get('valuable_family_vector_better')} | "
                f"{summary.get('worse_family_vector_worse')} | "
                f"{summary.get('expected_behavior_matches')} |"
            ),
            "",
            "### Case Family Results",
            "",
            "| Case | Family | Scenario | Effect | Vector rank | Baseline rank |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for case in report.get("cases", []):
        lines.append(
            "| "
            f"{_escape_markdown_table(case.get('case_id', ''))} | "
            f"{_escape_markdown_table(case.get('case_family', ''))} | "
            f"{_escape_markdown_table(case.get('scenario', ''))} | "
            f"{_escape_markdown_table(case.get('case_family_effect', ''))} | "
            f"{case.get('vector', {}).get('gold_rank')} | "
            f"{case.get('baseline', {}).get('gold_rank')} |"
        )
    guidance = report.get("production_guidance", {})
    if guidance:
        lines.extend(
            [
                "",
                "### Production Guidance",
                "",
                f"- Default live search: `{guidance.get('default_live_search')}`",
                f"- Storage: `{guidance.get('storage')}`",
                f"- Exact refs required: `{guidance.get('exact_refs_required')}`",
                f"- Reason: {guidance.get('reason')}",
            ]
        )
    return lines


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
