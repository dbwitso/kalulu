"""Inline calculator. Safe AST evaluation, no eval()."""
from __future__ import annotations

import ast
import math
import operator
import re

from ..core.models import Action, Result
from .base import Provider

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
    ast.BitXor: operator.xor, ast.LShift: operator.lshift, ast.RShift: operator.rshift,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}
_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}
_FUNCS = {
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": lambda x, b=10: math.log(x, b), "ln": math.log, "log2": math.log2, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "fact": math.factorial, "factorial": math.factorial, "min": min, "max": max,
    "hex": hex, "bin": bin, "oct": oct, "rad": math.radians, "deg": math.degrees,
    "gcd": math.gcd, "lcm": math.lcm,
}

_PERCENT_OF = re.compile(r"^\s*([\d.]+)\s*%\s*of\s*(.+)$", re.I)
_LOOKS_MATHY = re.compile(r"[\d)]\s*[-+*/^%×÷]|\b(" + "|".join(_FUNCS) + r")\s*\(|\b(pi|tau)\b|^0[xob]", re.I)


class CalcError(Exception):
    pass


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        l, r = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(r) > 10000:
            raise CalcError("exponent too large")
        if isinstance(node.op, ast.LShift) and r > 4096:
            raise CalcError("shift too large")
        return _BIN[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id.lower() in _NAMES:
        return _NAMES[node.id.lower()]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.lower() in _FUNCS:
        if node.keywords:
            raise CalcError("keywords not allowed")
        args = [_eval(a) for a in node.args]
        if node.func.id.lower() in ("fact", "factorial") and args and args[0] > 5000:
            raise CalcError("too large")
        return _FUNCS[node.func.id.lower()](*args)
    raise CalcError("unsupported expression")


def preprocess(expr: str) -> str:
    e = expr.strip().rstrip("=").strip()
    e = e.replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", "")
    e = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", e)            # 3 x 4
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*\d)", r"(\1/100)", e)  # 15% -> (15/100), keeps 10 % 3
    return e


def evaluate(expr: str):
    m = _PERCENT_OF.match(expr)
    if m:
        return float(m.group(1)) / 100 * evaluate(m.group(2))
    tree = ast.parse(preprocess(expr), mode="eval")
    return _eval(tree)


def fmt(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}" if abs(v) < 10**30 else f"{v:.6e}"
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            return str(v)
        if v.is_integer() and abs(v) < 1e15:
            return f"{int(v):,}"
        if abs(v) >= 1e15 or (abs(v) < 1e-6 and v != 0):
            return f"{v:.6e}"
        return f"{v:,.10f}".rstrip("0").rstrip(".")
    return str(v)


class CalculatorProvider(Provider):
    name = "calculator"
    weight = 2000.0   # always on top when the query is math

    def query(self, text: str) -> list[Result]:
        t = text.strip()
        if not t or not _LOOKS_MATHY.search(t):
            return []
        try:
            value = evaluate(t)
        except (CalcError, SyntaxError, ZeroDivisionError, OverflowError, ValueError, TypeError, RecursionError):
            return []
        shown = fmt(value)
        raw = shown.replace(",", "") if not isinstance(value, str) else shown
        return [Result(
            id="calc", title=shown, subtitle=f"{t} =", icon="accessories-calculator-symbolic",
            category="Calculator", learn=False,
            actions=[
                Action("Copy Answer", "copy", raw),
                Action("Copy Equation", "copy", f"{t} = {raw}", shortcut="Ctrl+↵"),
            ],
            score=0,
        )]
