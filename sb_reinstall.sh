#!/bin/bash
#########################################################################
# Title:         Saltbox sb Re-install Script                           #
# Author(s):     salty                                                  #
# URL:           https://github.com/r3dlobst3r/sb                         #
# --                                                                    #
#########################################################################
#                   GNU General Public License v3.0                     #
#########################################################################

################################
# Privilege Escalation
################################

if [ "$EUID" != 0 ]; then
    sudo "$0" "$@"
    exit $?
fi

################################
# Variables
################################

SB_REPO="https://github.com/r3dlobst3r/sb.git"
SB_PATH="/srv/git/sb"
RELEASE_FILE="/srv/git/sb/release.txt"
TARGET_BINARY_PATH="/srv/git/sb/sb"

################################
# Functions
################################

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

download_binary() {
    local github_tag
    local version
    local download_url
    local temp_binary_path
    local file_type

    if ! command -v file > /dev/null 2>&1; then
        run_cmd sudo apt-get update
        run_cmd sudo apt-get install -y file
    fi

    if [ ! -f "${RELEASE_FILE}" ]; then
        echo "Error: ${RELEASE_FILE} does not exist." >&2
        exit 1
    fi

    github_tag=$(head -n 1 "${RELEASE_FILE}" | tr -d '[:space:]')
    if [[ ! "$github_tag" =~ ^refs/tags/ ]]; then
        echo "Error: Invalid tag format in ${RELEASE_FILE}." >&2
        exit 1
    fi

    version=${github_tag#refs/tags/}
    if [ -z "$version" ]; then
        echo "Error: No version found in tag $github_tag." >&2
        exit 1
    fi

    download_url="https://github.com/r3dlobst3r/sb/releases/download/$version/sb"

    temp_binary_path="${TARGET_BINARY_PATH}.tmp"
    run_cmd curl -L -o "${temp_binary_path}" "${download_url}"

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
# Main
################################

echo "Re-installing sb repository."
export DEBIAN_FRONTEND=noninteractive

# Remove existing repo folder
if [ -d "$SB_PATH" ]; then
    run_cmd rm -rf $SB_PATH;
fi

# Clone SB repo
run_cmd git clone --branch master "${SB_REPO}" "$SB_PATH"

download_binary

# Set chmod +x on script files
run_cmd chmod +x $SB_PATH/*.sh

## Create script symlinks in /usr/local/bin
shopt -s nullglob
for i in "$SB_PATH"/*.sh; do
    if [ ! -f "/usr/local/bin/$(basename "${i%.*}")" ]; then
        run_cmd ln -s "${i}" "/usr/local/bin/$(basename "${i%.*}")"
    fi
done
shopt -u nullglob
