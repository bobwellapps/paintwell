#!/bin/bash
# Puts Paintwell on GitHub (github.com/bobwellapps/paintwell) and makes a release.
# The snap packaging (snap/, snap-tools/, SNAP-RELEASE.md, STORE-LISTING.txt) stays on this
# computer and is not uploaded. Safe to run again: it uploads your latest changes each time.
set -e
cd "$(dirname "$0")"
OWNER=bobwellapps
REPO=paintwell
VERSION=$(grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)".*/\1/')
TAG="v$VERSION"

echo "== 1. GitHub's command-line tool (gh) =="
if ! command -v gh >/dev/null; then
    echo "Installing gh and git..."
    sudo apt install -y gh git
fi
command -v git >/dev/null || sudo apt install -y git
if ! gh auth status >/dev/null 2>&1; then
    echo "Sign in to GitHub: choose GitHub.com, HTTPS, and 'Login with a web browser'."
    gh auth login --hostname github.com --git-protocol https --web
fi
gh auth setup-git >/dev/null 2>&1 || true
me=$(gh api user --jq .login)
echo "Signed in as: $me"
[ "$me" = "$OWNER" ] || echo "Note: you're signed in as '$me', not '$OWNER'."

echo
echo "== 2. Saving this version with git =="
[ -d .git ] || git init -q -b main
# name and email for the history: your GitHub name and its private no-reply address
git config user.name >/dev/null || git config user.name "$OWNER"
if ! git config user.email >/dev/null; then
    id=$(gh api user --jq .id)
    git config user.email "${id}+${me}@users.noreply.github.com"
fi
chmod +x publish-to-github.sh 2>/dev/null || true
remote_exists=0
gh repo view "$OWNER/$REPO" >/dev/null 2>&1 && remote_exists=1
if [ $remote_exists = 1 ]; then
    git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
    git fetch -q origin main 2>/dev/null || true
fi
# save local changes first, so the merge below has something to merge with
git add -A
git diff --cached --quiet || git commit -q -m "Paintwell $VERSION (local changes)"
# bring in anything changed on the GitHub website (e.g. README or pictures edited there);
# where both sides changed the same lines, the GitHub version is kept
if [ $remote_exists = 1 ] && git rev-parse -q --verify origin/main >/dev/null; then
    if ! git merge-base --is-ancestor origin/main HEAD; then
        echo "Bringing in changes made on the GitHub website..."
        git merge -q --no-edit -X theirs --allow-unrelated-histories origin/main
    fi
fi
# snap packaging stays on this computer only: take it off GitHub if it was uploaded before
git rm -r -q --cached --ignore-unmatch snap snap-tools SNAP-RELEASE.md STORE-LISTING.txt
# and no "building the snap" section in the README
python3 - <<'PY'
import re
s = open("README.md").read()
t = re.sub(r"## Building the snap\n.*?(?=\n## )", "", s, flags=re.S)
if t != s:
    open("README.md", "w").write(t)
PY
git add -A
if git diff --cached --quiet; then
    echo "Nothing new to save."
else
    git commit -q -m "Paintwell $VERSION"
    echo "Saved as 'Paintwell $VERSION'."
fi

echo
echo "== 3. Uploading to github.com/$OWNER/$REPO =="
if [ $remote_exists = 1 ]; then
    git push -u origin main
else
    gh repo create "$OWNER/$REPO" --public --source=. --remote=origin --push \
        --description "An easy, powerful image editor for Linux: layers, Content-Aware Fill and 50+ effects" \
        --homepage "https://snapcraft.io/paintwell"
fi
gh repo edit "$OWNER/$REPO" --add-topic image-editor --add-topic photo-editor --add-topic paint \
    --add-topic linux --add-topic qt --add-topic pyside6 >/dev/null 2>&1 || true

echo
echo "== 4. Release $TAG with the AppImage =="
appimage=""
if ls Paintwell-x86_64.AppImage.part* >/dev/null 2>&1 && [ ! -f Paintwell-x86_64.AppImage ]; then
    cat Paintwell-x86_64.AppImage.part* > Paintwell-x86_64.AppImage
fi
for f in Paintwell-x86_64.AppImage ~/Downloads/Paintwell-x86_64.AppImage; do
    [ -f "$f" ] && { appimage="$f"; break; }
done
notes="RELEASE-NOTES-$VERSION.md"
[ -f "$notes" ] || { echo "Paintwell $VERSION" > /tmp/pw-notes.md; notes=/tmp/pw-notes.md; }
if gh release view "$TAG" --repo "$OWNER/$REPO" >/dev/null 2>&1; then
    echo "Release $TAG already exists."
    [ -n "$appimage" ] && gh release upload "$TAG" "$appimage" --repo "$OWNER/$REPO" --clobber
else
    if [ -n "$appimage" ]; then
        gh release create "$TAG" "$appimage" --repo "$OWNER/$REPO" --title "Paintwell $VERSION" --notes-file "$notes"
    else
        gh release create "$TAG" --repo "$OWNER/$REPO" --title "Paintwell $VERSION" --notes-file "$notes"
        echo "(No AppImage found: put Paintwell-x86_64.AppImage in this folder and run this again to add it.)"
    fi
fi

echo
echo "Done:  https://github.com/$OWNER/$REPO"
echo "       https://github.com/$OWNER/$REPO/releases"
