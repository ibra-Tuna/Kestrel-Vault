# Contributing to Kestrel Vault

Thanks for considering a contribution. Kestrel Vault is a small, single-maintainer
project, so a few small conventions go a long way toward getting your change merged
quickly.

By participating in this project you agree to be respectful and constructive. There
is no formal Code of Conduct — the short version is: be kind, assume good faith,
and keep discussion focused on the code.

---

## Ways to contribute

You don't have to write code to help:

- **Report bugs** — use the [issue tracker](https://github.com/ibra-tuna/Kestrel-Vault/issues).
- **Suggest features** — open an issue describing the problem you want solved,
  not just the solution you have in mind.
- **Improve docs** — the README, docstrings, and inline comments are all fair game.
- **Test on your platform** — reports from Linux and macOS are especially useful,
  since releases are Windows-only.
- **Package** — a Linux AppImage/Flatpak or a macOS `.app` bundle would be very
  welcome (see [*Packaging*](#packaging)).
- **Translate** — the UI is currently English-only; localization help is welcome
  but should start with an issue so we can agree on the approach.

---

## Reporting bugs

A good bug report includes:

1. **What you did** — steps to reproduce, in order.
2. **What you expected** and **what actually happened**.
3. **Your environment** — OS and version, Python version, and whether you ran
   from source or from a release build.
4. **The log** — the last ~50 lines of `logs/kestrel.log` from your data folder
   (see the README's *Where your data lives* section). The log never contains
   plaintext secrets.
5. **Screenshots** if the bug is visual.

> **Never paste vault contents, master passwords, TOTP secrets, or unredacted
> screenshots of the vault into an issue.** If the bug only reproduces with a
> specific entry, describe its shape (e.g. "a 200-character password with symbols"),
> not its value.

For **security vulnerabilities**, do not open a public issue — see
[`SECURITY.md`](SECURITY.md).

---

## Suggesting features

Kestrel Vault deliberately stays small. Before opening a feature request, check
the README's *What it is* and *Security* sections — some "missing" features are
missing on purpose (no sync, no accounts, no telemetry, no recovery).

When you do open one, describe:

- The **problem** you're hitting.
- **How you work around it today.**
- Why you think it belongs in Kestrel Vault rather than in a companion tool.

Feature requests that add network calls, telemetry, or a recovery mechanism will
be declined — they conflict with the project's design goals.

---

## Development setup

```bash
git clone https://github.com/ibra-tuna/Kestrel-Vault.git
cd Kestrel-Vault
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m kestrel_vault
```

Run the test suite:

```bash
python -m pytest tests -q
```

Tests must pass before a PR is merged. CI runs them on every push.

---

## The architecture rule

This is the one rule that will get a PR sent back for changes.

Kestrel Vault separates a **pure-Python core** from a **Qt UI** so the core can be
tested headlessly and reused from a CLI. The following modules **must not import
`PySide6`**:

- `kestrel_vault/crypto.py`
- `kestrel_vault/storage.py`
- `kestrel_vault/models.py`
- `kestrel_vault/passwords.py`
- `kestrel_vault/importers.py`

Qt widgets live under `kestrel_vault/ui/`, and `services.py` / `theme.py` /
`icons.py` bridge the two.

If your change needs a new capability from the core, add it to the core (with a
test) and expose it through `session.py` or `services.py` — don't reach into Qt
from the core.

---

## Coding style

There's no formatter enforced in CI, so match the surrounding code:

- **4-space indentation**, no tabs.
- **Type hints** on public functions and class attributes.
- **Docstrings** on modules and non-obvious functions — short, not ceremonial.
- **Line length** ~88 characters (Black-ish, but not enforced).
- **Imports** grouped stdlib → third-party → local, each alphabetized.
- **No bare `except:`** — catch specific exceptions.
- **No `print()` for diagnostics** — use the existing logger.

If you're unsure, open the PR — style nits are easy to fix in review.

---

## Tests

- Add a test for any non-trivial change to the core.
- Keep tests **headless** — no Qt, no network, no real filesystem writes outside
  `tmp_path`.
- Prefer small, focused tests over one mega-test.
- Name them `test_<what>_<expected>`.

Run them before pushing:

```bash
python -m pytest tests -q
```

---

## Commits and pull requests

1. **Fork** the repo, then branch from `main`:
   `git checkout -b feature/my-thing` or `fix/my-bug`.
2. Keep commits **focused**. A PR that fixes one bug and refactors three files
   will be asked to split.
3. **Write a clear PR description** — what changed, why, and how you tested it.
   Link the issue it closes (`Closes #123`).
4. **Update the README** if your change is user-visible (new feature, new
   shortcut, new import format, changed file layout).
5. **Don't commit generated artifacts** — `dist/`, `build/`, `.venv/`,
   `__pycache__/`, and anything in your data folder.
6. **Don't bump the version** — the maintainer handles releases.
7. Be patient. This is a side project; reviews may take a few days.

### PR checklist

Before you hit "Create pull request", confirm:

- [ ] `python -m pytest tests -q` passes locally.
- [ ] No `import PySide6` in the core modules listed above.
- [ ] No secrets, master passwords, or vault contents in the diff.
- [ ] No generated files (`dist/`, `build/`, `.venv/`, `__pycache__/`).
- [ ] README updated if the change is user-visible.
- [ ] PR description explains **what** and **why**, not just **how**.

---

## Packaging

The PyInstaller spec (`kestrel_vault.spec`) is Windows-only. Contributions that
add:

- a **Linux** AppImage or Flatpak manifest,
- a **macOS** `.app` bundle build (PyInstaller, py2app, or a briefcase setup),

...are welcome. Please open an issue first so we can agree on the approach before
you invest time in a full build pipeline.

---

## Security issues

**Do not open a public issue for a security problem.** See [`SECURITY.md`](SECURITY.md)
for the private reporting channel.

---

## License

By submitting a pull request you agree that your contribution is licensed under
the project's [MIT License](LICENSE). No CLA is required.

If you're contributing on behalf of an employer, make sure you have permission to
do so.
