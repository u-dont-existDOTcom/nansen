# Install / recovery

Use this when `~/Téléchargements/nansen-signal-lab` exists but is not a Git repository, for example after extracting an older ZIP copy.

The procedure preserves `.env`, renames the stale directory instead of deleting it, clones the canonical repository, restores the API key, verifies that the local checkout is a real Git worktree and that `bootstrap.sh` contains the corrected `python -m pytest` invocation, then resumes bootstrap.

```bash
set -Eeuo pipefail
DEST="$HOME/Téléchargements/nansen-signal-lab"
REPO="https://github.com/u-dont-existDOTcom/nansen.git"

if git -C "$DEST" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$DEST" pull --ff-only
else
  KEY_TMP="$(mktemp)"
  HAVE_KEY=0

  if [ -f "$DEST/.env" ]; then
    cp "$DEST/.env" "$KEY_TMP"
    chmod 600 "$KEY_TMP"
    HAVE_KEY=1
  fi

  if [ -e "$DEST" ]; then
    mv "$DEST" "${DEST}.stale-$(date +%Y%m%d-%H%M%S)"
  fi

  git clone "$REPO" "$DEST"

  if [ "$HAVE_KEY" -eq 1 ]; then
    mv "$KEY_TMP" "$DEST/.env"
    chmod 600 "$DEST/.env"
  else
    rm -f "$KEY_TMP"
  fi
fi

cd "$DEST"
git rev-parse --show-toplevel
grep -n '\.venv/bin/python -m pytest -q' bootstrap.sh
bash bootstrap.sh
```

## Expected pre-bootstrap verification

`git rev-parse --show-toplevel` must print the `nansen-signal-lab` path, and the `grep` command must show:

```text
.venv/bin/python -m pytest -q
```

If either condition fails, do not run API experiments from that directory; it is not the canonical checkout.
