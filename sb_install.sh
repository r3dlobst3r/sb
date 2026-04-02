#!/bin/bash
#shellcheck disable=SC2220
#########################################################################
# Title:         Saltbox Install Script                                 #
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
VERBOSE_OPT=""
SB_REPO="https://github.com/r3dlobst3r/sb.git"
SB_PATH="/srv/git/sb"
TARGET_BINARY_PATH="/usr/local/bin/sb"
SB_INSTALL_SCRIPT="$SB_PATH/sb_install.sh"
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
BRANCH="arm-support"
BRANCH_OPT=""

################################
# Functions
################################

run_cmd() {
    local cmd_exit_code

    # Print the command being executed
    printf '%s\n' "+ $*" >&2

    "$@"
    cmd_exit_code=$?

    if [ $cmd_exit_code -ne 0 ]; then
        echo "Command failed with exit code $cmd_exit_code: $*" >&2
        exit $cmd_exit_code
    fi
}

download_binary() {
    local api_url
    local version
    local download_url
    local temp_binary_path
    local file_type

    if ! command -v file > /dev/null 2>&1; then
        run_cmd apt-get update
        run_cmd apt-get install -y file
    fi

    api_url="https://api.github.com/repos/r3dlobst3r/sb-go/releases/latest"

    # Silent but fail properly on HTTP errors
    version=$(curl -fsSL "${api_url}" | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')
    if [ -z "$version" ]; then
        echo "Error: Could not determine latest version from GitHub API." >&2
        exit 1
    fi

    echo "Latest sb-go version: $version"

    download_url="https://github.com/r3dlobst3r/sb-go/releases/download/${version}/sb_linux_arm64"

    temp_binary_path="${TARGET_BINARY_PATH}.tmp"

    # Silence curl progress but preserve errors
    run_cmd curl -fsSL -o "${temp_binary_path}" "${download_url}"

    file_type=$(file -b --mime-type "${temp_binary_path}")
    if [[ "$file_type" != application/* ]]; then
        echo "Error: Downloaded file is not a binary. Detected type: $file_type" >&2
        rm -f "${temp_binary_path}"
        exit 1
    fi

    run_cmd mv -f "${temp_binary_path}" "${TARGET_BINARY_PATH}"
    run_cmd chmod +x "${TARGET_BINARY_PATH}"
}

################################
# Argument Parser
################################

while getopts 'vb:' f; do
  case $f in
  v)  VERBOSE=true
      VERBOSE_OPT="-v"
  ;;
  b)  BRANCH=$OPTARG
  ;;
  esac
done

# Build optional branch argument for sb setup
if [ -n "$BRANCH" ]; then
    BRANCH_OPT="-b $BRANCH"
fi

################################
# Main
################################

# Update apt cache
run_cmd apt-get update

# Install curl
run_cmd apt-get install -y curl

# Check if /usr/local/bin exists, create it if not
if [ ! -d "/usr/local/bin" ]; then
    run_cmd mkdir -m 0755 -p /usr/local/bin
fi

download_binary

# Run sb setup
run_cmd /usr/local/bin/sb setup $VERBOSE_OPT $BRANCH_OPT
