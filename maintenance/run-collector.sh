#!/usr/bin/env bash
set -euo pipefail
branch=collector-state
state_dir="$RUNNER_TEMP/release-collector-state"
state="$state_dir/state.json"
gh auth setup-git
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
if git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null; then
  git fetch --quiet --depth=1 origin "refs/heads/$branch"
  git worktree add --quiet --detach "$state_dir" FETCH_HEAD
  test -s "$state" || { echo 'State branch exists but its checkpoint is missing'; exit 1; }
else
  rc=$?
  [[ $rc == 2 ]] || exit "$rc"
  git worktree add --quiet --detach "$state_dir" HEAD
  git -C "$state_dir" switch --orphan "$branch"
  gh label create release-candidate --color 3B6D56 --description '模型发布线索，等待核对' --force
  gh label create prepare-release --color B78337 --description '维护者已核对，生成收录分支' --force
fi
checkpoint() {
  git -C "$state_dir" add -- state.json
  if ! git -C "$state_dir" diff --cached --quiet; then
    git -C "$state_dir" commit --quiet -m "$1"
    git -C "$state_dir" push --quiet origin "HEAD:refs/heads/$branch"
  fi
}
rc=0
python maintenance/collector.py scan --state "$state" || rc=$?
test -s "$state"
if [[ -n "${REVIEW_MODEL:-}" ]]; then
  python maintenance/collector.py recheck --state "$state" --model "$REVIEW_MODEL" || rc=$?
fi
checkpoint 'Checkpoint sources and queued release evidence'
python maintenance/collector.py deliver --state "$state" || rc=$?
checkpoint 'Record delivered candidate Issues'
exit "$rc"
