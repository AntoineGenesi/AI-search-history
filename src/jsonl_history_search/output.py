from __future__ import annotations

import json


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def render_text(payload: dict) -> str:
    if "install_validation" in payload:
        return render_install_validation_text(payload)
    if "forensics" in payload:
        return render_forensics_text(payload)
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_text(payload)
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
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_markdown(payload)
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
