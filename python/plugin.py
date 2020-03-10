"""Behave VIM integration plugin.

Parser for behave feature test layout.

Parse feature files (features/*.feature) and feature step implementations
(features/steps/*.py).

Feature testing layout

  ./                      # -- Project root
  +-- behave.ini          # -- Config file (or .behaverc or setup.cfg or tox.ini)
  +-- features/           # -- Features directory
      +-- *.feature       # -- Feature files
      +-- environment.py  # -- Environment file
      +-- steps/          # -- Steps directory
      |    +-- *.py       # -- Step implementations
"""

# TODO Implement method to use custom data types in step parameters
# https://behave.readthedocs.io/en/latest/api.html#behave.register_type

import ast
import os
import pathlib
from collections import namedtuple

import parse
from behave import parser as behave_parser
from behave.configuration import read_configuration
from behave.i18n import languages

#: Step location tuple definition
StepLocation = namedtuple(
    "StepLocation", ["step_type", "description", "name", "filepath", "lineno"]
)
#: Invalid gherkin syntax location tuple definition
InvalidGherkinSyntaxLocation = namedtuple(
    "InvalidFeatureSyntaxLocation", ["filepath", "lineno"],
)


try:
    import vim
except ModuleNotFoundError:  # pragma: no cover
    print("No vim module available outside vim")

MAX_LOCATION_LIST_HEIGHT = 5


def behave_jump():
    """Jump to matching location in step definitions or implementation.

    Backend for BehaveJump command.
    """
    # check supported file types
    filetype = vim.current.buffer.options["filetype"] or "<UNKNOWN>"
    if isinstance(filetype, bytes):
        filetype = filetype.decode()  # vim
    assert isinstance(filetype, str)
    if filetype not in ("cucumber", "python"):
        print('File type "{}" is not supported by BehaveJump'.format(filetype))
        return

    try:
        locations = get_step_references(
            vim.current.buffer, vim.current.window.cursor[0]
        )
    except (FileNotFoundError, LookupError) as err:
        print(err)
        return
    except behave_parser.ParserError as err:
        assert len(err.args) == 1
        print(str(err).split("\n")[1])
        return
    assert locations

    if len(locations) == 1:
        vim.command(
            "{command} +{line_no} {filename}".format(
                command="split" if vim.current.buffer.options["modified"] else "edit",
                line_no=locations[0].lineno,
                filename=locations[0].filepath,
            )
        )
        return

    list_window_size = min(len(locations), MAX_LOCATION_LIST_HEIGHT)
    locations_list = []
    for location in locations:
        locations_list.append(
            {
                "filename": location.filepath,
                "lnum": location.lineno,
                "text": location.description,
            }
        )
    try:
        vim.Function("setloclist")(0, locations_list)  # vim
    except AttributeError:
        vim.funcs.setloclist(0, locations_list)  # neovim
    vim.command("lopen {}".format(list_window_size))


def behave_errors():
    """Find known issues in step implementation files."""
    try:
        features_root_dir = str(find_features_path(vim.current.buffer.name))
    except FileNotFoundError as err:
        print(err)
        return

    errors = []
    for generator_fn in get_step_defs_from_dir, get_step_impl_from_dir:
        try:
            for step in generator_fn(features_root_dir):
                try:
                    parse.parse(step.name, "")
                except ValueError as err:
                    errors.append([step, str(err)])
        except behave_parser.ParserError as err:
            err_lines = str(err).strip().split("\n")
            assert err_lines[0].strip() == 'Failed to parse "{}":'.format(err.filename)
            assert len(err_lines) == 2, err_lines
            errors.append(
                [
                    InvalidGherkinSyntaxLocation(
                        filepath=err.filename, lineno=err.line
                    ),
                    err_lines[1],
                ]
            )

    list_window_size = min(len(errors), MAX_LOCATION_LIST_HEIGHT)
    if list_window_size:
        quickfix_list = []
        for step, err in errors:
            quickfix_list.append(
                {
                    "filename": os.path.relpath(step.filepath),
                    "lnum": step.lineno,
                    "text": err,
                }
            )
        try:
            vim.Function("setqflist")(quickfix_list)  # vim
        except AttributeError:
            vim.funcs.setqflist(quickfix_list)  # neovim
        vim.command("copen {}".format(list_window_size))
    else:
        print("No Behave errors found")


def find_features_path(src_path):
    """Find path to the features directory in feature testing layout.

    :param src_path: Path to feature or python file
    :type src_path: str

    :return: Relative pathlib.Path object to features directory
    """
    path = pathlib.Path(os.path.expanduser(src_path))
    if not path.is_dir():
        path = path.parent
    while not (path / "features").is_dir():
        if path.is_absolute():
            path /= ".."
            if path.root == os.path.abspath(path):
                raise FileNotFoundError("Behave test layout not found")
        else:
            path = pathlib.Path(os.path.normpath(path / ".."))
            if path.resolve().root == str(path.resolve()):
                raise FileNotFoundError("Behave test layout not found")
    return pathlib.Path(os.path.relpath(path / "features"))


def find_features_language(path):
    """Find feature file language parameter from behave config files.

    :param path: Path to features directory
    :type path: pathlib.Path

    :return: Value of "lang" parameter in config file section [behave]
    :rtype: str or None
    """
    config = {}
    for filename in ["tox.ini", "setup.cfg", ".behaverc", "behave.ini"]:
        filepath = path / ".." / filename
        if filepath.is_file():
            config.update(read_configuration(os.path.normpath(filepath)))
    return config.get("lang")


def get_step_defs_from_dir(path):
    """Get step definition locations from specified directory (generator).

    :param path: Path to directory
    :type path: str

    :return: StepLocation
    """
    assert os.path.isdir(path)
    language = find_features_language(find_features_path(path))
    for dirpath, *_, filenames in os.walk(path):
        for filename in filenames:
            if filename.endswith(".feature"):
                filepath = os.path.join(dirpath, filename)
                with open(filepath, "rb") as src_file:
                    for _ in scan_step_def(src_file.readlines(), filepath, language):
                        yield _


def scan_step_def(buf, filepath=None, language=None):
    """Scan step definition locations from buffer (generator).

    :param buf: Buffer with file content lines
    :type buf: list
    :param filepath: Path to feature file
    :type filepath: str
    :param language: Feature file language
    :type language: str

    :return: StepLocation
    """
    filepath = filepath or buf.name
    language = language or find_features_language(find_features_path(filepath))
    feature = behave_parser.parse_file(filepath, language=language)
    translations_for_and = languages.get(language or "en", {}).get("and", [])

    for scenario in [feature.background] + feature.scenarios:
        if scenario:
            last_keyword = None
            for step in scenario.steps:
                name = step.name
                if scenario.type == "scenario_outline":
                    # replace <param> in step names with the value from the first
                    # row of examples
                    for param in scenario.examples[0].table.headings:
                        name = name.replace(
                            "<{}>".format(param), scenario.examples[0].table[0][param],
                        )
                if step.keyword not in translations_for_and:
                    last_keyword = step.keyword
                yield StepLocation(
                    step_type=step.step_type,
                    description=last_keyword + " " + step.name,
                    name=name,
                    filepath=feature.filename,
                    lineno=step.line,
                )


def get_step_impl_from_dir(path):
    """Get step implementation locations from directory (generator).

    Scan "steps/" subdirectory in specified path and yield location data for
    every step implementations found.

    :param path: Path to directory
    :type path: str

    :return: StepLocation
    """
    path = os.path.join(path, "steps")
    for dirpath, *_, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if filename.endswith(".py") and os.path.isfile(filepath):
                with open(filepath, "rb") as src_file:
                    for _ in scan_step_impl(src_file.readlines(), filepath):
                        yield _


def scan_step_impl(buf, filepath=None):
    """Get step implementation locations from buffer (generator).

    :param buf: Buffer with file content lines
    :type buf: list
    :param filepath: Path to python file
    :type filepath: str

    :return: StepLocation
    """
    filepath = filepath or buf.name
    filename2 = os.path.relpath(filepath, os.getcwd())
    try:
        content = "\n".join(buf)
    except TypeError:
        content = b"".join(buf)
    code = compile(content, filename2, "exec", ast.PyCF_ONLY_AST)
    for obj in code.body:
        if obj.__class__.__name__ == "FunctionDef":
            for deco in obj.decorator_list:
                if deco.func.id in ("given", "when", "then", "step") and deco.args:
                    yield StepLocation(
                        step_type=deco.func.id,
                        description="@{}({})".format(
                            deco.func.id, repr(deco.args[0].s)
                        ),
                        name=deco.args[0].s,
                        filepath=filepath,
                        lineno=deco.lineno,
                    )


def get_step_location(vim_buf, lineno):
    """Find location for step definition/implementation.

    Find first line number for step specified by current VIM buffer.

    :return: StepLocation or None

    :raises LookupError: Step not found in specified location
    """
    filepath = vim_buf.name
    find_features_path(filepath)
    location = None
    if filepath.endswith(".py"):
        for step in scan_step_impl(vim_buf, filepath):
            if lineno < step.lineno:
                break
            location = step
        if location is None:
            raise LookupError("Not a step implementation")
    elif filepath.endswith(".feature"):
        for step in scan_step_def(vim_buf, filepath):
            if lineno < step.lineno:
                break
            location = step
        if location is None:
            raise LookupError("Not a step definition")
    else:
        raise LookupError("Unexpected file type")

    return location


def get_step_references(vim_buf, lineno):
    """Find locations that matches step in cursor location.

    :return: list of StepLocation objects

    :raises LookupError: Step not found in specified location
    :raises LookupError: Step implementation not found
    :raises LookupError: Unused step implementation
    """
    filepath = vim_buf.name
    step = get_step_location(vim_buf, lineno)
    features_root_dir = str(find_features_path(filepath))
    locations = []
    errors = []
    if filepath.endswith(".py"):
        for step_def in get_step_defs_from_dir(features_root_dir):
            if step.step_type in ["step", step_def.step_type]:
                try:
                    if parse.parse(step.name, step_def.name):
                        locations.append(step_def)
                except ValueError as err:
                    raise LookupError("Error while parsing step: {}".format(err))
    else:
        assert filepath.endswith(".feature")
        for step_impl in get_step_impl_from_dir(features_root_dir):
            if step_impl.step_type in ["step", step.step_type]:
                try:
                    if parse.parse(step_impl.name, step.name):
                        locations.append(step_impl)
                except ValueError as err:
                    errors.append(err)

    if locations:
        return locations
    raise LookupError(generate_missing_location_msg(filepath, errors))


def generate_missing_location_msg(filepath, errors):
    """Generate error message for missing location LookupError."""
    msg = (
        "Unused step implementation"
        if filepath.endswith(".py")
        else "Step implementation not found"
    )

    if len(errors) == 1:
        msg += ". Also registered an error: {}".format(str(errors[0]))
    elif errors:
        msg += ". Also registered {} errors, " "use :BehaveErrors to see them.".format(
            len(errors)
        )

    return msg
