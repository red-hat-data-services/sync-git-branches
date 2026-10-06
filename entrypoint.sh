#!/usr/bin/env bash

UPSTREAM_REPO=$1
UPSTREAM_BRANCH=$2
DOWNSTREAM_BRANCH=$3
export GITHUB_TOKEN="$4"
FETCH_ARGS=$5
MERGE_ARGS=$6
PUSH_ARGS=$7
SPAWN_LOGS=$8
DOWNSTREAM_REPO=$9
IGNORE_FILES=${10}
UPSTREAM_SSH_KEY=${11}
UPSTREAM_TAG=${12}
DRY_RUN=${13:-false}

case "$DRY_RUN" in
  true|false) ;;
  *) echo "dry_run must be 'true' or 'false'"; exit 1 ;;
esac

push_downstream() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run: skipping push to downstream branch $DOWNSTREAM_BRANCH"
    return 0
  fi
  git push "$@" origin "$DOWNSTREAM_BRANCH"
}

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run enabled: merging and restoring excluded files locally; all pushes are skipped."
fi

if [[ -z "$UPSTREAM_REPO" ]]; then
  echo "Missing \$UPSTREAM_REPO"
  exit 1
fi


if [[ -z "$UPSTREAM_BRANCH" ]]
then
  REPO_NAME=${UPSTREAM_REPO/https:\/\/github.com\//}
  REPO_NAME=${REPO_NAME%.git}
  echo "REPO_NAME=$REPO_NAME"
  REPO_DETAILS=$(curl -fsS -H "Authorization: Bearer ${GITHUB_TOKEN}" "https://api.github.com/repos/${REPO_NAME}") || {
    echo "Could not retrieve upstream repository details"
    exit 1
  }
  UPSTREAM_BRANCH=$(jq -er '.default_branch | select(type == "string" and length > 0)' <<< "$REPO_DETAILS") || {
    echo "Could not determine upstream default branch"
    exit 1
  }
  echo "UPSTREAM_BRANCH=$UPSTREAM_BRANCH"
fi

if [[ -z "$DOWNSTREAM_BRANCH" ]]; then
  echo "Missing \$DOWNSTREAM_BRANCH"
  echo "Default to ${UPSTREAM_BRANCH}"
  DOWNSTREAM_BRANCH=$UPSTREAM_BRANCH
fi

if ! echo "$UPSTREAM_REPO" | grep '\.git'; then
  UPSTREAM_REPO="https://github.com/${UPSTREAM_REPO_PATH}.git"
fi

echo "UPSTREAM_REPO=$UPSTREAM_REPO"

GITHUB_CREDENTIAL_HELPER='!f() { if [ "$1" = get ]; then printf "username=x-access-token\npassword=%s\n" "$GITHUB_TOKEN"; fi; }; f'

if [[ $DOWNSTREAM_REPO == "GITHUB_REPOSITORY" ]]
then
  DOWNSTREAM_REPO="https://github.com/${GITHUB_REPOSITORY}.git"
fi

git clone -c "credential.https://github.com.helper=$GITHUB_CREDENTIAL_HELPER" "$DOWNSTREAM_REPO" work || exit 2
cd work || { echo "Missing work dir" && exit 2 ; }


git config user.name "${GITHUB_ACTOR}"
git config user.email "${GITHUB_ACTOR}@users.noreply.github.com"
git config --global merge.ours.driver true

if [[ -n "$UPSTREAM_SSH_KEY" ]]; then
  mkdir -p "$HOME/.ssh"
  chmod 700 $HOME/.ssh
  echo "$UPSTREAM_SSH_KEY" > $HOME/.ssh/upstream_ssh_key
  chmod 600 $HOME/.ssh/upstream_ssh_key

  export GIT_SSH_COMMAND="ssh -i ~/.ssh/upstream_ssh_key -o UserKnownHostsFile=/script/known_hosts"
  # convert upstream repo to a ssh-based URI
  UPSTREAM_REPO=$(echo "$UPSTREAM_REPO" | sed -E 's|https://.*github.com/(.*)/(.*).git|git@github.com:\1/\2.git|')
fi

git remote add upstream "$UPSTREAM_REPO"
git fetch ${FETCH_ARGS} upstream --tags
git remote -v

git checkout origin/${DOWNSTREAM_BRANCH}
git checkout -b ${DOWNSTREAM_BRANCH}

case ${SPAWN_LOGS} in
  (true)    echo -n "sync-upstream-repo https://github.com/dabreadman/sync-upstream-repo keeping CI alive."\
            "UNIX Time: " >> sync-upstream-repo
            date +"%s" >> sync-upstream-repo
            git add sync-upstream-repo
            git commit sync-upstream-repo -m "Syncing upstream";;
  (false)   echo "Not spawning time logs"
esac

push_downstream


IFS=', ' read -r -a exclusions <<< "$IGNORE_FILES"
for exclusion in "${exclusions[@]}"
do
   echo "$exclusion"
   echo "$exclusion merge=ours" >> .git/info/attributes
   cat .git/info/attributes
done

DOWNSTREAM_HEAD=$(git rev-parse HEAD)

restore_excluded_files() {
  if [[ -z "$IGNORE_FILES" ]]; then
    return
  fi

  local needs_amend=false

  while IFS=$'\t' read -r status file; do
    [[ -z "$status" ]] && continue
    local matched=false
    for exclusion in "${exclusions[@]}"; do
      if [[ "$file" == $exclusion ]]; then
        matched=true
        break
      fi
    done

    if $matched; then
      echo "Restoring excluded file to downstream state: $file"
      if [[ "$status" == "A" ]]; then
        git rm -f "$file"
      else
        git checkout "$DOWNSTREAM_HEAD" -- "$file"
      fi
      needs_amend=true
    fi
  done <<< "$(git diff --name-status "$DOWNSTREAM_HEAD" HEAD)"

  if $needs_amend; then
    git commit --amend --no-edit
  fi
}

if [[ -n "$UPSTREAM_TAG" ]]; then
  echo "UPSTREAM_TAG=$UPSTREAM_TAG"
  echo "Upstream tag is defined, pulling from tag $UPSTREAM_TAG instead of branch $UPSTREAM_BRANCH"
  MERGE_RESULT=$(git merge ${MERGE_ARGS} tags/${UPSTREAM_TAG} 2>&1)
else
  MERGE_RESULT=$(git merge ${MERGE_ARGS} upstream/${UPSTREAM_BRANCH} 2>&1)
fi

echo "$MERGE_RESULT"

if [[ $MERGE_RESULT == *"CONFLICT ("* ]]; then
  if [[ -z "$IGNORE_FILES" ]]; then
    echo "Merge conflicts detected and no exclusion patterns to resolve them"
    exit 1
  fi

  echo "Conflicts detected, attempting to auto-resolve conflicts on excluded files..."
  UNMERGED_FILES=$(git diff --name-only --diff-filter=U)
  HAS_UNRESOLVABLE=false

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    MATCHED=false
    for exclusion in "${exclusions[@]}"; do
      if [[ "$file" == $exclusion ]]; then
        MATCHED=true
        break
      fi
    done

    if $MATCHED; then
      echo "Auto-resolving excluded file: $file"
      if git show :2:"$file" > /dev/null 2>&1; then
        git checkout --ours "$file"
        git add "$file"
      else
        git rm -f "$file"
      fi
    else
      echo "Unresolvable conflict on: $file"
      HAS_UNRESOLVABLE=true
    fi
  done <<< "$UNMERGED_FILES"

  if $HAS_UNRESOLVABLE; then
    echo "Unresolvable conflicts remain, aborting"
    exit 1
  fi

  echo "All conflicts on excluded files resolved"
  git commit --no-edit -m "Merged upstream"
  restore_excluded_files
  push_downstream ${PUSH_ARGS} || exit $?
elif [[ $MERGE_RESULT == "" ]] || [[ $MERGE_RESULT == *"merge failed"* ]] || [[ $MERGE_RESULT == *"error:"* ]] || [[ $MERGE_RESULT == *"Aborting"* ]]; then
  exit 1
elif [[ $MERGE_RESULT != *"Already up to date."* ]]; then
  git commit -m "Merged upstream"
  restore_excluded_files
  push_downstream ${PUSH_ARGS} || exit $?
fi

if [[ "$DRY_RUN" == "true" ]]; then
  echo "Dry run completed: no changes were pushed."
fi

cd ..
# Temporary Git files can race with deletion. Cleanup must not turn a completed
# sync into a failure. Existing merge/push failure exits are unchanged.
for attempt in 1 2 3; do
  if rm -rf work; then
    break
  fi
  if [[ $attempt -eq 3 ]]; then
    echo "::warning::Could not remove temporary checkout after 3 attempts; sync result retained."
  else
    echo "Cleanup attempt $attempt failed; retrying in 1 second."
    sleep 1
  fi
done
