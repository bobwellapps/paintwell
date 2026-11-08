#!/bin/bash
# Paintwell snap, step 3 of 3: upload to the Snap Store.
# The first upload goes to the "edge" channel (a testing channel only people who ask get).
# Once you've checked it, this script can move it to "stable" for everyone.
set -e
cd "$(dirname "$0")/.."
name=$(grep -m1 "^name:" snap/snapcraft.yaml | sed "s/name: *//")
version=$(grep -m1 "^version:" snap/snapcraft.yaml | sed "s/version: *//; s/'//g; s/\"//g")
snapfile="${name}_${version}_amd64.snap"
[ -f "$snapfile" ] || { echo "$snapfile not found. Run ./snap-tools/2-build.sh first."; exit 1; }

echo "== Logging in to the Snap Store =="
echo "(You need a free account first: sign up at https://snapcraft.io)"
snapcraft whoami >/dev/null 2>&1 || snapcraft login

echo
echo "== Reserving the name '$name' (only needed the first time) =="
# you own the name if the store will show you its status (works with old and new snapcraft)
if snapcraft status "$name" >/dev/null 2>&1 || \
   snapcraft names 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$name"; then
    echo "'$name' is already registered to you."
else
    snapcraft register "$name" || {
        echo
        echo "Couldn't register '$name'. If it's taken, choose another name, change 'name:'"
        echo "at the top of snap/snapcraft.yaml, then run steps 2 and 3 again."
        exit 1
    }
fi

echo
echo "== Uploading $snapfile to the edge channel =="
snapcraft upload --release=edge "$snapfile"

echo
echo "Uploaded. To test the store copy (on another computer if you can):"
echo "    sudo snap remove $name; sudo snap install $name --edge"
echo
read -r -p "Move the newest edge upload to stable now, so everyone gets it? [y/N] " ans
if [ "${ans,,}" = "y" ]; then
    rev=$(snapcraft status "$name" 2>/dev/null | awk '$0 ~ /edge/ {for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+$/) r=$i} END {print r}')
    if [ -n "$rev" ]; then
        snapcraft release "$name" "$rev" stable
        echo "Released revision $rev to stable: https://snapcraft.io/$name"
    else
        echo "Couldn't work out the revision. Run:  snapcraft status $name"
        echo "then:  snapcraft release $name <revision> stable"
    fi
else
    echo "Later, run:  snapcraft status $name   to see the revision number, then"
    echo "             snapcraft release $name <revision> stable"
fi
echo
echo "Next: add screenshots and the description on your store page:"
echo "    https://snapcraft.io/$name/listing"
