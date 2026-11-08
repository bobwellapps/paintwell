#!/bin/bash
# Paintwell snap, step 1 of 3: install the build tools.  Run this once.
set -e
echo "== Installing Snapcraft (the snap build tool) =="
sudo snap install snapcraft --classic || sudo snap refresh snapcraft
echo
echo "== Installing LXD (Snapcraft builds inside a clean Ubuntu 24.04 container) =="
sudo snap install lxd || true
sudo lxd init --auto || true
if ! id -nG "$USER" | grep -qw lxd; then
    sudo usermod -aG lxd "$USER"
    echo
    echo "Done. Step 2 will pick up the new permission by itself, or restart first to be sure."
else
    echo
    echo "Done. You can go on to step 2:  ./snap-tools/2-build.sh"
fi
