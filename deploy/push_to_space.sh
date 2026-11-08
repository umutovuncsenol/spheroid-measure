#!/usr/bin/env bash
# Publish the current commit of this repository to a Hugging Face Space (Docker SDK).
#
#   bash deploy/push_to_space.sh <hf-user>/<space-name>
#
# Needs: git, and a Hugging Face access token with WRITE permission
# (huggingface.co -> Settings -> Access Tokens). Git asks for it as the password; the user name is
# your Hugging Face user name. The Space must already exist (huggingface.co/new-space, SDK: Gradio,
# template: Blank; Docker Spaces are a paid option, the Gradio runtime is free and works the same here).
# Every run replaces the Space contents with the current commit; the Space then rebuilds itself.
set -euo pipefail

SPACE="${1:?usage: bash deploy/push_to_space.sh <hf-user>/<space-name>}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo ">> Cloning the Space $SPACE ..."
git clone --depth 1 "https://huggingface.co/spaces/$SPACE" "$WORK/space"

echo ">> Copying the current commit ..."
find "$WORK/space" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$REPO_DIR" archive HEAD | tar -x -C "$WORK/space"
# Space-specific files go to the Space root: README header (SDK settings), space_app.py (starts the
# server), requirements.txt (CPU-only PyTorch), packages.txt (system libraries)
cp "$REPO_DIR"/deploy/hf_space/* "$WORK/space/"
rm -f "$WORK/space/Dockerfile" "$WORK/space/.dockerignore"   # the Space uses the Gradio SDK runtime, not Docker

cd "$WORK/space"
git add -A
if git diff --cached --quiet; then
  echo ">> Nothing changed; the Space is already up to date."
  exit 0
fi
git -c user.name="$(git -C "$REPO_DIR" config user.name || echo deploy)" \
    -c user.email="$(git -C "$REPO_DIR" config user.email || echo deploy@localhost)" \
    commit -q -m "Deploy $(git -C "$REPO_DIR" rev-parse --short HEAD)"
echo ">> Pushing (user name = your Hugging Face user name, password = your WRITE token) ..."
git push
echo ""
echo "Done. The Space is rebuilding (about 10 minutes the first time):"
echo "  https://huggingface.co/spaces/$SPACE"
echo "The website itself will be at:"
echo "  https://$(echo "$SPACE" | tr '/' '-' | tr '[:upper:]_.' '[:lower:]--').hf.space"
