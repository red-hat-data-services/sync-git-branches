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

## Development

For private repositories, the `token` input must have read access to the upstream and downstream repositories (including repository metadata for automatic default-branch discovery) and write access to the downstream repository. The action uses this token for GitHub HTTPS clone, fetch, and push without embedding it in remote URLs.

In [`action.yml`](https://github.com/dabreadman/sync-upstream-repo/blob/master/action.yml), we define `inputs`.  
We then pass these arguments into [`Dockerfile`](https://github.com/dabreadman/sync-upstream-repo/blob/master/Dockerfile), which then passed onto [`entrypoint.sh`](https://github.com/dabreadman/sync-upstream-repo/blob/master/entrypoint.sh).

`entrypoint.sh` does the heavy-lifting,

- Set up variables.
- Set up git config.
- Clone downstream repository.
- Fetch upstream repository.
- Attempt merge if behind, and push to downstream.
