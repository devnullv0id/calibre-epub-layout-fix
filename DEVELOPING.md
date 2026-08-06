# Building and testing

## Build

```
python build.py                                   # -> dist/EPUB-Layout-Fix.zip
python build.py --install                         # also runs calibre-customize -a
python build.py --restart                         # close calibre, build, install, start it again
```

Use `--restart` while developing. calibre gets closed first because it writes its stale in-memory
plugin list back on exit, which silently undoes an install made while it was open.

## Releasing

Bump `PLUGIN_VERSION` in `calibre_plugins/epub_layout_fix/action_base.py`, update the changelog,
and push to main. That is the whole release process: CI reads the version, and if no tag matches
it yet, creates the tag at that commit and publishes a release with the zip attached. Pushing
again without a bump publishes nothing, so ordinary commits are safe.

There is no `latest` tag to maintain. GitHub resolves `/releases/latest` to the newest
non-prerelease release on its own, which is the link the README points at.

Every push also uploads the zip as a run artifact, released or not, so the newest build is always
downloadable from the Actions tab. `build.py --check-version` still guards a tag pushed by hand
against a mismatched `PLUGIN_VERSION`.

## Tests

```
python tests/test_fixer.py [reference-library]    # engine: fixtures, parity, idempotency
python tests/test_matrix.py [book ...]            # every setting x every book, engine only
calibre-debug tests/test_matrix.py [book ...]     # ... plus the pipeline and settings plumbing
calibre-debug tests/smoke_gui.py                  # Qt widgets, offscreen
calibre-debug tests/test_pipeline.py [book ...]   # convert -> polish -> upgrade -> fix
calibre-debug tests/test_library.py               # throwaway library, action path, import listener
calibre-debug tests/test_progress.py              # job stages
calibre-debug tests/test_cli.py                   # the command line, end to end
calibre-debug tests/test_oracle.py                # calibre's own Check book, before vs after
```

`test_oracle.py` is the only suite that judges the fix by something other than the plugin's own
assumptions: it runs calibre's *Tools → Check book* before and after and fails if a warning or
error class appears more often afterwards. Set `EPLF_BOOKS` to include real books — off by default
because the checker is slow, several minutes for 32 books.

The suites that drive calibre load the **installed** plugin, not the working tree. Run
`python build.py --install` first or you will be testing the last build.

The engine imports nothing from calibre or Qt, so `test_fixer.py` runs under a plain interpreter.
Its parity test checks for identical results to the PowerShell implementation this was ported from,
across the same 21-book library.

`test_matrix.py` is the broad one. It crosses ten settings combinations with every fixture and any
books you hand it (or a folder in `EPLF_BOOKS`), then checks what has to hold whatever is switched
on: the archive is a valid EPUB with every CRC intact, every XHTML and the OPF still parse, no
entry is gained or lost, anything not meant to be touched is byte-identical, nothing references a
file that is not in the archive, and a second run changes nothing. Under `calibre-debug` it also
crosses the target version, polish, beautify and conversion stages, and checks that each setting
read back through `config` is the one the engine receives.

## Layout

| File | Contains |
|---|---|
| `fixer.py` | The engine. No calibre or Qt imports, so it is testable on its own |
| `jobs.py` | Worker entry points; runs on calibre's job thread, never touches the GUI |
| `ui.py` | The four `InterfaceAction`s and the job plumbing |
| `cli.py` | The command line, reached through `Plugin.cli_main` |
| `panel.py` | The settings widgets, shared by the conversion window and Preferences |
| `config.py` | Persisted settings and the Preferences page |
| `automation.py` | The import listener |
| `dialog.py`, `report_dialog.py` | The conversion window and the report window |
| `action_base.py` | The `InterfaceActionBase` declarations and the version number |

calibre loads one `Plugin` subclass per zip, so `FixLayoutAction.initialize` registers the other
three by appending them to calibre's initialised-plugin list. That is what makes all four appear
separately under **Preferences → Toolbars & menus**.
