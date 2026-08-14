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
    "clarify_requirements": "Requirements Clarification",
    "finalize_requirements": "3. Requirements Gate",
    "plan_research": "4. Research Planner",
    "research_dispatch": "5. Research Dispatch",
    "web_research_worker": "6. Parallel Web Research Worker",
    "assess_research_coverage": "7. Research Coverage Critic",
    "research_failure": "Research Gate Blocked",
    "synthesize_research": "8. Research Synthesizer",
    "curate_design_brief": "9. Design-Brief Curator",
    "plan_components": "10. Catalog-Aware Component Designer",
    "build_netlist": "11. Port-Level Netlist Builder",
    "validate_topology": "12. Deterministic + Engineering Validator",
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
        budget.add_row("Research limits", f"{settings.max_research_rounds} rounds / {settings.max_searches} searches")
        budget.add_row("Topology repair limit", str(settings.max_topology_rounds))
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
            if record.get("error"):
                self.console.print(f"[red]Search error:[/red] {record['error']}")
            table = Table("#", "Title", "URL", "Evidence snippet", show_lines=True)
            for index, result in enumerate(record.get("results", []), start=1):
                snippet = str(result.get("content") or "")
                if self.compact:
                    snippet = snippet[:220]
                table.add_row(str(index), str(result.get("title") or ""), str(result.get("url") or ""), snippet)
            self.console.print(table)
        self.console.print(Panel(Syntax(_json(values.get("research_findings", [])), "json", word_wrap=True), title="Distilled finding"))

    def _component_planner(self, values: dict[str, Any]) -> None:
        trace = values.get("catalog_tool_trace", [])
        if trace:
            self.console.print("[bold]Catalog tool audit trail[/bold]")
            for index, item in enumerate(trace, start=1):
                self.console.print(Panel(Syntax(_json(item), "json", word_wrap=True), title=f"Tool event {index}"))
        self.console.print(Panel(Syntax(_json(values.get("component_plan", {})), "json", word_wrap=True), title="Component plan"))

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
        answers: list[str] = []
        for question in payload.get("questions", []):
            self.console.print(Panel(str(question.get("why_it_matters") or ""), title=str(question.get("question"))))
            answer = self.console.input("[bold cyan]Your answer:[/bold cyan] ").strip()
            answers.append(f"{question.get('id')}: {answer}")
        return {"answers": answers}

    def final_output(self, output: dict[str, Any]) -> None:
        status = output.get("status")
        style = "green" if status == "validated" else "yellow" if status == "validated_with_warnings" else "red"
        self.console.print(Panel(str(output.get("design_narrative", "")), title=f"Final status: {status}", border_style=style))

        components = Table("ID", "Catalog key", "Type", "Role", "Manufacturer", "Sizing check", show_lines=True)
        for component in output.get("selected_components", []):
            components.add_row(
                str(component.get("id")),
                str(component.get("catalog_key")),
                str(component.get("comp_type")),
                str(component.get("role")),
                str(component.get("manufacturer") or "—"),
                "required" if component.get("requires_sizing_verification") else "not flagged",
            )
        self.console.print(components)

        connections = Table("From", "To", "Line", "Notes", show_lines=True)
        for connection in output.get("connections", []):
            connections.add_row(
                f"{connection.get('from_component')}.{connection.get('from_port')}",
                f"{connection.get('to_component')}.{connection.get('to_port')}",
                str(connection.get("line")),
                str(connection.get("notes") or ""),
            )
        self.console.print(connections)

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

        self.console.print(Panel(str(output.get("scope_statement", "")), title="Validation scope", border_style="yellow"))
        if not self.compact:
            self.console.print(Panel(Syntax(_json(output), "json", word_wrap=True), title="Complete machine-readable output"))

    def failure(self, failure: dict[str, Any]) -> None:
        self.console.print(Panel(Syntax(_json(failure), "json", word_wrap=True), title="Workflow stopped safely", border_style="red"))

    def saved(self, path: str) -> None:
        self.console.print(Text(f"Saved JSON result: {path}", style="green"))


