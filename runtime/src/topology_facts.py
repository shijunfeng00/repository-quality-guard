"""Language-neutral ownership and reuse topology facts.

Language providers translate syntax/type-system constructs into this model. Quality
policy consumes the normalized graph instead of branching on language names whenever
those facts are available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .model import Confidence, Definition, Usage


class OwnerKind(StrEnum):
    """Classify an authored architectural owner.

    The shared policy layer uses these values without depending on language syntax.
    """

    MODULE = "module"
    CLASS = "class"
    NAMESPACE = "namespace"
    SUBSYSTEM = "subsystem"
    UNKNOWN = "unknown"


class Visibility(StrEnum):
    """Describe normalized authored-symbol visibility.

    Providers map language-specific access rules or conventions onto these values.
    """

    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class UsageKind(StrEnum):
    """Classify a semantic relationship between two normalized symbols.

    Direct calls remain distinct from callback, protocol, state and dependency edges.
    """

    DIRECT_CALL = "direct_call"
    MAY_DISPATCH = "may_dispatch"
    CALLABLE_REFERENCE = "callable_reference"
    CALLBACK_REGISTRATION = "callback_registration"
    MAY_CALLBACK = "may_callback"
    PROTOCOL_HOOK = "protocol_hook"
    OVERRIDE = "override"
    STATIC_POLYMORPHIC_DISPATCH = "static_polymorphic_dispatch"
    FIELD_ACCESS = "field_access"
    DEPENDENCY = "dependency"
    CLOSURE_CAPTURE = "closure_capture"
    INHERITANCE = "inheritance"


class ContractOwnership(StrEnum):
    """Classify who owns a runtime contract inspected by a quality rule.

    Unknown ownership stays explicit instead of being guessed as internal or external.
    """

    INTERNAL_FORMAL = "internal_formal"
    EXTERNAL_OPTIONAL = "external_optional"
    DYNAMIC_BOUNDARY = "dynamic_boundary"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class SymbolFact:
    """Represent one normalized authored definition.

    The record keeps only facts that shared quality policy may consume across languages.
    """

    symbol_id: str
    language: str
    kind: str
    owner_id: str
    owner_kind: OwnerKind
    visibility: Visibility
    path: Path
    line: int
    end_line: int
    lines: int
    parameter_count: int = 0
    name: str = ""
    module: str = ""
    qualname: str = ""
    decorators: tuple[str, ...] = ()
    bases: tuple[str, ...] = ()
    exported: bool = False
    nested: bool = False
    test: bool = False
    fingerprint: str = ""
    generated: bool = False
    foreign: bool = False


@dataclass(slots=True, frozen=True)
class OwnerFact:
    """Represent a normalized architectural owner.

    An owner may be a module, class, namespace or later an explicit subsystem.
    """

    owner_id: str
    language: str
    kind: OwnerKind
    path: Path
    line: int


@dataclass(slots=True, frozen=True)
class UsageEdge:
    """Represent one typed static relationship between symbols or owners.

    The edge kind preserves how a target is consumed instead of collapsing all usage.
    """

    source_id: str
    target_id: str
    kind: UsageKind
    path: Path
    line: int
    confidence: Confidence = "high"


@dataclass(slots=True, frozen=True)
class ContractFact:
    """Represent one normalized runtime-contract access and its static ownership.

    Language providers record the access shape separately from the ownership verdict so
    shared policy can distinguish formal internal contracts, declared external optional
    contracts, explicit dynamic boundaries and unresolved cases without guessing.
    """

    path: Path
    line: int
    receiver: str
    ownership: ContractOwnership
    confidence: Confidence
    operation: str = ""
    selector: str = ""
    owner_id: str = ""
    evidence: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class CompilerDiagnosticFact:
    """Compiler-origin candidate in the same immutable repository topology.

    A type-checker diagnostic is an observation, not a runtime proof or a QG
    BLOCKER. The declaration and field are bound by AST/typed symbol identity,
    never by interpreting a developer-chosen identifier as an effect.
    """

    path: Path
    line: int
    column: int
    source_sha256: str
    native_rule: str
    owner_symbol_id: str
    member: str
    binding: str
    provider_identity: tuple[tuple[str, str], ...]
    capability: str = "TYPECHECKER_OBSERVATION_ONLY"


@dataclass(slots=True, frozen=True)
class InstanceFieldAccessFact:
    """A located authored instance-parameter field operation, not a MUST proof.

    The owner and method come from one normalized symbol graph.  Binding means
    the first formal parameter was not rebound within its lexical method body;
    runtime dispatch, aliases and inherited implementations remain open.
    """

    path: Path
    line: int
    column: int
    source_sha256: str
    owner_symbol_id: str
    method_symbol_id: str
    receiver_parameter_index: int
    member: str
    operation: str
    capability: str = "LEXICAL_INSTANCE_RECEIVER_OPEN_DISPATCH"


@dataclass(slots=True, frozen=True)
class FieldInitializationFact:
    """Located definite-field witness from a bounded static CFG.

    ``UNKNOWN`` is absence of proof, not evidence of absent runtime state.
    """

    path: Path
    line: int
    column: int
    source_sha256: str
    owner_symbol_id: str
    method_symbol_id: str
    member: str
    verdict: str
    witness_lines: tuple[int, ...]
    capability: str = "BOUNDED_SOURCE_NORMAL_CFG_OPEN_RUNTIME"


@dataclass(slots=True, frozen=True)
class ConstructorLifecycleFact:
    """Authored constructor and C3 observation, not a runtime MUST claim.

    Keep only source-certified constructor owner and C3 inheritance observations.
    """

    path: Path
    line: int
    source_sha256: str
    owner_symbol_id: str
    constructor_symbol_id: str
    mro_symbol_ids: tuple[str, ...]
    super_init_calls: tuple[tuple[str, int], ...]
    capability: str
    # Authored post-construction entry name -> unique C3 declaration identity.
    # This is a source-bound candidate; runtime dispatch remains OPEN.
    post_init_method_entries: tuple[tuple[str, str], ...] = ()
    unknown_method_entries: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TypedSchemaFieldFact:
    """One authored TypedDict field; spelling is never a type/effect proof.

    Bind required TypedDict fields to declared source identities and exact annotations.
    """

    schema_symbol_id: str
    path: Path
    line: int
    source_sha256: str
    field: str
    annotation: str
    required: bool
    type_binding: str
    capability: str = "SOURCE_AUTHORED_SCHEMA_OPEN_RUNTIME"


@dataclass(slots=True, frozen=True)
class TypedProducerFact:
    """A source-located producer declaration, not verified runtime output.

    Describe the origin and literal keys of a statically authored schema producer.
    """

    schema_symbol_id: str
    producer_symbol_id: str
    path: Path
    line: int
    source_sha256: str
    kind: str
    observed_literal_keys: tuple[str, ...] = ()
    capability: str = "SOURCE_DECLARED_PRODUCER_OPEN_RUNTIME"


@dataclass(slots=True, frozen=True)
class TypedConsumerFact:
    """Located TypedDict field read with syntactic guard, never a runtime proof.

    Represent typed field reads and the limited guards witnessed at the consumer.
    """

    schema_symbol_id: str
    consumer_symbol_id: str
    path: Path
    line: int
    column: int
    source_sha256: str
    receiver: str
    field: str
    required: bool
    declared_type: str
    observation: str
    guard_observation: str = "NONE"
    guard_line: int = 0
    presence_witness_line: int = 0
    capability: str = "SOURCE_BOUND_CONSUMER_OPEN_CFG_AND_RUNTIME"


@dataclass(slots=True, frozen=True)
class ParameterForwardingFact:
    """Authored formal-to-formal possible forwarding, never a content guarantee.

    Both function bodies and their actual source hashes are pinned. Dynamic
    dispatch and external input completeness remain explicitly open.
    """

    source_symbol_id: str
    source_parameter_index: int
    target_symbol_id: str
    target_parameter_index: int
    path: Path
    line: int
    column: int
    caller_sha256: str
    callee_sha256: str
    capability: str = "SOURCE_BOUND_MAY_FORWARD_OPEN_RUNTIME"


@dataclass(slots=True, frozen=True)
class ExceptionHandlerReadFact:
    """Conditional local-unbound read on a caught RHS-exception path.

    Link a handler read to a potentially interrupted local assignment, not a guaranteed exception.
    """

    path: Path
    line: int
    column: int
    source_sha256: str
    owner_symbol_id: str
    local_name: str
    assignment_line: int
    try_line: int
    handler_line: int
    capability: str = "SOURCE_BOUND_EXCEPTION_PATH_OPEN_PROVIDER"


@dataclass(slots=True, frozen=True)
class RepositoryTopology:
    """Hold the repository-level normalized ownership and reuse graph.

    The graph is generated during scanning but remains policy-neutral in Phase 1.
    """

    symbols: tuple[SymbolFact, ...]
    owners: tuple[OwnerFact, ...]
    edges: tuple[UsageEdge, ...]
    contracts: tuple[ContractFact, ...] = ()
    compiler_diagnostics: tuple[CompilerDiagnosticFact, ...] = ()
    instance_field_accesses: tuple[InstanceFieldAccessFact, ...] = ()
    constructor_lifecycles: tuple[ConstructorLifecycleFact, ...] = ()
    field_initializations: tuple[FieldInitializationFact, ...] = ()
    typed_schema_fields: tuple[TypedSchemaFieldFact, ...] = ()
    typed_producers: tuple[TypedProducerFact, ...] = ()
    typed_consumers: tuple[TypedConsumerFact, ...] = ()
    parameter_forwardings: tuple[ParameterForwardingFact, ...] = ()
    exception_handler_reads: tuple[ExceptionHandlerReadFact, ...] = ()
    _incoming_index: dict[str, tuple[UsageEdge, ...]] = field(
        init=False, repr=False, compare=False
    )
    _outgoing_index: dict[str, tuple[UsageEdge, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Build stable source/target indices from the immutable edge collection.

        Returns:
            None; the declared derived indices are initialized exactly once.
        """
        incoming: dict[str, list[UsageEdge]] = {}
        outgoing: dict[str, list[UsageEdge]] = {}
        for edge in self.edges:
            incoming.setdefault(edge.target_id, []).append(edge)
            outgoing.setdefault(edge.source_id, []).append(edge)
        object.__setattr__(
            self,
            "_incoming_index",
            {key: tuple(rows) for key, rows in incoming.items()},
        )
        object.__setattr__(
            self,
            "_outgoing_index",
            {key: tuple(rows) for key, rows in outgoing.items()},
        )

    def incoming(
        self, target_id: str, kinds: tuple[UsageKind, ...] = ()
    ) -> tuple[UsageEdge, ...]:
        """Return stable incoming edges for a target, optionally filtered by kind.

        Args:
            target_id: Normalized target symbol identifier.
            kinds: Accepted edge kinds; empty means all kinds.

        Returns:
            Incoming edges in stable repository order.
        """
        accepted = frozenset(kinds)
        rows = self._incoming_index.get(target_id, ())
        return (
            rows
            if not accepted
            else tuple(edge for edge in rows if edge.kind in accepted)
        )

    def state_components(self, owner_id: str) -> tuple[tuple[str, ...], ...]:
        """Return field-connected state components for one authored owner.

        Methods/functions and fields participate only when a FIELD_ACCESS edge links
        them. Isolated callables without state access are omitted rather than treated
        as artificial singleton owner clusters.

        Args:
            owner_id: Normalized class/module owner identifier.

        Returns:
            Stable connected components containing at least one field and callable.
        """
        owned = {
            symbol.symbol_id: symbol
            for symbol in self.symbols
            if symbol.owner_id == owner_id
        }
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            if edge.kind is not UsageKind.FIELD_ACCESS:
                continue
            if edge.source_id not in owned or edge.target_id not in owned:
                continue
            adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
            adjacency.setdefault(edge.target_id, set()).add(edge.source_id)

        components: list[tuple[str, ...]] = []
        pending = set(adjacency)
        while pending:
            start = min(pending)
            stack = [start]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(sorted(adjacency[current] - visited, reverse=True))
            pending.difference_update(visited)
            has_field = any(owned[item].kind == "field" for item in visited)
            has_callable = any(
                owned[item].kind in {"function", "method"} for item in visited
            )
            if has_field and has_callable:
                components.append(tuple(sorted(visited)))
        return tuple(sorted(components))

    def outgoing(
        self, source_id: str, kinds: tuple[UsageKind, ...] = ()
    ) -> tuple[UsageEdge, ...]:
        """Return stable outgoing edges for a source, optionally filtered by kind.

        Args:
            source_id: Normalized source symbol or owner identifier.
            kinds: Accepted edge kinds; empty means all kinds.

        Returns:
            Outgoing edges in stable repository order.
        """
        accepted = frozenset(kinds)
        rows = self._outgoing_index.get(source_id, ())
        return (
            rows
            if not accepted
            else tuple(edge for edge in rows if edge.kind in accepted)
        )


def merge_topologies(topologies: tuple[RepositoryTopology, ...]) -> RepositoryTopology:
    """Merge language-provider topology into one stable repository graph.

    Providers own syntax extraction, while shared QG policy consumes one normalized
    graph.  The merge therefore deduplicates identical immutable facts but rejects
    conflicting symbol/owner identities instead of silently choosing one provider.

    Args:
        topologies: Provider graphs generated from the same repository snapshot.

    Returns:
        One deterministically ordered normalized repository topology.

    Raises:
        ValueError: Two providers emitted different facts for the same symbol/owner id.
    """
    symbols: dict[str, SymbolFact] = {}
    owners: dict[str, OwnerFact] = {}
    edges: set[UsageEdge] = set()
    contracts: set[ContractFact] = set()
    compiler_diagnostics: set[CompilerDiagnosticFact] = set()
    instance_field_accesses: set[InstanceFieldAccessFact] = set()
    constructor_lifecycles: set[ConstructorLifecycleFact] = set()
    field_initializations: set[FieldInitializationFact] = set()
    typed_schema_fields: set[TypedSchemaFieldFact] = set()
    typed_producers: set[TypedProducerFact] = set()
    typed_consumers: set[TypedConsumerFact] = set()
    parameter_forwardings: set[ParameterForwardingFact] = set()
    exception_handler_reads: set[ExceptionHandlerReadFact] = set()

    for topology in topologies:
        for symbol in topology.symbols:
            if symbol.symbol_id in symbols and symbols[symbol.symbol_id] != symbol:
                raise ValueError(f"conflicting normalized symbol: {symbol.symbol_id}")
            symbols[symbol.symbol_id] = symbol
        for owner in topology.owners:
            if owner.owner_id in owners and owners[owner.owner_id] != owner:
                raise ValueError(f"conflicting normalized owner: {owner.owner_id}")
            owners[owner.owner_id] = owner
        edges.update(topology.edges)
        contracts.update(topology.contracts)
        compiler_diagnostics.update(topology.compiler_diagnostics)
        instance_field_accesses.update(topology.instance_field_accesses)
        constructor_lifecycles.update(topology.constructor_lifecycles)
        field_initializations.update(topology.field_initializations)
        typed_schema_fields.update(topology.typed_schema_fields)
        typed_producers.update(topology.typed_producers)
        typed_consumers.update(topology.typed_consumers)
        parameter_forwardings.update(topology.parameter_forwardings)
        exception_handler_reads.update(topology.exception_handler_reads)

    return RepositoryTopology(
        symbols=tuple(sorted(symbols.values(), key=lambda item: item.symbol_id)),
        owners=tuple(sorted(owners.values(), key=lambda item: item.owner_id)),
        edges=tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.source_id,
                    item.target_id,
                    item.kind,
                ),
            )
        ),
        contracts=tuple(
            sorted(
                contracts,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.receiver,
                    item.operation,
                    item.selector,
                ),
            )
        ),
        compiler_diagnostics=tuple(
            sorted(
                compiler_diagnostics,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.column,
                    item.native_rule,
                    item.owner_symbol_id,
                    item.member,
                ),
            )
        ),
        instance_field_accesses=tuple(
            sorted(
                instance_field_accesses,
                key=lambda item: (
                    item.path.as_posix(),
                    item.line,
                    item.column,
                    item.method_symbol_id,
                    item.member,
                    item.operation,
                ),
            )
        ),
        constructor_lifecycles=tuple(
            sorted(
                constructor_lifecycles,
                key=lambda x: (x.path.as_posix(), x.line, x.owner_symbol_id),
            )
        ),
        field_initializations=tuple(
            sorted(
                field_initializations,
                key=lambda x: (
                    x.path.as_posix(),
                    x.line,
                    x.column,
                    x.owner_symbol_id,
                    x.member,
                ),
            )
        ),
        typed_schema_fields=tuple(
            sorted(
                typed_schema_fields,
                key=lambda x: (x.path.as_posix(), x.line, x.schema_symbol_id, x.field),
            )
        ),
        typed_consumers=tuple(
            sorted(
                typed_consumers,
                key=lambda x: (
                    x.path.as_posix(),
                    x.line,
                    x.column,
                    x.schema_symbol_id,
                    x.consumer_symbol_id,
                ),
            )
        ),
        typed_producers=tuple(
            sorted(
                typed_producers,
                key=lambda x: (
                    x.path.as_posix(),
                    x.line,
                    x.producer_symbol_id,
                    x.schema_symbol_id,
                ),
            )
        ),
        parameter_forwardings=tuple(
            sorted(
                parameter_forwardings,
                key=lambda x: (
                    x.path.as_posix(),
                    x.line,
                    x.column,
                    x.source_symbol_id,
                    x.target_symbol_id,
                    x.source_parameter_index,
                    x.target_parameter_index,
                ),
            )
        ),
        exception_handler_reads=tuple(
            sorted(
                exception_handler_reads,
                key=lambda x: (
                    x.path.as_posix(),
                    x.line,
                    x.column,
                    x.owner_symbol_id,
                    x.local_name,
                ),
            )
        ),
    )


def python_usage_edge(usage: Usage, target: Definition) -> UsageEdge:
    """Translate a resolved Python usage into a normalized typed edge.

    Args:
        usage: Static Python usage after repository-local target resolution.
        target: Resolved repository definition consumed by the usage.

    Returns:
        A direct-call or callable-reference edge with the lexical source symbol.
    """
    source_id = (
        f"{usage.module}.{usage.owner_qualname}"
        if usage.module and usage.owner_qualname
        else usage.owner_qualname or usage.module
    )
    return UsageEdge(
        source_id=source_id,
        target_id=target.symbol_id,
        kind=(UsageKind.DIRECT_CALL if usage.is_call else UsageKind.CALLABLE_REFERENCE),
        path=usage.path,
        line=usage.line,
    )


def python_topology(
    definitions: list[Definition], resolved_edges: list[UsageEdge]
) -> RepositoryTopology:
    """Normalize existing Python scanner facts without changing rule decisions.

    Args:
        definitions: Existing resolved Python definitions from the scanner.
        resolved_edges: Typed repository-local usage edges produced during resolution.

    Returns:
        Repository topology containing symbols, module/class owners and usage edges.
    """
    symbol_facts: list[SymbolFact] = []
    for definition in definitions:
        if definition.kind == "method":
            owner_qualname, _, _ = definition.qualname.rpartition(".")
            owner_id = (
                f"{definition.module}.{owner_qualname}"
                if definition.module and owner_qualname
                else owner_qualname or definition.module
            )
            owner_kind = OwnerKind.CLASS
        else:
            owner_id = definition.module
            owner_kind = OwnerKind.MODULE
        name = definition.name
        if name.startswith("__") and not name.endswith("__"):
            visibility = Visibility.PRIVATE
        elif name.startswith("_") and not name.endswith("__"):
            visibility = Visibility.INTERNAL
        else:
            visibility = Visibility.PUBLIC
        symbol_facts.append(
            SymbolFact(
                symbol_id=definition.symbol_id,
                language="python",
                kind=definition.kind,
                owner_id=owner_id,
                owner_kind=owner_kind,
                visibility=visibility,
                path=definition.path,
                line=definition.line,
                end_line=definition.end_line,
                lines=definition.lines,
                parameter_count=definition.parameter_count,
                nested=definition.nested,
            )
        )
    symbols = tuple(sorted(symbol_facts, key=lambda item: item.symbol_id))
    owner_map: dict[str, OwnerFact] = {}
    for definition in definitions:
        if definition.module and definition.module not in owner_map:
            owner_map[definition.module] = OwnerFact(
                owner_id=definition.module,
                language="python",
                kind=OwnerKind.MODULE,
                path=definition.path,
                line=1,
            )
        if definition.kind != "class":
            continue
        owner_map[definition.symbol_id] = OwnerFact(
            owner_id=definition.symbol_id,
            language="python",
            kind=OwnerKind.CLASS,
            path=definition.path,
            line=definition.line,
        )
    edges = tuple(
        sorted(
            resolved_edges,
            key=lambda item: (
                item.path.as_posix(),
                item.line,
                item.source_id,
                item.target_id,
                item.kind,
            ),
        )
    )
    return RepositoryTopology(
        symbols=symbols,
        owners=tuple(sorted(owner_map.values(), key=lambda item: item.owner_id)),
        edges=edges,
    )
