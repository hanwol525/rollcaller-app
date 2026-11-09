"""Pronunciation eval harness (Brief 04a).

Measures the pronunciation path. Changes nothing in it: every engine runs
through the app's own functions, imported read-only, and no module here writes
to anything under ``app/``.

Two stages, deliberately separate (see ``__main__``):

* ``generate`` calls engines and writes one JSONL record per name and engine.
* ``score`` reads those records and is **pure**, so re-scoring never re-calls
  an LLM. That is what makes it safe to change a scoring rule after a paid run.

Run from ``backend/``, always::

    ./app/venv/bin/python -m eval generate --split dev --dry-run

``backend/`` is not optional: ``app.config`` runs a ``mkdir`` on
``settings.storage_fs_root`` at import time (config.py:58-59), which resolves
against the process CWD, so importing the app from anywhere else scatters a
stray ``blobstore/``.
"""
