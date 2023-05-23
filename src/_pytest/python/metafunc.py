import dataclasses
import enum
import itertools
from collections import Counter
from collections import defaultdict
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Pattern
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union

from . import nodes
from _pytest import fixtures
from _pytest._io.saferepr import saferepr
from _pytest.compat import ascii_escaped
from _pytest.compat import assert_never
from _pytest.compat import final
from _pytest.compat import get_default_arg_names
from _pytest.compat import NOTSET
from _pytest.compat import STRING_TYPES
from _pytest.config import Config
from _pytest.deprecated import check_ispytest
from _pytest.mark import Mark
from _pytest.mark import MarkDecorator
from _pytest.mark import ParameterSet
from _pytest.mark.structures import normalize_mark_list
from _pytest.outcomes import fail
from _pytest.scope import Scope

if TYPE_CHECKING:
    from _pytest.scope import _ScopeName
    from typing_extensions import Literal


@final
@dataclasses.dataclass(frozen=True)
class IdMaker:
    """Make IDs for a parametrization."""

    __slots__ = (
        "argnames",
        "parametersets",
        "idfn",
        "ids",
        "config",
        "nodeid",
        "func_name",
    )

    # The argnames of the parametrization.
    argnames: Sequence[str]
    # The ParameterSets of the parametrization.
    parametersets: Sequence[ParameterSet]
    # Optionally, a user-provided callable to make IDs for parameters in a
    # ParameterSet.
    idfn: Optional[Callable[[Any], Optional[object]]]
    # Optionally, explicit IDs for ParameterSets by index.
    ids: Optional[Sequence[Optional[object]]]
    # Optionally, the pytest config.
    # Used for controlling ASCII escaping, and for calling the
    # :hook:`pytest_make_parametrize_id` hook.
    config: Optional[Config]
    # Optionally, the ID of the node being parametrized.
    # Used only for clearer error messages.
    nodeid: Optional[str]
    # Optionally, the ID of the function being parametrized.
    # Used only for clearer error messages.
    func_name: Optional[str]

    def make_unique_parameterset_ids(self) -> List[str]:
        """Make a unique identifier for each ParameterSet, that may be used to
        identify the parametrization in a node ID.

        Format is <prm_1_token>-...-<prm_n_token>[counter], where prm_x_token is
        - user-provided id, if given
        - else an id derived from the value, applicable for certain types
        - else <argname><parameterset index>
        The counter suffix is appended only in case a string wouldn't be unique
        otherwise.
        """
        resolved_ids = list(self._resolve_ids())
        # All IDs must be unique!
        if len(resolved_ids) != len(set(resolved_ids)):
            # Record the number of occurrences of each ID.
            id_counts = Counter(resolved_ids)
            # Map the ID to its next suffix.
            id_suffixes: Dict[str, int] = defaultdict(int)
            # Suffix non-unique IDs to make them unique.
            for index, id in enumerate(resolved_ids):
                if id_counts[id] > 1:
                    resolved_ids[index] = f"{id}{id_suffixes[id]}"
                    id_suffixes[id] += 1
        return resolved_ids

    def _resolve_ids(self) -> Iterable[str]:
        """Resolve IDs for all ParameterSets (may contain duplicates)."""
        for idx, parameterset in enumerate(self.parametersets):
            if parameterset.id is not None:
                # ID provided directly - pytest.param(..., id="...")
                yield parameterset.id
            elif self.ids and idx < len(self.ids) and self.ids[idx] is not None:
                # ID provided in the IDs list - parametrize(..., ids=[...]).
                yield self._idval_from_value_required(self.ids[idx], idx)
            else:
                # ID not provided - generate it.
                yield "-".join(
                    self._idval(val, argname, idx)
                    for val, argname in zip(parameterset.values, self.argnames)
                )

    def _idval(self, val: object, argname: str, idx: int) -> str:
        """Make an ID for a parameter in a ParameterSet."""
        idval = self._idval_from_function(val, argname, idx)
        if idval is not None:
            return idval
        idval = self._idval_from_hook(val, argname)
        if idval is not None:
            return idval
        idval = self._idval_from_value(val)
        if idval is not None:
            return idval
        return self._idval_from_argname(argname, idx)

    def _idval_from_function(
        self, val: object, argname: str, idx: int
    ) -> Optional[str]:
        """Try to make an ID for a parameter in a ParameterSet using the
        user-provided id callable, if given."""
        if self.idfn is None:
            return None
        try:
            id = self.idfn(val)
        except Exception as e:
            prefix = f"{self.nodeid}: " if self.nodeid is not None else ""
            msg = "error raised while trying to determine id of parameter '{}' at position {}"
            msg = prefix + msg.format(argname, idx)
            raise ValueError(msg) from e
        if id is None:
            return None
        return self._idval_from_value(id)

    def _idval_from_hook(self, val: object, argname: str) -> Optional[str]:
        """Try to make an ID for a parameter in a ParameterSet by calling the
        :hook:`pytest_make_parametrize_id` hook."""
        if self.config:
            id: Optional[str] = self.config.hook.pytest_make_parametrize_id(
                config=self.config, val=val, argname=argname
            )
            return id
        return None

    def _idval_from_value(self, val: object) -> Optional[str]:
        """Try to make an ID for a parameter in a ParameterSet from its value,
        if the value type is supported."""
        if isinstance(val, STRING_TYPES):
            return _ascii_escaped_by_config(val, self.config)
        elif val is None or isinstance(val, (float, int, bool, complex)):
            return str(val)
        elif isinstance(val, Pattern):
            return ascii_escaped(val.pattern)
        elif val is NOTSET:
            # Fallback to default. Note that NOTSET is an enum.Enum.
            pass
        elif isinstance(val, enum.Enum):
            return str(val)
        elif isinstance(getattr(val, "__name__", None), str):
            # Name of a class, function, module, etc.
            name: str = getattr(val, "__name__")
            return name
        return None

    def _idval_from_value_required(self, val: object, idx: int) -> str:
        """Like _idval_from_value(), but fails if the type is not supported."""
        id = self._idval_from_value(val)
        if id is not None:
            return id

        # Fail.
        if self.func_name is not None:
            prefix = f"In {self.func_name}: "
        elif self.nodeid is not None:
            prefix = f"In {self.nodeid}: "
        else:
            prefix = ""
        msg = (
            f"{prefix}ids contains unsupported value {saferepr(val)} (type: {type(val)!r}) at index {idx}. "
            "Supported types are: str, bytes, int, float, complex, bool, enum, regex or anything with a __name__."
        )
        fail(msg, pytrace=False)

    @staticmethod
    def _idval_from_argname(argname: str, idx: int) -> str:
        """Make an ID for a parameter in a ParameterSet from the argument name
        and the index of the ParameterSet."""
        return str(argname) + str(idx)


def _ascii_escaped_by_config(val: Union[str, bytes], config: Optional[Config]) -> str:
    if config is None:
        escape_option = False
    else:
        escape_option = config.getini(
            "disable_test_id_escaping_and_forfeit_all_rights_to_community_support"
        )
    # TODO: If escaping is turned off and the user passes bytes,
    #       will return a bytes. For now we ignore this but the
    #       code *probably* doesn't handle this case.
    return val if escape_option else ascii_escaped(val)  # type: ignore


@final
@dataclasses.dataclass(frozen=True)
class CallSpec2:
    """A planned parameterized invocation of a test function.

    Calculated during collection for a given test function's Metafunc.
    Once collection is over, each callspec is turned into a single Item
    and stored in item.callspec.
    """

    # arg name -> arg value which will be passed to the parametrized test
    # function (direct parameterization).
    funcargs: Dict[str, object] = dataclasses.field(default_factory=dict)
    # arg name -> arg value which will be passed to a fixture of the same name
    # (indirect parametrization).
    params: Dict[str, object] = dataclasses.field(default_factory=dict)
    # arg name -> arg index.
    indices: Dict[str, int] = dataclasses.field(default_factory=dict)
    # Used for sorting parametrized resources.
    _arg2scope: Dict[str, Scope] = dataclasses.field(default_factory=dict)
    # Parts which will be added to the item's name in `[..]` separated by "-".
    _idlist: List[str] = dataclasses.field(default_factory=list)
    # Marks which will be applied to the item.
    marks: List[Mark] = dataclasses.field(default_factory=list)

    def setmulti(
        self,
        *,
        valtypes: Mapping[str, "Literal['params', 'funcargs']"],
        argnames: Iterable[str],
        valset: Iterable[object],
        id: str,
        marks: Iterable[Union[Mark, MarkDecorator]],
        scope: Scope,
        param_index: int,
    ) -> "CallSpec2":
        funcargs = self.funcargs.copy()
        params = self.params.copy()
        indices = self.indices.copy()
        arg2scope = self._arg2scope.copy()
        for arg, val in zip(argnames, valset):
            if arg in params or arg in funcargs:
                raise ValueError(f"duplicate {arg!r}")
            valtype_for_arg = valtypes[arg]
            if valtype_for_arg == "params":
                params[arg] = val
            elif valtype_for_arg == "funcargs":
                funcargs[arg] = val
            else:
                assert_never(valtype_for_arg)
            indices[arg] = param_index
            arg2scope[arg] = scope
        return CallSpec2(
            funcargs=funcargs,
            params=params,
            indices=indices,
            _arg2scope=arg2scope,
            _idlist=[*self._idlist, id],
            marks=[*self.marks, *normalize_mark_list(marks)],
        )

    def getparam(self, name: str) -> object:
        try:
            return self.params[name]
        except KeyError as e:
            raise ValueError(name) from e

    @property
    def id(self) -> str:
        return "-".join(self._idlist)


@final
class Metafunc:
    """Objects passed to the :hook:`pytest_generate_tests` hook.

    They help to inspect a test function and to generate tests according to
    test configuration or values specified in the class or module where a
    test function is defined.
    """

    fixturenames: List[str]
    _arg2fixturedefs: Dict[str, Sequence["fixtures.FixtureDef[Any]"]]

    def __init__(
        self,
        definition: nodes.FunctionDefinition,
        fixtureinfo: fixtures.FuncFixtureInfo,
        config: Config,
        cls=None,
        module=None,
        *,
        _ispytest: bool = False,
    ) -> None:
        check_ispytest(_ispytest)

        #: Access to the underlying :class:`_pytest.python.FunctionDefinition`.
        self.definition = definition

        #: Access to the :class:`pytest.Config` object for the test session.
        self.config = config

        #: The module object where the test function is defined in.
        self.module = module

        #: Underlying Python test function.
        self.function = definition.obj

        #: Set of fixture names required by the test function.
        self.fixturenames = fixtureinfo.names_closure

        #: Class object where the test function is defined in or ``None``.
        self.cls = cls

        self._arg2fixturedefs = fixtureinfo.name2fixturedefs

        # Result of parametrize().
        self._calls: List[CallSpec2] = []

    def parametrize(
        self,
        argnames: Union[str, Sequence[str]],
        argvalues: Iterable[Union[ParameterSet, Sequence[object], object]],
        indirect: Union[bool, Sequence[str]] = False,
        ids: Optional[
            Union[Iterable[Optional[object]], Callable[[Any], Optional[object]]]
        ] = None,
        scope: "Optional[_ScopeName]" = None,
        *,
        _param_mark: Optional[Mark] = None,
    ) -> None:
        """Add new invocations to the underlying test function using the list
        of argvalues for the given argnames. Parametrization is performed
        during the collection phase. If you need to setup expensive resources
        see about setting indirect to do it rather than at test setup time.

        Can be called multiple times, in which case each call parametrizes all
        previous parametrizations, e.g.

        ::

            unparametrized:         t
            parametrize ["x", "y"]: t[x], t[y]
            parametrize [1, 2]:     t[x-1], t[x-2], t[y-1], t[y-2]

        :param argnames:
            A comma-separated string denoting one or more argument names, or
            a list/tuple of argument strings.

        :param argvalues:
            The list of argvalues determines how often a test is invoked with
            different argument values.

            If only one argname was specified argvalues is a list of values.
            If N argnames were specified, argvalues must be a list of
            N-tuples, where each tuple-element specifies a value for its
            respective argname.

        :param indirect:
            A list of arguments' names (subset of argnames) or a boolean.
            If True the list contains all names from the argnames. Each
            argvalue corresponding to an argname in this list will
            be passed as request.param to its respective argname fixture
            function so that it can perform more expensive setups during the
            setup phase of a test rather than at collection time.

        :param ids:
            Sequence of (or generator for) ids for ``argvalues``,
            or a callable to return part of the id for each argvalue.

            With sequences (and generators like ``itertools.count()``) the
            returned ids should be of type ``string``, ``int``, ``float``,
            ``bool``, or ``None``.
            They are mapped to the corresponding index in ``argvalues``.
            ``None`` means to use the auto-generated id.

            If it is a callable it will be called for each entry in
            ``argvalues``, and the return value is used as part of the
            auto-generated id for the whole set (where parts are joined with
            dashes ("-")).
            This is useful to provide more specific ids for certain items, e.g.
            dates.  Returning ``None`` will use an auto-generated id.

            If no ids are provided they will be generated automatically from
            the argvalues.

        :param scope:
            If specified it denotes the scope of the parameters.
            The scope is used for grouping tests by parameter instances.
            It will also override any fixture-function defined scope, allowing
            to set a dynamic scope using test context or configuration.
        """
        argnames, parametersets = ParameterSet._for_parametrize(
            argnames,
            argvalues,
            self.function,
            self.config,
            nodeid=self.definition.nodeid,
        )
        del argvalues

        if "request" in argnames:
            fail(
                "'request' is a reserved name and cannot be used in @pytest.mark.parametrize",
                pytrace=False,
            )

        if scope is not None:
            scope_ = Scope.from_user(
                scope, descr=f"parametrize() call in {self.function.__name__}"
            )
        else:
            scope_ = _find_parametrized_scope(argnames, self._arg2fixturedefs, indirect)

        self._validate_if_using_arg_names(argnames, indirect)

        arg_values_types = self._resolve_arg_value_types(argnames, indirect)

        # Use any already (possibly) generated ids with parametrize Marks.
        if _param_mark and _param_mark._param_ids_from:
            generated_ids = _param_mark._param_ids_from._param_ids_generated
            if generated_ids is not None:
                ids = generated_ids

        ids = self._resolve_parameter_set_ids(
            argnames, ids, parametersets, nodeid=self.definition.nodeid
        )

        # Store used (possibly generated) ids with parametrize Marks.
        if _param_mark and _param_mark._param_ids_from and generated_ids is None:
            object.__setattr__(_param_mark._param_ids_from, "_param_ids_generated", ids)

        # Create the new calls: if we are parametrize() multiple times (by applying the decorator
        # more than once) then we accumulate those calls generating the cartesian product
        # of all calls.
        newcalls = []
        for callspec in self._calls or [CallSpec2()]:
            for param_index, (param_id, param_set) in enumerate(
                zip(ids, parametersets)
            ):
                newcallspec = callspec.setmulti(
                    valtypes=arg_values_types,
                    argnames=argnames,
                    valset=param_set.values,
                    id=param_id,
                    marks=param_set.marks,
                    scope=scope_,
                    param_index=param_index,
                )
                newcalls.append(newcallspec)
        self._calls = newcalls

    def _resolve_parameter_set_ids(
        self,
        argnames: Sequence[str],
        ids: Optional[
            Union[Iterable[Optional[object]], Callable[[Any], Optional[object]]]
        ],
        parametersets: Sequence[ParameterSet],
        nodeid: str,
    ) -> List[str]:
        """Resolve the actual ids for the given parameter sets.

        :param argnames:
            Argument names passed to ``parametrize()``.
        :param ids:
            The `ids` parameter of the ``parametrize()`` call (see docs).
        :param parametersets:
            The parameter sets, each containing a set of values corresponding
            to ``argnames``.
        :param nodeid str:
            The nodeid of the definition item that generated this
            parametrization.
        :returns:
            List with ids for each parameter set given.
        """
        if ids is None:
            idfn = None
            ids_ = None
        elif callable(ids):
            idfn = ids
            ids_ = None
        else:
            idfn = None
            ids_ = self._validate_ids(ids, parametersets, self.function.__name__)
        id_maker = IdMaker(
            argnames,
            parametersets,
            idfn,
            ids_,
            self.config,
            nodeid=nodeid,
            func_name=self.function.__name__,
        )
        return id_maker.make_unique_parameterset_ids()

    def _validate_ids(
        self,
        ids: Iterable[Optional[object]],
        parametersets: Sequence[ParameterSet],
        func_name: str,
    ) -> List[Optional[object]]:
        try:
            num_ids = len(ids)  # type: ignore[arg-type]
        except TypeError:
            try:
                iter(ids)
            except TypeError as e:
                raise TypeError("ids must be a callable or an iterable") from e
            num_ids = len(parametersets)

        # num_ids == 0 is a special case: https://github.com/pytest-dev/pytest/issues/1849
        if num_ids != len(parametersets) and num_ids != 0:
            msg = "In {}: {} parameter sets specified, with different number of ids: {}"
            fail(msg.format(func_name, len(parametersets), num_ids), pytrace=False)

        return list(itertools.islice(ids, num_ids))

    def _resolve_arg_value_types(
        self,
        argnames: Sequence[str],
        indirect: Union[bool, Sequence[str]],
    ) -> Dict[str, "Literal['params', 'funcargs']"]:
        """Resolve if each parametrized argument must be considered a
        parameter to a fixture or a "funcarg" to the function, based on the
        ``indirect`` parameter of the parametrized() call.

        :param List[str] argnames: List of argument names passed to ``parametrize()``.
        :param indirect: Same as the ``indirect`` parameter of ``parametrize()``.
        :rtype: Dict[str, str]
            A dict mapping each arg name to either:
            * "params" if the argname should be the parameter of a fixture of the same name.
            * "funcargs" if the argname should be a parameter to the parametrized test function.
        """
        if isinstance(indirect, bool):
            valtypes: Dict[str, Literal["params", "funcargs"]] = dict.fromkeys(
                argnames, "params" if indirect else "funcargs"
            )
        elif isinstance(indirect, Sequence):
            valtypes = dict.fromkeys(argnames, "funcargs")
            for arg in indirect:
                if arg not in argnames:
                    fail(
                        "In {}: indirect fixture '{}' doesn't exist".format(
                            self.function.__name__, arg
                        ),
                        pytrace=False,
                    )
                valtypes[arg] = "params"
        else:
            fail(
                "In {func}: expected Sequence or boolean for indirect, got {type}".format(
                    type=type(indirect).__name__, func=self.function.__name__
                ),
                pytrace=False,
            )
        return valtypes

    def _validate_if_using_arg_names(
        self,
        argnames: Sequence[str],
        indirect: Union[bool, Sequence[str]],
    ) -> None:
        """Check if all argnames are being used, by default values, or directly/indirectly.

        :param List[str] argnames: List of argument names passed to ``parametrize()``.
        :param indirect: Same as the ``indirect`` parameter of ``parametrize()``.
        :raises ValueError: If validation fails.
        """
        default_arg_names = set(get_default_arg_names(self.function))
        func_name = self.function.__name__
        for arg in argnames:
            if arg not in self.fixturenames:
                if arg in default_arg_names:
                    fail(
                        "In {}: function already takes an argument '{}' with a default value".format(
                            func_name, arg
                        ),
                        pytrace=False,
                    )
                else:
                    if isinstance(indirect, Sequence):
                        name = "fixture" if arg in indirect else "argument"
                    else:
                        name = "fixture" if indirect else "argument"
                    fail(
                        f"In {func_name}: function uses no {name} '{arg}'",
                        pytrace=False,
                    )


def _find_parametrized_scope(
    argnames: Sequence[str],
    arg2fixturedefs: Mapping[str, Sequence[fixtures.FixtureDef[object]]],
    indirect: Union[bool, Sequence[str]],
) -> Scope:
    """Find the most appropriate scope for a parametrized call based on its arguments.

    When there's at least one direct argument, always use "function" scope.

    When a test function is parametrized and all its arguments are indirect
    (e.g. fixtures), return the most narrow scope based on the fixtures used.

    Related to issue #1832, based on code posted by @Kingdread.
    """
    if isinstance(indirect, Sequence):
        all_arguments_are_fixtures = len(indirect) == len(argnames)
    else:
        all_arguments_are_fixtures = bool(indirect)

    if all_arguments_are_fixtures:
        fixturedefs = arg2fixturedefs or {}
        used_scopes = [
            fixturedef[0]._scope
            for name, fixturedef in fixturedefs.items()
            if name in argnames
        ]
        # Takes the most narrow scope from used fixtures.
        return min(used_scopes, default=Scope.Function)

    return Scope.Function
