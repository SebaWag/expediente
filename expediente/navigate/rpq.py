"""Regular path queries: the traversal primitive.

Instead of an ad-hoc walk with a relation filter, a route is written as a *regular
expression over edge labels*. That is not decoration — it is what the destination
graph database (MillenniumDB) is built around, so a pattern written here is a
pattern the backend can evaluate natively instead of us re-implementing it in
Python later.

Grammar (deliberately small):

    expr    := term ('|' term)*          alternation
    term    := factor ('/' factor)*      concatenation
    factor  := atom ('*' | '+' | '?')?   repetition, bounded by max_repetition
    atom    := NAME ['^']                an edge label, '^' means inverted
             | '(' expr ')'

Examples that are the actual product:

    REPRESENTA_A^/REPRESENTA_A           two suppliers sharing a legal representative
    TIENE_DOMICILIO^/TIENE_DOMICILIO     two suppliers sharing an address
    REPRESENTA_A^/REPRESENTA_A/ADJUDICADA_A^/EMITE^
                                         supplier -> shared person -> second supplier
                                         -> award -> buyer   (the hidden network)

Every route is cycle-free and deterministically ordered, so it can be replayed —
which is the whole reason an agent is allowed to choose one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..graph.store import GraphStore, NodeKey
from .paths import Hop, Path

#: Named patterns. The agent picks a name; the reviewer reads the expression.
PATTERNS: dict[str, str] = {
    "comparte_persona": "REPRESENTA_A^/REPRESENTA_A",
    "comparte_domicilio": "TIENE_DOMICILIO^/TIENE_DOMICILIO",
    "vende_a": "ADJUDICADA_A^/EMITE^",
    "red_oculta": "REPRESENTA_A^/REPRESENTA_A/ADJUDICADA_A^/EMITE^",
    "evidencia": "SOPORTADA_POR/EN",
}

DEFAULT_MAX_REPETITION = 3


class PatternError(ValueError):
    """Raised when a pattern does not parse. Never silently ignored."""


@dataclass(frozen=True, slots=True)
class Rel:
    name: str
    inverse: bool = False


@dataclass(frozen=True, slots=True)
class Seq:
    parts: tuple["Expr", ...]


@dataclass(frozen=True, slots=True)
class Alt:
    parts: tuple["Expr", ...]


@dataclass(frozen=True, slots=True)
class Repeat:
    inner: "Expr"
    minimum: int
    maximum: int | None


Expr = Rel | Seq | Alt | Repeat


def parse(pattern: str) -> Expr:
    """Parse a path expression. Raises :class:`PatternError` on anything unexpected."""
    tokens = _tokenize(pattern)
    parser = _Parser(tokens)
    expr = parser.parse_alternation()
    if not parser.at_end():
        raise PatternError(f"unexpected trailing input at token {parser.peek()!r}")
    return expr


def _tokenize(pattern: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char.isspace():
            index += 1
            continue
        if char in "^/|*+?()":
            tokens.append(char)
            index += 1
            continue
        start = index
        while index < len(pattern) and (pattern[index].isalnum() or pattern[index] == "_"):
            index += 1
        if start == index:
            raise PatternError(f"unexpected character {char!r} at position {index}")
        tokens.append(pattern[start:index].upper())
    return tokens


class _Parser:
    def __init__(self, tokens: Sequence[str]) -> None:
        self._tokens = list(tokens)
        self._position = 0

    def at_end(self) -> bool:
        return self._position >= len(self._tokens)

    def peek(self) -> str | None:
        return None if self.at_end() else self._tokens[self._position]

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise PatternError("unexpected end of pattern")
        self._position += 1
        return token

    def parse_alternation(self) -> Expr:
        parts = [self.parse_sequence()]
        while self.peek() == "|":
            self.take()
            parts.append(self.parse_sequence())
        return parts[0] if len(parts) == 1 else Alt(tuple(parts))

    def parse_sequence(self) -> Expr:
        parts = [self.parse_factor()]
        while self.peek() == "/":
            self.take()
            parts.append(self.parse_factor())
        return parts[0] if len(parts) == 1 else Seq(tuple(parts))

    def parse_factor(self) -> Expr:
        atom = self.parse_atom()
        token = self.peek()
        if token == "*":
            self.take()
            return Repeat(atom, minimum=0, maximum=None)
        if token == "+":
            self.take()
            return Repeat(atom, minimum=1, maximum=None)
        if token == "?":
            self.take()
            return Repeat(atom, minimum=0, maximum=1)
        return atom

    def parse_atom(self) -> Expr:
        token = self.take()
        if token == "(":
            inner = self.parse_alternation()
            if self.take() != ")":
                raise PatternError("unbalanced parentheses")
            return inner
        if not token[0].isalnum():
            raise PatternError(f"expected an edge label, found {token!r}")
        inverse = False
        if self.peek() == "^":
            self.take()
            inverse = True
        return Rel(token, inverse=inverse)


def match(
    store: GraphStore,
    pattern: str,
    start: NodeKey,
    *,
    max_repetition: int = DEFAULT_MAX_REPETITION,
) -> list[Path]:
    """Evaluate a pattern from ``start``. Accepts a named pattern or an expression."""
    expression = PATTERNS.get(pattern, pattern)
    try:
        parsed = parse(expression)
    except PatternError as error:
        raise PatternError(f"{pattern!r}: {error}") from error

    results = _evaluate(store, parsed, start, max_repetition)
    return sorted(results, key=lambda path: (path.length, path.nodes))


def _evaluate(
    store: GraphStore, expr: Expr, start: NodeKey, max_repetition: int
) -> list[Path]:
    if isinstance(expr, Rel):
        return _step(store, start, expr)
    if isinstance(expr, Seq):
        current = [Path(nodes=(start,), hops=())]
        for part in expr.parts:
            extended: list[Path] = []
            for path in current:
                for branch in _evaluate(store, part, path.end, max_repetition):
                    merged = _merge(path, branch)
                    if merged is not None:
                        extended.append(merged)
            current = extended
            if not current:
                return []
        return current
    if isinstance(expr, Alt):
        branches: list[Path] = []
        for part in expr.parts:
            branches.extend(_evaluate(store, part, start, max_repetition))
        return branches
    if isinstance(expr, Repeat):
        return _repeat(store, expr, start, max_repetition)
    raise PatternError(f"unsupported expression {expr!r}")


def _step(store: GraphStore, node: NodeKey, rel: Rel) -> list[Path]:
    """One edge, in the direction the expression asked for."""
    if rel.inverse:
        edges = [(source, props) for source, _dst, props in store.incoming(rel.name, node)]
        return [Path(nodes=(node, source), hops=(Hop(node, rel.name, source, props, reversed=True),))
                for source, props in edges]
    edges = [(target, props) for _src, target, props in store.edges(rel.name, src=node)]
    return [Path(nodes=(node, target), hops=(Hop(node, rel.name, target, props),))
            for target, props in edges]


def _repeat(
    store: GraphStore, expr: Repeat, start: NodeKey, max_repetition: int
) -> list[Path]:
    upper = expr.maximum if expr.maximum is not None else max_repetition
    results: list[Path] = []
    frontier = _evaluate(store, expr.inner, start, max_repetition)
    if expr.minimum == 0:
        results.append(Path(nodes=(start,), hops=()))
    for _ in range(min(upper, max_repetition)):
        results.extend(frontier)
        extended: list[Path] = []
        for path in frontier:
            for branch in _evaluate(store, expr.inner, path.end, max_repetition):
                merged = _merge(path, branch)
                if merged is not None:
                    extended.append(merged)
        frontier = extended
        if not frontier:
            break
    return results


def _merge(left: Path, right: Path) -> Path | None:
    """Concatenate two routes, refusing to revisit a node.

    The junction is the one node both sides legitimately share: ``A/P`` means the
    end of ``A`` is the start of ``P``. Checking the full node sets would reject
    every concatenation of length two or more, which is every useful pattern —
    so the comparison excludes the junction and nothing else.
    """
    if set(left.nodes[:-1]) & set(right.nodes):
        return None
    return Path(nodes=left.nodes + right.nodes[1:], hops=left.hops + right.hops)


def endpoints(paths: Iterable[Path]) -> list[NodeKey]:
    """Distinct destination nodes, ordered — the answer to "who else is connected"."""
    return sorted({path.end for path in paths})
