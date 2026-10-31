"""AST static validator for generated strategy code (Section 6 & 10.1)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

ALLOWED_MODULES: frozenset[str] = frozenset({"math", "statistics", "collections"})

FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "input",
        "breakpoint",
        "os",
        "sys",
        "subprocess",
        "socket",
        "shutil",
        "pathlib",
        " ctypes",
        "builtins",
        "__builtins__",
    }
)

REQUIRED_METHODS: tuple[str, ...] = ("__init__", "on_tick", "on_fill", "report")
MAX_LINES: int = 300


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    params: dict[str, int | float] = field(default_factory=dict)
    sweep_params: tuple[str, str] | None = None


def _extract_number(node: ast.AST) -> int | float | None:
    """Extract numeric literal (int or float, not bool) from an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _extract_number(node.operand)
        if inner is not None:
            return -inner if isinstance(node.op, ast.USub) else inner
    return None


def validate(code: str) -> ValidationResult:
    """Statically validate strategy source code against Section 6 rules."""
    errors: list[str] = []
    if not isinstance(code, str) or not code.strip():
        return ValidationResult(ok=False, errors=["Strategy code is empty."])

    lines = code.splitlines()
    if len(lines) > MAX_LINES:
        errors.append(f"Strategy source has {len(lines)} lines (maximum allowed is {MAX_LINES}).")

    try:
        tree = ast.parse(code, filename="strategy.py")
    except SyntaxError as exc:
        return ValidationResult(
            ok=False,
            errors=[f"SyntaxError at line {exc.lineno}: {exc.msg}"],
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if alias.name not in ALLOWED_MODULES or root_mod not in ALLOWED_MODULES:
                    errors.append(
                        f"Forbidden import '{alias.name}' at line {node.lineno} "
                        f"(allowed: {', '.join(sorted(ALLOWED_MODULES))})."
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or not node.module or node.module not in ALLOWED_MODULES:
                errors.append(
                    f"Forbidden from-import '{node.module or '.'}' at line {node.lineno} "
                    f"(allowed: {', '.join(sorted(ALLOWED_MODULES))})."
                )
            else:
                for alias in node.names:
                    if alias.name == "*" or alias.name.startswith("_"):
                        errors.append(f"Forbidden import target '{alias.name}' at line {node.lineno}.")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                errors.append(f"Forbidden identifier '{node.id}' at line {node.lineno}.")
            elif node.id.startswith("__") and node.id.endswith("__"):
                errors.append(f"Forbidden dunder identifier '{node.id}' at line {node.lineno}.")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                errors.append(f"Forbidden attribute '{node.attr}' at line {node.lineno}.")
            elif node.attr.startswith("__") and node.attr.endswith("__"):
                errors.append(f"Forbidden dunder attribute access '.{node.attr}' at line {node.lineno}.")

    # Inspect top-level definitions for PARAMS, SWEEP_PARAMS, and class Strategy
    params_node: ast.AST | None = None
    sweep_node: ast.AST | None = None
    strategy_cls: ast.ClassDef | None = None

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if target.id == "PARAMS":
                        params_node = stmt.value
                    elif target.id == "SWEEP_PARAMS":
                        sweep_node = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "PARAMS":
                params_node = stmt.value
            elif stmt.target.id == "SWEEP_PARAMS":
                sweep_node = stmt.value
        elif isinstance(stmt, ast.ClassDef) and stmt.name == "Strategy":
            strategy_cls = stmt

    parsed_params: dict[str, int | float] = {}
    if params_node is None:
        errors.append("Missing required top-level `PARAMS` dictionary.")
    elif not isinstance(params_node, ast.Dict):
        errors.append("`PARAMS` must be a dictionary literal of numbers.")
    else:
        for k_node, v_node in zip(params_node.keys, params_node.values):
            if not isinstance(k_node, ast.Constant) or not isinstance(k_node.value, str):
                errors.append("All `PARAMS` keys must be strings.")
                continue
            num = _extract_number(v_node) if v_node is not None else None
            if num is None:
                errors.append(f"`PARAMS[{k_node.value!r}]` must be a numeric literal (int or float).")
            else:
                parsed_params[k_node.value] = num
        if not parsed_params:
            errors.append("`PARAMS` dictionary must not be empty.")

    parsed_sweep: tuple[str, str] | None = None
    if sweep_node is None:
        errors.append("Missing required top-level `SWEEP_PARAMS` 2-tuple.")
    elif not isinstance(sweep_node, (ast.Tuple, ast.List)) or len(sweep_node.elts) != 2:
        errors.append("`SWEEP_PARAMS` must be a 2-tuple of two `PARAMS` keys.")
    else:
        k0, k1 = sweep_node.elts
        if (
            isinstance(k0, ast.Constant)
            and isinstance(k0.value, str)
            and isinstance(k1, ast.Constant)
            and isinstance(k1.value, str)
        ):
            s0, s1 = k0.value, k1.value
            if s0 == s1:
                errors.append("`SWEEP_PARAMS` must name two distinct keys in `PARAMS`.")
            for sk in (s0, s1):
                if parsed_params and sk not in parsed_params:
                    errors.append(f"`SWEEP_PARAMS` key {sk!r} is not present in `PARAMS`.")
            parsed_sweep = (s0, s1)
        else:
            errors.append("`SWEEP_PARAMS` elements must be string literals.")

    if strategy_cls is None:
        errors.append("Missing required `class Strategy`.")
    else:
        method_names = {
            item.name for item in strategy_cls.body if isinstance(item, ast.FunctionDef)
        }
        for req_m in REQUIRED_METHODS:
            if req_m not in method_names:
                errors.append(f"`class Strategy` is missing required method `{req_m}`.")

    return ValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        params=parsed_params,
        sweep_params=parsed_sweep,
    )
