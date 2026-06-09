#!/usr/bin/env bash

set -x

UPSTREAM_REPO=$1
UPSTREAM_BRANCH=$2
DOWNSTREAM_BRANCH=$3
GITHUB_TOKEN=$4
FETCH_ARGS=$5
MERGE_ARGS=$6
PUSH_ARGS=$7
SPAWN_LOGS=$8
DOWNSTREAM_REPO=$9
IGNORE_FILES=${10}
UPSTREAM_SSH_KEY=${11}
UPSTREAM_TAG=${12}

if [[ -z "$UPSTREAM_REPO" ]]; then
  echo "Missing \$UPSTREAM_REPO"
  exit 1
fi


if [[ -z "$UPSTREAM_BRANCH" ]]
then
  REPO_NAME=${UPSTREAM_REPO/https:\/\/github.com\//}
  REPO_NAME=${REPO_NAME%.git}
  echo "REPO_NAME=$REPO_NAME"
  UPSTREAM_BRANCH=$(curl -s https://api.github.com/repos/$REPO_NAME | jq -r '.default_branch')
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

if [[ $DOWNSTREAM_REPO == "GITHUB_REPOSITORY" ]]
then
  git clone "https://github.com/${GITHUB_REPOSITORY}.git" work
  cd work || { echo "Missing work dir" && exit 2 ; }
  git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
else
  git clone $DOWNSTREAM_REPO work
  cd work || { echo "Missing work dir" && exit 2 ; }
  git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${DOWNSTREAM_REPO/https:\/\/github.com\//}"
fi



git config user.name "${GITHUB_ACTOR}"
git config user.email "${GITHUB_ACTOR}@users.noreply.github.com"
git config --local user.password ${GITHUB_TOKEN}
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

git push origin ${DOWNSTREAM_BRANCH}


IFS=', ' read -r -a exclusions <<< "$IGNORE_FILES"
for exclusion in "${exclusions[@]}"
do
   echo "$exclusion"
   echo "$exclusion merge=ours" >> .git/info/attributes
   cat .git/info/attributes
done

if [[ -n "$UPSTREAM_TAG" ]]; then
  echo "UPSTREAM_TAG=$UPSTREAM_TAG"
  echo "Upstream tag is defined, pulling from tag $UPSTREAM_TAG instead of branch $UPSTREAM_BRANCH"
  MERGE_RESULT=$(git merge ${MERGE_ARGS} tags/${UPSTREAM_TAG} 2>&1)
else
  MERGE_RESULT=$(git merge ${MERGE_ARGS} upstream/${UPSTREAM_BRANCH} 2>&1)
fi

echo "$MERGE_RESULT"

if [[ $MERGE_RESULT == "" ]] || [[ $MERGE_RESULT == *"merge failed"* ]] || [[ $MERGE_RESULT == *"error:"* ]] || [[ $MERGE_RESULT == *"Aborting"* ]]; then
  exit 1
elif [[ $MERGE_RESULT == *"CONFLICT ("* ]]; then
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
  git push ${PUSH_ARGS} origin ${DOWNSTREAM_BRANCH} || exit $?
elif [[ $MERGE_RESULT != *"Already up to date."* ]]; then
  git commit -m "Merged upstream"
  git push ${PUSH_ARGS} origin ${DOWNSTREAM_BRANCH} || exit $?
fi

cd ..
rm -rf work
