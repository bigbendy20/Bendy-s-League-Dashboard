"""
Load `.env` for the command-line scripts.

`app.py` has called `load_dotenv()` since the first week, so the Streamlit
side has always seen the key. The CLI scripts — `seed_profiles.py` and
`refresh_job.py` — did not, because they were written for GitHub Actions,
where the key arrives as a real environment variable and there is no `.env`
at all. On the machine where the backfill actually runs, that combination
means `RIOT_API_KEY is not set` while the key sits in `.env` two lines away.

Three decisions worth stating, because each prevents a specific failure:

**Real environment variables win.** `.env` fills gaps, it never overwrites.
Actions secrets and a hand-set `set DATABASE_URL=…` must both take
precedence over a stale file, or overriding config from the shell would
silently do nothing.

**The path is anchored to this file, not the working directory.**
`load_dotenv()` searches upward from the cwd, which works when a `.bat` has
already `cd`-ed into the folder and fails when someone runs
`python lol-dashboard/refresh_job.py` from the parent. The key doesn't move,
so neither should the lookup.

**No dependency on python-dotenv.** The first version of this module called
`dotenv_values` when the library was importable, reasoning that `app.py` used
it and two parsers could disagree. But moving `app.py` onto this loader
removed the second parser — there is nothing left to agree with. The
dependency then only added failure modes, and one showed up immediately: the
startup tests stub `dotenv` in `sys.modules`, and the stub returned an object
whose `.items()` yielded nothing, so the loader read the file and applied
none of it, silently. Checked against the real `.env` before the library was
dropped: same keys, same values.
"""
import os


def parse(text) -> dict:
    """`KEY=value` lines -> a dict. Comments and blanks ignored.

    A deliberately small subset of the format: enough for a file of keys and
    regions, and not pretending to be a shell.
    """
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
        if quoted:
            value = value[1:-1]
        elif " #" in value:
            # Trailing comment on an unquoted value. Only with preceding
            # whitespace: `#` is legal inside a Riot ID tag line.
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def path_for(module_file) -> str:
    """Where `.env` lives: beside the code, whatever the cwd is."""
    return os.path.join(os.path.dirname(os.path.abspath(module_file)), ".env")


def load(path=None, environ=None) -> list:
    """Fill missing environment variables from `.env`.

    Returns the names actually applied — nothing for a missing file, and
    nothing for a key the environment already defines. Returning the list
    rather than a bool is what lets a caller say *which* settings it picked
    up, and lets the tests tell "loaded and skipped" from "never read".
    """
    if path is None:
        path = path_for(__file__)
    if environ is None:
        environ = os.environ
    if not os.path.exists(path):
        return []

    with open(path, encoding="utf-8") as handle:
        values = parse(handle.read())

    applied = []
    for key, value in values.items():
        if environ.get(key):
            continue
        environ[key] = value
        applied.append(key)
    return applied
