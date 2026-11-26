#!/usr/bin/env bash
# Commits state/feed changes (if any) and refreshes the CDN copy of the feed.
set -e
git config user.name "cluefinder-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A state feed
if git diff --cached --quiet; then echo "nothing to save"; exit 0; fi
FEED_CHANGED=$(git diff --cached --name-only | grep -c '^feed/' || true)
git commit -q -m "$1"
for i in 1 2 3 4 5; do
  git pull -q --rebase origin main && git push -q origin HEAD:main && break
  sleep $((i * 5))
done
if [ "$FEED_CHANGED" != "0" ]; then
  curl -s "https://purge.jsdelivr.net/gh/${GITHUB_REPOSITORY}@main/feed/puzzles.json" > /dev/null || true
fi
