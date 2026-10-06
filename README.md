# Sync Upstream Repo Fork

This is a Github Action used to merge changes from remote.  

This is forked from [dabreadman](https://github.com/dabreadman/sync-upstream-repo), with me adding optional parameter of downstream repo URL. Now this action can run from a third repo and sync upstream repo to provided downstream repo.

## Use case

- Perserve a repo while keeping up-to-date (rather than to clone it).
- Have a branch in sync with upstream, and pull changes into dev branch.

## Usage

Example github action [here](https://github.com/THIS-IS-NOT-A-BACKUP/go-web-proxy/blob/main/.github/workflows/sync5.yml):

```YAML
name: Sync Upstream

env:
  # Required, URL to upstream (fork base)
  UPSTREAM_URL: "https://github.com/dabreadman/go-web-proxy.git"
  # Required, token to authenticate bot, could use ${{ secrets.GITHUB_TOKEN }} 
  # Over here, we use a PAT instead to authenticate workflow file changes.
  WORKFLOW_TOKEN: ${{ secrets.WORKFLOW_TOKEN }}
  # Optional, defaults to main
  UPSTREAM_BRANCH: "main"
  # Optional, defaults to current repo
  DOWNSTREAM_URL: "https://github.com/dchourasia/sync-upstream-repo"
  # Optional, defaults to UPSTREAM_BRANCH
  DOWNSTREAM_BRANCH: ""
  # Optional fetch arguments
  FETCH_ARGS: ""
  # Optional merge arguments
  MERGE_ARGS: ""
  # Optional push arguments
  PUSH_ARGS: ""
  # Optional toggle to spawn time logs (keeps action active) 
  SPAWN_LOGS: "false" # "true" or "false"
  IGNORE_FILES: "dummy.ext"

# This runs every day on 1801 UTC
on:
  schedule:
    - cron: '1 18 * * *'
  # Allows manual workflow run (must in default branch to work)
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: GitHub Sync to Upstream Repository
        uses: dchourasia/sync-upstream-repo@main
        with: 
          upstream_repo: ${{ env.UPSTREAM_URL }}
          upstream_branch: ${{ env.UPSTREAM_BRANCH }}
          downstream_repo: ${{ env.DOWNSTREAM_URL }}
          downstream_branch: ${{ env.DOWNSTREAM_BRANCH }}
          token: ${{ env.WORKFLOW_TOKEN }}
          fetch_args: ${{ env.FETCH_ARGS }}
          merge_args: ${{ env.MERGE_ARGS }}
          push_args: ${{ env.PUSH_ARGS }}
          spawn_logs: ${{ env.SPAWN_LOGS }}
          ignore_files: ${{ env.IGNORE_FILES }}
```

This action syncs your repo (merge changes from `remote`) at branch `main` with the upstream repo ``` https://github.com/dabreadman/go-web-proxy.git ``` every day on 1801 UTC.  
Do note GitHub Action scheduled workflow usually face delay as it is pushed onto a queue, the delay is usually within 1 hour long.

Note: If `SPAWN_LOGS` is set to `true`, this action will create a `sync-upstream-repo` file at root directory with timestamps of when the action is ran. This is to mitigate the hassle of GitHub disabling actions for a repo when inactivity was detected.

## Dry run

Set `dry_run: "true"` to execute the existing sync locally without pushing to a
remote repository. It defaults to `"false"`; omitted inputs perform normal syncs.

```yaml
- name: Check main-to-release merge feasibility
  uses: red-hat-data-services/sync-git-branches@main
  with:
    upstream_repo: https://github.com/red-hat-data-services/COMPONENT.git
    upstream_branch: main
    downstream_repo: https://github.com/red-hat-data-services/COMPONENT.git
    downstream_branch: rhoai-3.6
    token: ${{ secrets.READ_TOKEN }}
    ignore_files: '.tekton/*, .github/renovate.json, .tekton/README.md'
    dry_run: "true"
```

Cloning, fetching, merging, conflict resolution, excluded-file restoration, and
local commits/amendments run as usual in the disposable `work` clone. Every push
is skipped, including the initial push and pushes after conflict resolution.
`spawn_logs: "true"` stays local too. Logs identify each skipped push.

Dry runs retain the action's existing merge-error handling and exit-status
behavior. Unresolved conflicts still fail the run. `dry_run` accepts only
`"true"` or `"false"`.

Dry-run credentials need only read access to both repositories, including
repository metadata when discovering the upstream default branch. A dry run
checks local merge feasibility; branch rules, push permissions and races with
later remote updates are evaluated when a real sync pushes.

Passing `--no-commit` as `merge_args`, or `--dry-run` as `push_args`, is not a
replacement for this input. Only `dry_run: "true"` suppresses every push.

## Development

For normal syncs of private repositories, the `token` input must have read access to the upstream and downstream repositories (including repository metadata for automatic default-branch discovery) and write access to the downstream repository. The action uses this token for GitHub HTTPS clone, fetch, and push without embedding it in remote URLs.

In [`action.yml`](https://github.com/dabreadman/sync-upstream-repo/blob/master/action.yml), we define `inputs`.  
We then pass these arguments into [`Dockerfile`](https://github.com/dabreadman/sync-upstream-repo/blob/master/Dockerfile), which then passed onto [`entrypoint.sh`](https://github.com/dabreadman/sync-upstream-repo/blob/master/entrypoint.sh).

`entrypoint.sh` does the heavy-lifting,

- Set up variables.
- Set up git config.
- Clone downstream repository.
- Fetch upstream repository.
- Attempt merge if behind, and push to downstream.

Local checks (Git and Bash required; tests use temporary local repositories):

```bash
bash -n entrypoint.sh
uv run --no-project --python 3.12 python -m unittest discover -s tests -v
```
