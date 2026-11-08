#!/bin/bash
# Paintwell snap, step 2 of 3: build the snap, then install it on this computer to test.
set -e
cd "$(dirname "$0")/.."
if ! command -v snapcraft >/dev/null; then
    echo "Snapcraft isn't installed yet. Run ./snap-tools/1-setup.sh first."; exit 1
fi
if ! id -nG | grep -qw lxd; then
    # this terminal was started before the account joined the lxd group
    if id -nG "$USER" | grep -qw lxd && [ -z "$PAINTWELL_SG" ]; then
        echo "(Switching this run to the lxd group: no need to log out.)"
        export PAINTWELL_SG=1
        exec sg lxd -c "\"$0\" $*"
    fi
    echo "Your account isn't in the lxd group yet. Run ./snap-tools/1-setup.sh first,"
    echo "then restart the computer (or run:  newgrp lxd  in this terminal) and try again."
    exit 1
fi
if ! lxc storage list >/dev/null 2>&1; then
    echo "LXD isn't set up yet. Running:  sudo lxd init --auto"
    sudo lxd init --auto
fi
version=$(grep -m1 "^version:" snap/snapcraft.yaml | sed "s/version: *//; s/'//g; s/\"//g")
echo "== Building Paintwell $version (the first build takes a while) =="
snapcraft pack
snapfile="paintwell_${version}_amd64.snap"
if [ ! -f "$snapfile" ]; then
    echo "The build finished but $snapfile wasn't found."; exit 1
fi
echo
echo "== Built: $snapfile =="
read -r -p "Install it on this computer to test it? [Y/n] " ans
if [ "${ans,,}" != "n" ]; then
    sudo snap install --dangerous "./$snapfile"
    # permissions the store won't connect automatically until it approves them
    sudo snap connect paintwell:removable-media || true
    sudo snap connect paintwell:raw-usb || true
    sudo snap connect paintwell:avahi-observe || true
    sudo snap connect paintwell:network-bind || true
    # printing from a snap goes through the small "cups" helper snap, which passes jobs on
    # to your normal printer system (the Snap Store sets this up for people who install
    # Paintwell from there; a test copy needs it done by hand)
    if ! snap list cups >/dev/null 2>&1; then
        echo
        echo "Printing from snaps needs the small 'cups' helper snap. It works alongside your"
        echo "normal printer setup and doesn't change it."
        read -r -p "Install it now? [Y/n] " a2
        [ "${a2,,}" != "n" ] && sudo snap install cups
    fi
    snap list cups >/dev/null 2>&1 && sudo snap connect paintwell:cups cups:cups || true
    echo
    echo "Installed. Start it from your app menu or by typing:  paintwell"
    echo "Please check: open and save a picture, print, and scan (if you have a scanner)."
    echo "To remove the test copy later:  sudo snap remove paintwell"
fi
echo
echo "When you're happy, go on to step 3:  ./snap-tools/3-publish.sh"
