#!/usr/bin/env bash
# Take a bare Amazon Linux team box to a machine that can run the town.
#
# Written after doing it by hand cost an hour of a metered box. Everything
# here was found the expensive way:
#
#   * the repo is private and the box has no GitHub credentials, so the code
#     arrives as an archive rather than a clone - and no token is left on a
#     machine every teammate logs into as the same user;
#   * Amazon Linux 2023 ships none of the GTK/X11 stack Chromium links
#     against (16 libraries), has no fonts at all, and grants no user
#     namespaces - so Chrome starts, answers on its DevTools port, and dies
#     the moment it renders. Six cameras filmed nothing and every log said
#     only "no close frame received or sent";
#   * there is no sudo, so all of it is fetched with `dnf download` and
#     unpacked with rpm2cpio into $HOME;
#   * the engines have their own venv, separate from the app's.
#
# Usage, from your laptop:
#     ./Run-commands/bootstrap-box.sh team@<address>
set -euo pipefail

BOX="${1:?usage: bootstrap-box.sh team@<address>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20 "$BOX")

echo "== 1/5  shipping the code (private repo, no clone)"
git -C "$HERE" archive --format=tar HEAD app engine Run-commands docs/GOALS.md \
  | gzip > /tmp/kasukabe-code.tgz
echo "   $(du -h /tmp/kasukabe-code.tgz | cut -f1) at $(git -C "$HERE" rev-parse --short HEAD)"
scp -o BatchMode=yes -q /tmp/kasukabe-code.tgz "$BOX:~/"
"${SSH[@]}" 'rm -rf ~/kasukabe && mkdir -p ~/kasukabe && tar xzf ~/code.tgz -C ~/kasukabe 2>/dev/null || tar xzf ~/kasukabe-code.tgz -C ~/kasukabe'

echo "== 2/5  python environments (app and engine are separate)"
"${SSH[@]}" 'set -e
  cd ~/kasukabe/app   && uv venv .venv --python 3.12 -q && uv pip install --python .venv/bin/python -e . -q
  cd ~/kasukabe/engine && uv sync -q
  echo "   app + engine venvs built"'

echo "== 3/5  headless Chrome, and the 16 libraries and 104 fonts it needs"
"${SSH[@]}" 'set -e
  cd ~/kasukabe/app
  uv pip install --python .venv/bin/python playwright imageio-ffmpeg -q
  .venv/bin/python -m playwright install chromium 2>&1 | tail -1
  mkdir -p ~/chromelibs/rpms ~/chromelibs/root
  cd ~/chromelibs/rpms
  dnf download -q --resolve --destdir . \
    atk at-spi2-atk at-spi2-core cups-libs libxcb libxkbcommon alsa-lib \
    mesa-libgbm libX11 libXext cairo pango libXcomposite libXdamage libXfixes \
    libXrandr nss nspr libdrm libxshmfence \
    fontconfig freetype dejavu-sans-fonts dejavu-serif-fonts liberation-fonts \
    google-noto-sans-fonts 2>&1 | tail -1
  cd ~/chromelibs/root
  for r in ~/chromelibs/rpms/*.rpm; do rpm2cpio "$r" | cpio -idmu --quiet 2>/dev/null || true; done
  echo "   $(find ~/chromelibs/root -name "*.so*" | wc -l) libraries, $(find ~/chromelibs/root -name "*.ttf" | wc -l) fonts"'

echo "== 4/5  pointing Chrome at them, for every future shell"
"${SSH[@]}" 'grep -q chromelibs ~/.bashrc 2>/dev/null || cat >> ~/.bashrc <<"EOF"

# Headless Chrome on a box with no root, no fonts and no GTK libs. Without
# these Chrome starts, answers on its DevTools port, and dies as it renders -
# so the recorder connects and the socket closes under it, once per frame,
# saying only "no close frame received or sent".
export LD_LIBRARY_PATH="$(find $HOME/chromelibs/root -name "*.so*" -exec dirname {} \; 2>/dev/null | sort -u | tr "\n" ":")$LD_LIBRARY_PATH"
export FONTCONFIG_PATH="$HOME/chromelibs/root/etc/fonts"
export XDG_DATA_HOME="$HOME/chromelibs/root/usr/share"
EOF
  echo "   written to ~/.bashrc"'

echo "== 5/5  proving it, before any money is spent"
"${SSH[@]}" 'set -e
  source ~/.bashrc
  cd ~/kasukabe/app
  TRACE_SQLITE_PATH=data/preflight.db .venv/bin/python -m pytest broker/tests -q 2>&1 | tail -1
  .venv/bin/python -c "
import sys; sys.path.insert(0, \"scripts\")
from record_ui import find_chrome, find_ffmpeg
from pathlib import Path
c, f = find_chrome(), find_ffmpeg()
ok = Path(c).is_absolute() and Path(c).exists()
print(\"   chrome:\", \"found\" if ok else \"MISSING\")
print(\"   ffmpeg:\", \"found\" if f else \"MISSING\")
raise SystemExit(0 if (ok and f) else \"cannot record\")"
  nproc | sed "s/^/   CPUs: /"; free -g | awk "/Mem:/{print \"   RAM : \"\$2\" GB\"}"'

cat <<'NEXT'

== ready. Two things left, and only you can do the first:

   ssh BOX
   cd ~/kasukabe/app && nano .env        # OPENAI_API_KEY, ONLY_MODEL=gpt-6-luna,
                                         # OPENAI_RPM=500, WORLD_BASE_URL=...
   chmod 600 .env

   tmux new -s fifty
   ./run-scenario.sh world/scenarios/fifty_from_day_two.json \
       fifty-day2-$(date +%Y%m%d) 4
NEXT
