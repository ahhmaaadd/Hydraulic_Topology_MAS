"""Static guard against reading a Pydantic attribute off a plain dict.

This is the defect class behind the live P7-01 crash::

    topology_gaps = blocking_catalog_gaps([g.model_dump() for g in plan.catalog_gaps])
    ...
    ", ".join(sorted({gap.capability for gap in topology_gaps}))
                      ^^^^^^^^^^^^^^  AttributeError: 'dict' object has no attribute 'capability'

The pipeline moves the same payloads back and forth between Pydantic models and
plain dicts - models inside a node, dicts in LangGraph state and in the saved
JSON - so a name can hold either form depending on where it came from. Runtime
tests only catch the mistake if the branch happens to execute, and the branches
that consume ``catalog_gaps`` or ``evidence_gaps`` only execute when a run is
already going wrong.

So this walks the AST instead. Inside each function it tracks names that are
provably dicts - assigned from a known dict-returning helper, from
``.model_dump(...)``, or from a comprehension over one of those - and fails if
any of them is read with attribute syntax.

The check is deliberately conservative. It only follows sources it is certain
about, so it will not catch every possible instance, but it produces no false
positives and it pins the one that shipped.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "hydraulic_mas"

# Helpers whose declared return type is a dict or a list of dicts.
DICT_RETURNING_CALLS = {
    "blocking_catalog_gaps",
    "model_dump",
    "_as_dict",
    "_json_loads",
}

# Attribute names that are legitimate on a dict.
DICT_SAFE_ATTRS = {
    "get",
    "keys",
    "values",
    "items",
    "pop",
    "setdefault",
    "update",
    "copy",
    "clear",
    "fromkeys",
    "popitem",
}


def _is_dict_source(node: ast.AST) -> bool:
    """True when the expression provably evaluates to a dict or list of dicts."""
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in DICT_RETURNING_CALLS:
            return True
        if isinstance(func, ast.Attribute) and func.attr in DICT_RETURNING_CALLS:
            return True
    if isinstance(node, ast.Dict):
        return True
    # [x.model_dump() for x in ...] and friends
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _is_dict_source(node.elt)
    return False


class _Scope(ast.NodeVisitor):
    """Track provably-dict names within one function body.

    Two Python details matter and both produced false positives in the first
    cut of this lint:

    * a comprehension has its own scope, so ``[g.model_dump() for g in gaps]``
      must not leak ``g`` into the enclosing function; and
    * an assignment's right-hand side is evaluated *before* the name is
      rebound, so ``value = value.model_dump()`` is not a dict being read with
      attribute syntax.
    """

    def __init__(self, path: pathlib.Path, dict_names: set[str] | None = None) -> None:
        self.path = path
        self.dict_names: set[str] = set(dict_names or ())
        self.violations: list[str] = []

    # -- binding -----------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        # Evaluate the RHS under the *old* bindings, then rebind.
        self.visit(node.value)
        is_dict = _is_dict_source(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if is_dict:
                    self.dict_names.add(target.id)
                else:
                    self.dict_names.discard(target.id)

    def visit_For(self, node: ast.For) -> None:
        # A for-loop target does leak into the enclosing function scope.
        self.visit(node.iter)
        self._bind_iteration(node.target, node.iter, self.dict_names)
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def _bind_iteration(self, target: ast.AST, iterable: ast.AST, names: set[str]) -> None:
        iterates_dicts = _is_dict_source(iterable) or (
            isinstance(iterable, ast.Name) and iterable.id in self.dict_names
        )
        if isinstance(target, ast.Name):
            if iterates_dicts:
                names.add(target.id)
            else:
                names.discard(target.id)

    def _visit_comprehension(self, node: ast.AST) -> None:
        child = _Scope(self.path, self.dict_names)
        for generator in node.generators:  # type: ignore[attr-defined]
            self.visit(generator.iter)
            child._bind_iteration(generator.target, generator.iter, child.dict_names)
        for part in ("elt", "key", "value"):
            element = getattr(node, part, None)
            if element is not None:
                child.visit(element)
        for generator in node.generators:  # type: ignore[attr-defined]
            for condition in generator.ifs:
                child.visit(condition)
        self.violations.extend(child.violations)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    # -- checking ----------------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if (
            isinstance(value, ast.Name)
            and value.id in self.dict_names
            and node.attr not in DICT_SAFE_ATTRS
        ):
            self.violations.append(
                f"{self.path.name}:{node.lineno}: '{value.id}' holds a dict but is read as "
                f"'.{node.attr}'. Use {value.id}.get({node.attr!r}) instead."
            )
        self.generic_visit(node)


def _scan(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = _Scope(path)
            for statement in node.body:
                scope.visit(statement)
            violations.extend(scope.violations)
    return violations


@pytest.mark.parametrize(
    "module", sorted(PACKAGE.rglob("*.py")), ids=lambda p: p.name
)
def test_no_pydantic_attribute_read_off_a_dict(module: pathlib.Path) -> None:
    violations = _scan(module)
    assert not violations, "\n".join(violations)


def test_the_lint_catches_the_shipped_p7_01_bug() -> None:
    """Guard the guard: the original line must still be detected."""
    source = '''
def _candidate_evaluation(plan, requirements):
    topology_gaps = blocking_catalog_gaps(
        [gap.model_dump(mode="json") for gap in plan.catalog_gaps]
    )
    if topology_gaps:
        errors.append(", ".join(sorted({gap.capability for gap in topology_gaps})))
'''
    tree = ast.parse(source)
    scope = _Scope(pathlib.Path("synthetic.py"))
    for statement in tree.body[0].body:  # type: ignore[attr-defined]
        scope.visit(statement)
    assert any(".capability" in v for v in scope.violations), scope.violations


def test_the_lint_allows_attribute_access_on_real_models() -> None:
    """A plan's own gap objects are models, so ``.capability`` is correct there."""
    source = '''
def _candidate_evaluation(plan, requirements):
    for gap in plan.evidence_gaps:
        warnings.append(f"evidence gap ({gap.capability}): {gap.reason}")
'''
    tree = ast.parse(source)
    scope = _Scope(pathlib.Path("synthetic.py"))
    for statement in tree.body[0].body:  # type: ignore[attr-defined]
        scope.visit(statement)
    assert scope.violations == []
