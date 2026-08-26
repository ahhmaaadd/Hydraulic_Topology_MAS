from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


NODE_TITLES = {
    "extract_requirements": "1. Requirements Extractor",
    "critique_requirements": "2. Requirements Critic",
    "repair_requirements": "Requirements Structural Repair",
    "clarify_requirements": "Requirements Clarification",
    "finalize_requirements": "3. Requirements Gate",
    "requirements_failure": "Requirements Gate Blocked",
    "plan_research": "4. Research Planner",
    "research_dispatch": "5. Research Dispatch",
    "web_research_worker": "6. Parallel Web Research Worker",
    "assess_research_coverage": "7. Research Coverage Critic",
    "research_failure": "Research Gate Blocked",
    "synthesize_research": "8. Research Synthesizer",
    "curate_design_brief": "9. Design-Brief Curator",
    "plan_components": "10. Generic Topology Component Designer",
    "build_netlist": "11. Port-Level Netlist Builder",
    "validate_topology": "12. Deterministic + Engineering Validator",
    "repair_topology_requirements": "Upstream Requirements Repair",
    "targeted_research": "Targeted Research Repair",
    "finalize_topology": "13. Final Topology Output",
}


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


class TerminalReporter:
    def __init__(self, *, compact: bool = False):
        self.console = Console()
        self.compact = compact

    def run_header(self, problem_id: str, problem: str, settings: Any) -> None:
        self.console.rule(f"[bold cyan]{problem_id} — Hydraulic topology workflow")
        self.console.print(Panel(problem, title="Input problem", border_style="cyan"))
        budget = Table(show_header=False, box=None)
        budget.add_row("Design model", settings.model)
        budget.add_row("Research/distillation model", settings.fast_model)
        budget.add_row(
            "Research limits",
            f"{settings.max_research_rounds} rounds / {settings.max_searches} searches / "
            f"{settings.max_documents_per_search} documents per search",
        )
        budget.add_row("Topology repair limit", str(settings.max_topology_rounds))
        budget.add_row("Output boundary", "generic component classes + direct hydraulic port connections; sizing deferred")
        self.console.print(budget)

    def node_update(self, update: dict[str, Any]) -> None:
        if "__interrupt__" in update:
            return
        for node, values in update.items():
            title = NODE_TITLES.get(node, node)
            self.console.rule(f"[bold blue]{title}")
            if not isinstance(values, dict):
                self.console.print(values)
                continue
            if node == "web_research_worker":
                self._research_worker(values)
            elif node == "finalize_requirements":
                self._requirements_gate(values)
            elif node == "plan_components":
                self._component_planner(values)
            elif node == "validate_topology":
                self._validation(values)
            elif node == "finalize_topology" and values.get("final_output"):
                self.final_output(values["final_output"])
            elif self.compact:
                self._compact_values(values)
            else:
                self.console.print(Syntax(_json(values), "json", word_wrap=True))

    def _requirements_gate(self, values: dict[str, Any]) -> None:
        gate = values.get("requirements_gate", {})
        decision = str(gate.get("decision") or "unknown")
        color = "green" if decision == "proceed" else "red"
        summary = Table(show_header=False, box=None)
        summary.add_row("Decision", f"[{color}]{decision.upper()}[/{color}]")
        summary.add_row("Critic status", str(gate.get("completeness_status") or "unknown"))
        summary.add_row("Blocking questions", str(len(gate.get("blocking_question_ids", []))))
        summary.add_row("Structural errors", str(len(gate.get("structural_issues", []))))
        summary.add_row("Advisories", str(len(gate.get("advisory_issues", []))))
        summary.add_row("Assumptions merged", str(len(gate.get("merged_assumption_ids", []))))
        self.console.print(summary)

        findings = Table("Classification", "Detail", show_lines=True)
        for question_id in gate.get("blocking_question_ids", []):
            findings.add_row("BLOCKING QUESTION", str(question_id))
        for issue in gate.get("structural_issues", []):
            findings.add_row("STRUCTURAL ERROR", str(issue))
        for issue in gate.get("advisory_issues", []):
            findings.add_row("ADVISORY", str(issue))
        for note in gate.get("normalizations", []):
            findings.add_row("NORMALIZED", str(note))
        if findings.row_count:
            self.console.print(findings)
        if not self.compact:
            self.console.print(Panel(Syntax(_json(gate), "json", word_wrap=True), title="Requirements gate audit"))

    def _compact_values(self, values: dict[str, Any]) -> None:
        summary = Table(show_header=False, box=None)
        for key, value in values.items():
            if isinstance(value, list):
                rendered = f"{len(value)} item(s)"
            elif isinstance(value, dict):
                rendered = str(value.get("status") or value.get("verdict") or f"{len(value)} fields")
            else:
                rendered = str(value)
            summary.add_row(key, rendered)
        self.console.print(summary)

    def _research_worker(self, values: dict[str, Any]) -> None:
        for record in values.get("research_search_log", []):
            self.console.print(f"[bold]Query:[/bold] {record.get('query')}")
            self.console.print(
                "Candidates: "
                f"{record.get('candidate_count', 0)} | selected: {record.get('selected_count', 0)} | "
                f"full documents: {record.get('extracted_count', 0)} | duplicates skipped: "
                f"{record.get('duplicate_count', 0)}"
            )
            rejected = record.get("rejected_results", [])
            if rejected:
                reasons: dict[str, int] = {}
                for item in rejected:
                    reason = str(item.get("reason") or "rejected")
                    reasons[reason] = reasons.get(reason, 0) + 1
                self.console.print("Rejected: " + ", ".join(f"{key}={value}" for key, value in sorted(reasons.items())))
            if record.get("error"):
                self.console.print(f"[red]Search error:[/red] {record['error']}")
            table = Table("#", "Source", "Quality", "Title", "URL", "Evidence", show_lines=True)
            for index, result in enumerate(record.get("results", []), start=1):
                snippet = str(result.get("content") or "")
                snippet = snippet[:220] if self.compact else snippet[:900]
                table.add_row(
                    str(index),
                    str(result.get("source_kind") or "other"),
                    str(result.get("quality_score") or 0),
                    str(result.get("title") or ""),
                    str(result.get("url") or ""),
                    snippet,
                )
            self.console.print(table)
        self.console.print(Panel(Syntax(_json(values.get("research_findings", [])), "json", word_wrap=True), title="Distilled finding"))

    def _component_planner(self, values: dict[str, Any]) -> None:
        recovery = values.get("component_planner_recovery", [])
        if recovery:
            self.console.print(
                Panel(
                    Syntax(_json(recovery), "json", word_wrap=True),
                    title="Recovered structured-output errors",
                    border_style="yellow",
                )
            )
        trace = values.get("catalog_tool_trace", [])
        if trace:
            self.console.print("[bold]Catalog tool audit trail[/bold]")
            for index, item in enumerate(trace, start=1):
                self.console.print(Panel(Syntax(_json(item), "json", word_wrap=True), title=f"Tool event {index}"))
        evaluations = values.get("candidate_evaluations", [])
        if evaluations:
            table = Table("Candidate", "Eligible", "Score", "Components", "Selected", "Errors / warnings", show_lines=True)
            for item in evaluations:
                findings = [f"ERROR: {value}" for value in item.get("errors", [])]
                findings += [f"WARN: {value}" for value in item.get("warnings", [])]
                table.add_row(
                    str(item.get("candidate_id")),
                    "yes" if item.get("eligible") else "no",
                    str(item.get("score")),
                    str(item.get("component_count")),
                    "YES" if item.get("selected") else "",
                    "\n".join(findings),
                )
            self.console.print(table)
        if not self.compact and values.get("component_candidates"):
            self.console.print(
                Panel(
                    Syntax(_json(values["component_candidates"]), "json", word_wrap=True),
                    title="Candidate component/topology plans",
                )
            )
        self.console.print(Panel(Syntax(_json(values.get("component_plan", {})), "json", word_wrap=True), title="Selected component plan"))

    def _validation(self, values: dict[str, Any]) -> None:
        validation = values.get("topology_validation", {})
        deterministic = validation.get("deterministic", values.get("deterministic_validation", {}))
        table = Table("Check", "Passed", "Details", show_lines=True)
        for check in deterministic.get("checks", []):
            passed = check.get("passed")
            table.add_row(str(check.get("name")), "[green]YES[/green]" if passed else "[red]NO[/red]", str(check.get("details")))
        self.console.print(table)
        issues = deterministic.get("issues", []) + validation.get("design_review", {}).get("design_issues", [])
        if issues:
            issue_table = Table("Severity", "Code", "Scope", "Description", show_lines=True)
            for issue in issues:
                color = "red" if issue.get("severity") == "error" else "yellow"
                issue_table.add_row(
                    f"[{color}]{issue.get('severity')}[/{color}]",
                    str(issue.get("code")),
                    str(issue.get("scope")),
                    str(issue.get("description")),
                )
            self.console.print(issue_table)
        self.console.print(Panel(Syntax(_json(validation), "json", word_wrap=True), title="Combined validation report"))

    def clarification(self, payload: dict[str, Any], *, non_interactive: bool) -> dict[str, Any]:
        self.console.rule("[bold yellow]Requirements clarification required")
        if non_interactive:
            self.console.print("Non-interactive mode: requesting conservative, explicit assumptions.")
            return {"use_assumptions": True}
        answers: list[dict[str, str]] = []
        for question in payload.get("questions", []):
            detail = str(question.get("why_it_matters") or "")
            if question.get("topic"):
                detail += f"\n\nDecision topic: {question.get('topic')}"
            self.console.print(Panel(detail, title=str(question.get("question"))))
            answer = self.console.input("[bold cyan]Your answer:[/bold cyan] ").strip()
            answers.append({"id": str(question.get("id")), "answer": answer})
        return {"answers": answers}

    def final_output(self, output: dict[str, Any]) -> None:
        status = output.get("status")
        style = "green" if status == "validated" else "yellow" if status == "validated_with_warnings" else "red"
        self.console.print(Panel(str(output.get("design_narrative", "")), title=f"Final status: {status}", border_style=style))

        selected = output.get("selected_components", [])
        components = Table("ID", "Component class", "Generic key", "Type", "Functional role", show_lines=True)
        for component in selected:
            components.add_row(
                str(component.get("id")),
                str(component.get("name")),
                str(component.get("catalog_key")),
                str(component.get("comp_type")),
                str(component.get("role")),
            )
        self.console.print(components)

        name_counts: dict[str, int] = {}
        for component in selected:
            name = str(component.get("name") or component.get("id"))
            name_counts[name] = name_counts.get(name, 0) + 1
        labels: dict[str, str] = {}
        for component in selected:
            component_id = str(component.get("id"))
            name = str(component.get("name") or component_id)
            labels[component_id] = name if name_counts[name] == 1 else f"{name} [{component_id}]"

        self.console.print("[bold]Direct port-to-port topology[/bold]")
        connections = Table("Connection", "Line", "Purpose", show_lines=True)
        for connection in output.get("connections", []):
            from_id = str(connection.get("from_component"))
            to_id = str(connection.get("to_component"))
            connections.add_row(
                f"{labels.get(from_id, from_id)}.{connection.get('from_port')} -> "
                f"{labels.get(to_id, to_id)}.{connection.get('to_port')}",
                str(connection.get("line")),
                str(connection.get("notes") or ""),
            )
        self.console.print(connections)

        decisions = output.get("motion_control_decisions", [])
        if decisions:
            table = Table(
                "Phase",
                "Function",
                "Motion",
                "Load",
                "Speed realization",
                "Metering",
                "Chamber",
                "Flow",
                "Compensation",
                "Load control",
                show_lines=True,
            )
            for item in decisions:
                table.add_row(
                    str(item.get("phase_id")),
                    str(item.get("function_id")),
                    str(item.get("motion")),
                    str(item.get("load_type")),
                    str(item.get("speed_realization")),
                    str(item.get("metering_side")),
                    str(item.get("metered_chamber")),
                    str(item.get("metered_flow")),
                    str(item.get("flow_compensation")),
                    str(item.get("load_control")),
                )
            self.console.print(table)

        phase_configurations = output.get("phase_configurations", [])
        if phase_configurations:
            table = Table("Phase", "Function", "Motion", "Component states", "Active", "Forbidden", show_lines=True)
            for item in phase_configurations:
                states = ", ".join(
                    f"{state.get('component_id')}={state.get('state')}"
                    for state in item.get("component_states", [])
                )
                table.add_row(
                    str(item.get("phase_id")),
                    str(item.get("function_id")),
                    str(item.get("motion")),
                    states,
                    ", ".join(item.get("expected_active_function_ids", [])),
                    ", ".join(item.get("forbidden_active_function_ids", [])),
                )
            self.console.print(table)

        if output.get("candidate_evaluations"):
            table = Table("Candidate", "Eligible", "Score", "Selected", show_lines=True)
            for item in output["candidate_evaluations"]:
                table.add_row(
                    str(item.get("candidate_id")),
                    str(item.get("eligible")),
                    str(item.get("score")),
                    str(item.get("selected")),
                )
            self.console.print(table)

        if output.get("component_planner_recovery"):
            self.console.print(
                Panel(
                    Syntax(_json(output["component_planner_recovery"]), "json", word_wrap=True),
                    title="Recovered component-planner output errors",
                    border_style="yellow",
                )
            )

        if output.get("repair_history"):
            self.console.print(
                Panel(Syntax(_json(output["repair_history"]), "json", word_wrap=True), title="Executed repair history")
            )

        if output.get("external_interfaces"):
            interfaces = Table("Component.port", "External system", "Domain", "Notes", show_lines=True)
            for item in output["external_interfaces"]:
                interfaces.add_row(
                    f"{item.get('component_id')}.{item.get('port')}",
                    str(item.get("external_system")),
                    str(item.get("domain")),
                    str(item.get("notes") or ""),
                )
            self.console.print(interfaces)

        if output.get("catalog_gaps") or output.get("evidence_gaps"):
            gaps = Table("Kind", "Capability", "Blocking", "Reason", show_lines=True)
            for item in output.get("catalog_gaps", []):
                gaps.add_row("catalog", str(item.get("capability")), str(item.get("blocking")), str(item.get("reason")))
            for item in output.get("evidence_gaps", []):
                gaps.add_row("evidence", str(item.get("capability")), str(item.get("blocking")), str(item.get("reason")))
            self.console.print(gaps)

        if output.get("repair_stop_reason"):
            self.console.print(
                Panel(str(output["repair_stop_reason"]), title="Repair loop stopped", border_style="yellow")
            )

        self.console.print(Panel(str(output.get("scope_statement", "")), title="Validation scope", border_style="yellow"))
        if output.get("research_audit"):
            audit = output["research_audit"]
            table = Table("Searches", "Rounds", "Unique sources", "Extracted", "Duplicates", "Verified claims")
            table.add_row(
                str(audit.get("searches_used", 0)),
                str(audit.get("rounds_used", 0)),
                str(audit.get("selected_unique_sources", 0)),
                str(audit.get("extracted_documents", 0)),
                str(audit.get("duplicate_urls_skipped", 0)),
                str(audit.get("verified_evidence_claims", 0)),
            )
            self.console.print(table)
        if not self.compact:
            self.console.print(Panel(Syntax(_json(output), "json", word_wrap=True), title="Complete machine-readable output"))

    def sized_output(self, sized: dict[str, Any]) -> None:
        """Render the sizing certificate: what was selected and what was proved."""
        verdict = str(sized.get("verdict", "UNKNOWN"))
        colour = {"PROVED": "green", "UNDECIDED": "yellow"}.get(verdict, "red")
        self.console.rule(f"[bold]Sizing - {verdict}")

        sizing = sized.get("sizing") or {}
        supply = sizing.get("__supply__") or {}
        components = Table("Item", "Selection", "Detail", show_lines=True)
        for component_id, record in sizing.items():
            if component_id == "__supply__" or "bore_mm" not in record:
                continue
            ratio = record["cap_area_m2"] / max(record["annulus_area_m2"], 1e-12)
            components.add_row(
                component_id,
                f"{record['bore_mm']:g}/{record['rod_mm']:g} mm",
                f"cap {record['cap_area_m2'] * 1e6:.0f} mm2, "
                f"annulus {record['annulus_area_m2'] * 1e6:.0f} mm2, ratio {ratio:.2f}",
            )
        if supply:
            components.add_row(
                "Pump", f"{supply.get('displacement_cm3', 0):g} cm3/rev",
                f"{supply.get('flow_m3s', 0) * 60000:.2f} L/min at "
                f"{supply.get('speed_rpm', 1500):g} rpm")
            components.add_row("Relief valve", f"{supply.get('relief_pa', 0) / 1e5:.1f} bar", "")
            components.add_row("Prime mover", f"{supply.get('motor_kw', 0)} kW", "shaft power, worst phase")
            components.add_row("Reservoir", f"{supply.get('reservoir_l', 0):g} L", "three times delivered flow")
            components.add_row("Valves", str(supply.get("valve_size", "")),
                               f"peak return {supply.get('peak_return_lpm', 0):.1f} L/min")
            for label in ("suction", "pressure", "return"):
                if supply.get(f"{label}_tube"):
                    components.add_row(f"{label.title()} line", str(supply[f"{label}_tube"]), "")
        self.console.print(components)

        certificate = sized.get("certificate") or {}
        criteria = certificate.get("criteria") or []
        if criteria:
            table = Table("Criterion", "Required", "Achieved", "Verdict", "Method", show_lines=True)
            for item in criteria:
                mark = {"PROVED": "[green]", "UNDECIDED": "[yellow]"}.get(
                    str(item.get("verdict")), "[red]")
                table.add_row(
                    str(item.get("criterion_id")), str(item.get("required")),
                    str(item.get("achieved")),
                    f"{mark}{item.get('verdict')}[/]", str(item.get("method")),
                )
            self.console.print(table)

        # What the verdict does *not* range over, said out loud. A verdict shown
        # without its coverage reads as "fully verified", and the checks answer
        # about three in five of the stated acceptance criteria.
        coverage = certificate.get("coverage") or {}
        if coverage.get("stated"):
            self.console.print(
                f"[dim]Coverage: {coverage['answered']} of {coverage['stated']} stated "
                f"acceptance criteria answered by {coverage['checks']} checks.[/]")
            reasons: dict[str, int] = {}
            for item in certificate.get("uncovered") or []:
                reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
            if reasons:
                detail = ", ".join(f"{count} {reason.replace('_', ' ')}"
                                   for reason, count in sorted(reasons.items(),
                                                               key=lambda pair: -pair[1]))
                self.console.print(f"[dim]Not answered: {detail}.[/]")

        points = certificate.get("operating_points") or {}
        if points:
            table = Table("Phase", "Regime", "Velocity", "Pump", "Supply", "Exhaust", "Over relief")
            for phase_id, point in points.items():
                table.add_row(
                    phase_id, str(point.get("regime")),
                    f"{point.get('velocity_m_min', 0):.3f} m/min",
                    f"{point.get('pump_pressure_bar', 0):.2f} bar",
                    f"{point.get('supply_pressure_bar', 0):.2f} bar",
                    f"{point.get('exhaust_pressure_bar', 0):.2f} bar",
                    f"{point.get('relief_flow_lpm', 0):.2f} L/min",
                )
            self.console.print(table)

        for finding in certificate.get("findings") or []:
            style = "red" if finding.get("severity") == "error" else "yellow"
            self.console.print(
                f"[{style}][{finding.get('layer')}] {finding.get('code')}[/]: {finding.get('message')}")

        scores = sized.get("candidate_scores") or []
        if scores:
            table = Table("Candidate", "Eligible", "Score", "P/U/R", "Oversizing", "Selected")
            for score in scores:
                table.add_row(
                    str(score.get("candidate_id")), str(score.get("eligible")),
                    f"{score.get('score', 0):.1f}",
                    f"{score.get('proved', 0)}/{score.get('undecided', 0)}/{score.get('refuted', 0)}",
                    f"{score.get('oversizing_index', 0):.3f}", str(score.get("selected")),
                )
            self.console.print(table)

        if sized.get("stop_reason"):
            self.console.print(
                Panel(str(sized["stop_reason"]), title="Sizing stopped", border_style=colour))

    def failure(self, failure: dict[str, Any]) -> None:
        self.console.print(Panel(Syntax(_json(failure), "json", word_wrap=True), title="Workflow stopped safely", border_style="red"))

    def saved(self, path: str) -> None:
        self.console.print(Text(f"Saved JSON result: {path}", style="green"))
