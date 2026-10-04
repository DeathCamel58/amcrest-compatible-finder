"""Check cameras.json, the firmware_compatible.json index, and the detail and layout files it points to (data/)
against their JSON Schemas (docs/schema/) and the rules that span the files.

    python -m util.validate_output [cameras.json] [firmware_compatible.json]

Detail and layout paths are relative to the directory firmware_compatible.json is in.

Prints a summary of problems by category and exits non-zero if there are any."""
import json
import os
import re
import sys
from collections import Counter
from typing import NamedTuple
from urllib.parse import unquote

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'schema')
CAMERAS_SCHEMA = os.path.join(SCHEMA_DIR, 'cameras.schema.json')
COMPAT_SCHEMA = os.path.join(SCHEMA_DIR, 'firmware_compatible.schema.json')
DETAIL_SCHEMA = os.path.join(SCHEMA_DIR, 'firmware_detail.schema.json')
LAYOUT_SCHEMA = os.path.join(SCHEMA_DIR, 'partition_layout.schema.json')


class Problem(NamedTuple):
    file: str      # which output file ("cameras.json", "firmware_compatible.json", or "both")
    key: str       # firmware file name, or "" for file-level problems
    category: str  # short, groupable description, e.g. "schema: listings[].kind: required"
    message: str

    def __str__(self):
        where = f'{self.file}: {self.key}' if self.key else self.file
        return f'{where}: [{self.category}] {self.message}'


def load_json(path, label, problems):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as err:
        problems.append(Problem(label, '', 'unreadable', f'{path}: {err}'))
        return None


def schema_category(error):
    """A category that groups the same problem across entries: the path inside an entry (indexes as []) plus the
    failed keyword, with the property name for required / forbidden properties."""
    path = list(error.absolute_path)[1:]
    field = ''.join(f'[]' if isinstance(part, int) else f'.{part}' for part in path).lstrip('.') or '(entry)'
    keyword = error.validator
    if keyword in ('required', 'dependentRequired'):
        # "'kind' is a required property" / "'listing_only_reason' is a dependency of 'downloadable'"
        missing = re.search(r"'([^']*)'", error.message)
        missing = missing[1] if missing else '?'
        field = f'{field}.{missing}' if path else missing
        return f'schema: {field}: missing'
    if keyword == 'not' and error.validator_value == {}:
        return f'schema: {field}: not allowed here'
    return f'schema: {field}: {keyword}'


def make_validator(schema_path, jsonschema):
    with open(schema_path) as f:
        schema = json.load(f)
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def schema_problems(data, schema_path, label, jsonschema, validator=None):
    validator = validator or make_validator(schema_path, jsonschema)

    errors = list(validator.iter_errors(data))
    # Dates and timestamps have both a pattern and a format; one problem is enough
    pattern_paths = {tuple(error.absolute_path) for error in errors if error.validator == 'pattern'}
    problems = []
    for error in errors:
        path = list(error.absolute_path)
        if error.validator == 'format' and tuple(path) in pattern_paths:
            continue
        key = str(path[0]) if path else ''
        if error.validator == 'not' and error.validator_value == {}:
            # Properties that must be absent in this context are written as {"not": {}}
            message = f'{path[-1]!r} is not allowed here (value {error.instance!r})'
        else:
            message = error.message
        message = message if len(message) <= 300 else message[:300] + '...'
        problems.append(Problem(label, key, schema_category(error), message))
    return problems


def unquote_collisions(data, label):
    """Keys that only differ in %-escaping name the same file (file names are stored decoded)."""
    by_name = {}
    for key in data:
        by_name.setdefault(unquote(key), []).append(key)
    return [Problem(label, key, 'cross: keys equal after unquote', f'same name as {[k for k in keys if k != key]}')
            for keys in by_name.values() if len(keys) > 1 for key in keys]


def cross_file_problems(cameras, compat):
    problems = []
    cameras_ok = isinstance(cameras, dict)
    compat_ok = isinstance(compat, dict)

    if cameras_ok:
        problems += unquote_collisions(cameras, 'cameras.json')
        for key, entry in cameras.items():
            if not isinstance(entry, dict):
                continue
            main = entry.get('duplicate_of')
            if isinstance(main, str):
                main_entry = cameras.get(main)
                if not isinstance(main_entry, dict):
                    problems.append(Problem('cameras.json', key, 'cross: duplicate_of target missing',
                                            f'duplicate_of {main!r} is not in cameras.json'))
                else:
                    if main_entry.get('duplicate_of'):
                        problems.append(Problem('cameras.json', key, 'cross: duplicate_of target is a duplicate',
                                                f'{main!r} is itself a duplicate of {main_entry["duplicate_of"]!r}'))
                    if key not in (main_entry.get('aliases') or []):
                        problems.append(Problem('cameras.json', key, 'cross: duplicate not in main aliases',
                                                f'{main!r} does not list this file in aliases'))
            for alias in entry.get('aliases') or []:
                alias_entry = cameras.get(alias)
                if not isinstance(alias_entry, dict):
                    problems.append(Problem('cameras.json', key, 'cross: alias missing',
                                            f'alias {alias!r} is not in cameras.json'))
                elif alias_entry.get('duplicate_of') != key:
                    problems.append(Problem('cameras.json', key, 'cross: alias does not point back',
                                            f'alias {alias!r} has duplicate_of {alias_entry.get("duplicate_of")!r}'))

    if compat_ok:
        problems += unquote_collisions(compat, 'firmware_compatible.json')
        for key, result in compat.items():
            if cameras_ok and key not in cameras:
                problems.append(Problem('firmware_compatible.json', key, 'cross: key not in cameras.json',
                                        'has a result but no cameras.json entry'))
            if not isinstance(result, dict):
                continue
            main = result.get('duplicate_of')
            if not isinstance(main, str):
                continue
            if cameras_ok and main not in cameras:
                problems.append(Problem('firmware_compatible.json', key, 'cross: duplicate_of target missing',
                                        f'duplicate_of {main!r} is not in cameras.json'))
            main_result = compat.get(main)
            if not isinstance(main_result, dict):
                problems.append(Problem('firmware_compatible.json', key, 'cross: duplicate_of target missing',
                                        f'duplicate_of {main!r} has no result'))
            elif main_result.get('status') == 'duplicate' or main_result.get('duplicate_of'):
                problems.append(Problem('firmware_compatible.json', key, 'cross: duplicate_of target is a duplicate',
                                        f'{main!r} is itself a duplicate of {main_result.get("duplicate_of")!r}'))
            if cameras_ok and isinstance(cameras.get(key), dict) and cameras[key].get('duplicate_of') != main:
                problems.append(Problem('both', key, 'cross: duplicate_of differs between files',
                                        f'firmware_compatible.json says {main!r}, cameras.json says '
                                        f'{cameras[key].get("duplicate_of")!r}'))

    return problems


def data_problems(cameras, compat, root, jsonschema):
    """The detail and layout files: each index entry's detail exists, is valid and belongs to that file's content;
    each package's layout exists and matches its images; and nothing is stored that the index doesn't use."""
    from util.analysis_store import DETAIL_DIR, LAYOUT_DIR, layout_path
    problems = []
    if not isinstance(compat, dict):
        return problems
    detail_validator = make_validator(DETAIL_SCHEMA, jsonschema) if jsonschema else None
    layout_validator = make_validator(LAYOUT_SCHEMA, jsonschema) if jsonschema else None
    cameras = cameras if isinstance(cameras, dict) else {}
    checked_details, used_layouts, checked_layouts = {}, set(), {}

    def read(path, label, key):
        try:
            with open(os.path.join(root, path)) as f:
                return json.load(f)
        except FileNotFoundError:
            problems.append(Problem(label, key, 'data: missing file', f'{path} does not exist'))
        except (OSError, ValueError) as err:
            problems.append(Problem(label, key, 'data: unreadable', f'{path}: {err}'))
        return None

    for key, entry in compat.items():
        path = entry.get('detail') if isinstance(entry, dict) else None
        if not path:
            continue
        if path not in checked_details:
            detail = read(path, 'firmware_compatible.json', key)
            checked_details[path] = detail
            if detail is None:
                continue
            if detail_validator:
                problems += [p._replace(file=path, key=key) for p in
                             schema_problems(detail, DETAIL_SCHEMA, path, jsonschema, detail_validator)]
            sha = detail.get('sha256') or ''
            if not path.endswith(f'/{sha[:16]}.json'):
                problems.append(Problem(path, key, 'data: name does not match sha256', f'sha256 is {sha}'))
            for package in detail.get('packages') or []:
                layout = package.get('partition_layout')
                if not layout:
                    continue
                used_layouts.add(layout_path(layout))
                if layout not in checked_layouts:
                    rows = read(layout_path(layout), path, key)
                    checked_layouts[layout] = rows
                    if rows is not None and layout_validator:
                        problems += [p._replace(file=layout_path(layout), key=key) for p in
                                     schema_problems(rows, LAYOUT_SCHEMA, layout_path(layout), jsonschema,
                                                     layout_validator)]
                rows = checked_layouts.get(layout)
                if rows and len(rows.get('partitions') or []) != len(package.get('partition_images') or []):
                    problems.append(Problem(path, key, 'data: images do not match layout rows',
                                            f'{len(package.get("partition_images") or [])} images for '
                                            f'{len(rows.get("partitions") or [])} rows of layout {layout}'))
        detail = checked_details.get(path)
        file_sha = ((cameras.get(key) or {}).get('file_hashes') or {}).get('sha256')
        if detail and file_sha and detail.get('sha256') != file_sha:
            problems.append(Problem('both', key, 'data: detail is for different content',
                                    f'cameras.json has sha256 {file_sha}, {path} is for {detail.get("sha256")}'))

    for directory, used in ((DETAIL_DIR, set(checked_details)), (LAYOUT_DIR, used_layouts)):
        for current, _, names in os.walk(os.path.join(root, directory)):
            for name in names:
                relative = os.path.relpath(os.path.join(current, name), root).replace(os.sep, '/')
                if name.endswith('.json') and relative not in used:
                    problems.append(Problem(relative, '', 'data: file nothing refers to',
                                            'not used by any firmware_compatible.json entry'))
    return problems


def validate(cameras_path='cameras.json', compat_path='firmware_compatible.json'):
    """Returns a list of Problem tuples (empty when both files are valid)."""
    problems = []
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
        problems.append(Problem('both', '', 'jsonschema missing',
                                'the jsonschema package is not installed (pip install jsonschema); '
                                'only the cross-file rules were checked'))

    cameras = load_json(cameras_path, 'cameras.json', problems)
    compat = load_json(compat_path, 'firmware_compatible.json', problems)

    if jsonschema is not None:
        if cameras is not None:
            problems += schema_problems(cameras, CAMERAS_SCHEMA, 'cameras.json', jsonschema)
        if compat is not None:
            problems += schema_problems(compat, COMPAT_SCHEMA, 'firmware_compatible.json', jsonschema)

    problems += cross_file_problems(cameras, compat)
    problems += data_problems(cameras, compat, os.path.dirname(os.path.abspath(compat_path)), jsonschema)
    return problems


def main(argv):
    if len(argv) > 2 or any(arg in ('-h', '--help') for arg in argv):
        print(__doc__)
        return 2
    cameras_path = argv[0] if argv else 'cameras.json'
    compat_path = argv[1] if len(argv) > 1 else 'firmware_compatible.json'
    problems = validate(cameras_path, compat_path)

    if not problems:
        print(f'OK: {cameras_path} and {compat_path} are valid')
        return 0

    # A few examples per category, then the counts
    by_category = {}
    for problem in problems:
        by_category.setdefault((problem.file, problem.category), []).append(problem)
    for (file, category), items in sorted(by_category.items()):
        print(f'{file}: {category}: {len(items)}')
        for problem in items[:3]:
            print(f'    {problem.key or "(file)"}: {problem.message}')
        if len(items) > 3:
            print(f'    ... and {len(items) - 3} more')

    files = Counter(problem.file for problem in problems)
    print(f'\n{len(problems)} problems (' + ', '.join(f'{count} in {file}' for file, count in sorted(files.items()))
          + f') in {len(by_category)} categories')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
