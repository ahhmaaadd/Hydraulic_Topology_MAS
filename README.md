# Hydraulic Topology Multi-Agent System

This is a terminal-only LangGraph system that stops at validated hydraulic
topology. It has no web frontend and no API/backend server.

For each problem it:

1. extracts and critiques structured requirements;
2. plans a compact set of decision-focused web searches;
3. ranks sources, extracts the best unique documents, and verifies claim-level
   evidence before creating targeted follow-ups;
4. synthesizes buildable circuit patterns with citations;
5. lets a catalog-aware designer inspect and select generic functional class
   keys using tools and compare two or three candidate plans;
6. preserves each typed `metering_side`, chamber, flow direction, compensation,
   load-control and synchronization decision without downstream reinterpretation;
7. builds direct component-to-component port connections and explicit valve
   states for every motion phase, with shared endpoints representing branches;
8. proves supply, exhaust, metering, pilot, sequence and forbidden-motion paths
   in separate directed phase graphs, then runs a topology-only LLM review;
9. repairs selection or wiring errors with executable add/delete/replace actions,
   or returns an unsupported pattern to targeted research; and
10. prints every stage and the final selected components/connections in detail.

The schemas and core prompts are adapted from
[`ahhmaaadd/Paper_2_Current`](https://github.com/ahhmaaadd/Paper_2_Current)
at commit `d33920a`. Sizing, simulation, the previous chat backend, and the
frontend are deliberately not included.

## What “validated” means

A valid result means the generic component functions, exact per-phase valve
states, directed supply/exhaust paths, metering direction, pilot release,
sequencing constraints and direct port connectivity are structurally and
behaviorally coherent. It does **not**
mean fabrication-ready. Pump and cylinder sizing, pressure/flow ratings,
reservoir volume, line selection, filters, cooling, prime mover, dynamic
simulation, pressure-loss and thermal analysis, structural checks, hose routing,
functional safety and formal risk assessment remain downstream work.

The 21-class runtime catalog contains only topology-changing classes, including
plain check, true unloading, counterbalance/overcenter, divider/combiner,
pressure-reducing-with-reverse-check and externally piloted sequence functions.
It deliberately
excludes manifolds, tees, pipes/hoses, filters/strainers, coolers, gauges,
temperature/level devices, breathers, motors, couplings, shafts and pressure
switches. Branches use repeated endpoints, for example:

```text
Tank.S -> Pump.S
Pump.P -> Relief Valve.P
Relief Valve.T -> Tank.R
Pump.P -> DCV.P
DCV.T -> Tank.R
```

## Install on Windows PowerShell

Open PowerShell in this project folder, then run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Open `.env` and add:

```text
OPENAI_API_KEY=your_key_here
TAVILY_API_KEY=your_key_here
```

The project now uses the same Aalto model setup as the attached example by
default. The design model is `gpt-5.5-2026-04-24` through Aalto's Responses
endpoint, while extraction and research distillation use
`gpt-4o-2024-11-20` through its deployment endpoint. The existing
`OPENAI_API_KEY` is also sent as the `Ocp-Apim-Subscription-Key` header.

## Install on Linux, macOS, or WSL

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Then add the same API keys to `.env`.

## Run

List the bundled problems without using any API:

```powershell
python run_topology.py --list-problems
```

Run one problem with full terminal detail:

```powershell
python run_topology.py --problem P7-01
```

Run all seven problems:

```powershell
python run_topology.py --all
```

Run a custom statement:

```powershell
python run_topology.py --text "Design a hydraulic system for ..."
```

The installed command is equivalent:

```powershell
hydraulic-topology --problem P7-01
```

By default, the complete final JSON is also written to
`runs/YYYYMMDD-HHMMSS/P7-01.json`. It includes a compact research audit; a
controlled research failure is saved there as well. Use `--no-save` for
terminal-only output or `--compact` to reduce intermediate printing.

Useful safeguards and limits:

```powershell
python run_topology.py --problem P7-01 `
  --max-research-rounds 3 `
  --max-searches 10 `
  --max-topology-rounds 4
```

The research loop does not declare success based on search count. Each critical
circuit-pattern need must have verified placement/connection evidence plus a
verified component operating or safety principle from extracted credible sources.
URLs are deduplicated across parallel workers, low-quality sources are rejected,
and confidence is calculated from source quality rather than accepted from the
LLM. Research supports design principles; it does not require finding an
existing schematic identical to the requested machine.

Optional retrieval settings in `.env` are `MAX_DOCUMENTS_PER_SEARCH` (default
`3`) and `MAX_DOCUMENT_CHARS` (default `12000`).

## Azure or an OpenAI-compatible university gateway

The default Aalto configuration only requires:

```text
OPENAI_API_KEY=...
TAVILY_API_KEY=...
```

It is equivalent to setting:

```text
OPENAI_API_MODE=aalto
OPENAI_BASE_URL=https://aalto-openai-apigw.azure-api.net
HYDRAULIC_MODEL=gpt-5.5-2026-04-24
HYDRAULIC_FAST_MODEL=gpt-4o-2024-11-20
```

For standard Azure OpenAI, set:

```text
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2025-04-01-preview
OPENAI_API_MODE=azure
HYDRAULIC_MODEL=your-design-deployment
HYDRAULIC_FAST_MODEL=your-fast-deployment
```

For another OpenAI-compatible gateway, set `OPENAI_API_MODE=compatible`,
`OPENAI_BASE_URL`, and optionally `OPENAI_DEFAULT_HEADERS_JSON`.

## Test

Tests are offline and do not call OpenAI or Tavily:

```powershell
python -m pytest
```

## Main files

| File | Purpose |
| --- | --- |
| `hydraulic_mas/graph.py` | LangGraph nodes, edges, research loop, and repair routing |
| `hydraulic_mas/schemas.py` | Pydantic contracts through final topology |
| `hydraulic_mas/decision_flow.py` | Canonical typed metering/synchronization propagation and semantic checks |
| `hydraulic_mas/candidate_selection.py` | Deterministic candidate scoring and executable structural repairs |
| `hydraulic_mas/prompts.py` | Adapted prompts plus research coverage and topology-only selection rules |
| `hydraulic_mas/catalog.py` | Read-only generic class index and designer tools |
| `hydraulic_mas/validation.py` | Directed phase-state, metering, sequence, synchronization, catalog, and port checks |
| `hydraulic_mas/terminal.py` | Detailed Rich terminal rendering |
| `CHANGES.md` | Run diagnosis and this topology-only revision record |
| `docs/ARCHITECTURE.md` | Design decisions and boundaries |
| `docs/FLOW.md` | Exact execution and state flow |

The implementation follows the official LangGraph patterns for
[`StateGraph`, loops, and `Send`](https://docs.langchain.com/oss/python/langgraph/workflows-agents),
[`interrupt`/`Command(resume=...)`](https://docs.langchain.com/oss/python/langgraph/interrupts),
and [structured agent output](https://docs.langchain.com/oss/python/langchain/structured-output).
