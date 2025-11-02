#!/bin/bash
#########################################################################
# Title:         Saltbox Repo Cloner Script                             #
# Author(s):     desimaniac, salty                                      #
# URL:           https://github.com/r3dlobst3r/sb                         #
# --                                                                    #
#########################################################################
#                   GNU General Public License v3.0                     #
#########################################################################

################################
# Variables
################################

VERBOSE=false
BRANCH="feature/cloudflare-tunnel-support"
SALTBOX_PATH="/srv/git/saltbox"
SALTBOX_REPO="https://github.com/r3dlobst3r/saltbox.git"

################################
# Functions
################################

usage () {
    echo "Usage:"
    echo "    sb_repo -b <branch>    Repo branch to use. Default is 'feature/cloudflare-tunnel-support'."
    echo "    sb_repo -v             Enable Verbose Mode."
    echo "    sb_repo -h             Display this help message."
}

run_cmd() {
    local error_output
    local cmd_exit_code

    if $VERBOSE; then
        printf '%s\n' "+ $*" >&2
        "$@"
    else
        error_output=$("$@" 2>&1)
    fi
    cmd_exit_code=$?

    if [ $cmd_exit_code -ne 0 ]; then
        echo "Command failed with exit code $cmd_exit_code: $*" >&2
        if [ -n "$error_output" ]; then
            echo "Error output: $error_output" >&2
        fi
        exit $cmd_exit_code
    fi
}

################################
# Argument Parser
################################

while getopts ':b:vh' f; do
    case $f in
    b)  BRANCH=$OPTARG;;
    v)  VERBOSE=true;;
    h)
        usage
        exit 0
        ;;
    \?)
        echo "Invalid Option: -$OPTARG" 1>&2
        echo ""
        usage
        exit 1
        ;;
    esac
done

################################
# Main
################################

$VERBOSE && echo "git branch selected: $BRANCH"

## Clone Saltbox and pull latest commit
if [ -d "$SALTBOX_PATH" ]; then
    if [ -d "$SALTBOX_PATH/.git" ]; then
        cd "$SALTBOX_PATH" || exit
        run_cmd git fetch --all --prune
        # shellcheck disable=SC2086
        run_cmd git checkout -f $BRANCH
        # shellcheck disable=SC2086
        run_cmd git reset --hard origin/$BRANCH
        run_cmd git submodule update --init --recursive
        $VERBOSE && echo "git branch: $(git rev-parse --abbrev-ref HEAD)"
    else
        cd "$SALTBOX_PATH" || exit
        run_cmd rm -rf library/
        run_cmd git init
        run_cmd git remote add origin "$SALTBOX_REPO"
        run_cmd git fetch --all --prune
        # shellcheck disable=SC2086
        run_cmd git branch $BRANCH origin/$BRANCH
        # shellcheck disable=SC2086
        run_cmd git reset --hard origin/$BRANCH
        run_cmd git submodule update --init --recursive
        $VERBOSE && echo "git branch: $(git rev-parse --abbrev-ref HEAD)"
    fi
else
    # shellcheck disable=SC2086
    run_cmd git clone -b $BRANCH "$SALTBOX_REPO" "$SALTBOX_PATH"
    cd "$SALTBOX_PATH" || exit
    run_cmd git submodule update --init --recursive
    $VERBOSE && echo "git branch: $(git rev-parse --abbrev-ref HEAD)"
fi

release=$(lsb_release -cs 2>/dev/null | grep -v "No LSB modules are available.")

## Copy settings and config files into Saltbox folder
shopt -s nullglob
for i in "$SALTBOX_PATH"/defaults/*.default; do
    if [ ! -f "$SALTBOX_PATH/$(basename "${i%.*}")" ]; then
        if [[ $release =~ (jammy)$ ]]; then
            run_cmd cp -n "${i}" "$SALTBOX_PATH/$(basename "${i%.*}")"
        elif [[ $release =~ (noble)$ ]]; then
            run_cmd cp --update=none "${i}" "$SALTBOX_PATH/$(basename "${i%.*}")"
        fi
    fi
done
shopt -u nullglob

## Activate Git Hooks
cd "$SALTBOX_PATH" || exit
run_cmd bash "$SALTBOX_PATH/bin/git/init-hooks"

## Download saltbox.fact file
FACT_URL="https://github.com/r3dlobst3r/ansible-facts/releases/latest/download/saltbox-facts"
FACT_PATH="$SALTBOX_PATH/ansible_facts.d/saltbox.fact"

$VERBOSE && echo "Downloading the saltbox.fact file..."
mkdir -p "$SALTBOX_PATH/ansible_facts.d"

# Execute curl command and store the exit status
curl -fsSL "$FACT_URL" -o "$FACT_PATH"
curl_exit_status=$?

# Check the exit status
if [ $curl_exit_status -eq 0 ]; then
    run_cmd chmod +x "$FACT_PATH"
    $VERBOSE && echo "The saltbox.fact file downloaded and set as executable."
else
    echo "Failed to download the saltbox.fact file. curl exit status: $curl_exit_status" >&2
    exit 1
fi

$VERBOSE && echo "sb_repo.sh ran successfully."
exit 0
