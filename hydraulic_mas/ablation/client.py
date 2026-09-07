"""The live model client for the direct arms.

Isolated behind one small factory so that importing the harness never requires an
API key, a network, or a langchain version. The offline suite substitutes a stub
with the same signature; nothing else in the package knows the difference.
"""

from __future__ import annotations

from typing import Any

from .schemas import DirectSizing


def build_direct_client(model: Any, *, temperature: float | None = None):
    """Wrap a chat model as ``(system, prompt, tools) -> DirectSizing``.

    ``temperature`` is threaded through so a repeatability sweep can vary the
    seed the only way most APIs allow. Models that reject the parameter are left
    at their default rather than failing the run, because a refused sampling
    knob should not cost a data point.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    def client(system: str, prompt: str, tools: list[Any]) -> DirectSizing:
        runnable = model
        if temperature is not None:
            try:
                runnable = model.bind(temperature=temperature)
            except Exception:  # noqa: BLE001 - an unsupported knob is not a failure
                runnable = model
        if tools:
            # A0.5: the model may call arithmetic tools, then must answer in the
            # schema. A short agent loop rather than a single call, but still no
            # verifier and no repair - which is the whole distinction from A1.
            from langgraph.prebuilt import create_react_agent

            agent = create_react_agent(runnable, tools, response_format=DirectSizing)
            state = agent.invoke({"messages": [SystemMessage(system), HumanMessage(prompt)]})
            return state["structured_response"]
        structured = runnable.with_structured_output(DirectSizing)
        return structured.invoke([SystemMessage(system), HumanMessage(prompt)])

    return client


def build_verified_client(model: Any, *, temperature: float | None = None):
    """Wrap a chat model as ``(system, prompt, tools, schema) -> schema``.

    A0-V needs a schema argument because it fills two different shapes: a set of
    candidates on the opening call, one design on each repair. Kept as a separate
    factory from ``build_direct_client`` rather than a parameter on it, so that
    adding this arm cannot change the behaviour of A0 or A0.5 - the two cells
    whose data is already collected and must stay reproducible.

    ``tools`` is accepted and asserted empty. A0-V is the *no tools* cell of the
    factorial, and the assertion is what stops that from silently ceasing to be
    true after some later refactor.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    def client(system: str, prompt: str, tools: list[Any], schema: type) -> Any:
        assert not tools, "A0-V is the toolless cell; binding tools would collapse the factorial"
        runnable = model
        if temperature is not None:
            try:
                runnable = model.bind(temperature=temperature)
            except Exception:  # noqa: BLE001 - an unsupported knob is not a failure
                runnable = model
        structured = runnable.with_structured_output(schema)
        return structured.invoke([SystemMessage(system), HumanMessage(prompt)])

    return client


def build_full_planner(model: Any, load_tolerance_lookup=None):
    """Wrap the shipped A1 planner as the callable ``run_full_arm`` expects."""
    from langchain_core.messages import HumanMessage

    from ..sizing.agent import build_sizing_planner
    from ..sizing.selection import evaluate_and_select_sizing

    def planner(topology, requirements, contract, load_tolerance):
        agent = build_sizing_planner(model, topology, requirements, contract, load_tolerance)
        from .prompts import render_contract

        payload = (
            "VALIDATED TOPOLOGY:\n" + str(topology)
            + "\n\nACCEPTANCE CONTRACT:\n" + render_contract(contract)
            + "\n\nSize this circuit. Use the tools for every number, and evaluate each "
            "candidate with evaluate_sizing_policy before returning it."
        )
        plan_set = agent.invoke({"messages": [HumanMessage(payload)]})["structured_response"]
        return evaluate_and_select_sizing(
            plan_set, topology, requirements, contract, load_tolerance)

    return planner
