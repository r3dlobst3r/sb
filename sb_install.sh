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
RELEASE_FILE="/srv/git/sb/release.txt"
TARGET_BINARY_PATH="/srv/git/sb/sb"
SB_INSTALL_SCRIPT="$SB_PATH/sb_install.sh"
SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
BRANCH="feature/cloudflare-tunnel-support"

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

################################
# Main
################################

# Check if Cloudbox is installed
# develop
if [ -d "/srv/git/cloudbox" ]; then
    echo "==== Cloudbox Install Detected ===="
    echo "Cloudbox installed. Exiting..."
    echo "==== Cloudbox Install Detected ===="
    exit 1
fi

# feature/cloudflare-tunnel-support
for directory in /home/*/*/ ; do
    base=$(basename "$directory")
    if [ "$base" == "cloudbox" ]; then
        echo "==== Cloudbox Install Detected ===="
        echo "Cloudbox installed. Exiting..."
        echo "==== Cloudbox Install Detected ===="
        exit 1
    fi
done

# Check for supported Ubuntu Releases
release=$(lsb_release -cs 2>/dev/null | grep -v "No LSB modules are available.")

# Add more releases like (focal|jammy)$
if [[ $release =~ (jammy|noble)$ ]]; then
    echo "${release^} is currently supported."
else
    echo "==== UNSUPPORTED OS ===="
    echo "Install cancelled: ${release^} is not supported."
    echo "Supported OS: 22.04 (Jammy) and 24.04 (Nobel)"
    echo "==== UNSUPPORTED OS ===="
    exit 1
fi

# Check if using valid arch
arch=$(uname -m)

if [[ $arch =~ (x86_64)$ ]]; then
    echo "$arch is currently supported."
else
    echo "==== UNSUPPORTED CPU Architecture ===="
    echo "Install cancelled: $arch is not supported."
    echo "Supported CPU Architecture(s): x86_64"
    echo "==== UNSUPPORTED CPU Architecture ===="
    exit 1
fi

# Check for LXC using systemd-detect-virt
if systemd-detect-virt -c | grep -qi 'lxc'; then
    echo "==== UNSUPPORTED VIRTUALIZATION ===="
    echo "Install cancelled: Running in an LXC container is not supported."
    echo "==== UNSUPPORTED VIRTUALIZATION ===="
    exit 1
fi

# Check if specific desktop packages are installed
if dpkg -l ubuntu-desktop &>/dev/null; then
    echo "==== UNSUPPORTED DESKTOP INSTALL ===="
    echo "Install cancelled: Only Ubuntu Server is supported."
    echo "==== UNSUPPORTED DESKTOP INSTALL ===="
    exit 1
fi

# Define required CPU features for x86-64-v2
#required_features=("sse4_2" "popcnt")

# Check for x86-64-v2 support
#for feature in "${required_features[@]}"; do
#  if ! grep -q " $feature " /proc/cpuinfo; then
#    echo "==== UNSUPPORTED CPU Microarchitecture ===="
#    echo "Install cancelled: CPU does not support minimum microarchitecture level: x86-64-v2"
#    echo "==== UNSUPPORTED CPU Microarchitecture ===="
#    exit 1
#  fi
#done

echo "Installing Saltbox Dependencies."

$VERBOSE && echo "Script Path: $SCRIPT_PATH"

# Update apt cache
run_cmd apt-get update

# Install git
run_cmd apt-get install -y git curl

# Remove existing repo folder
if [ -d "$SB_PATH" ]; then
    run_cmd rm -rf $SB_PATH;
fi

# Clone SB repo
run_cmd mkdir -p /srv/git
run_cmd mkdir -p /srv/ansible
run_cmd git clone --branch feature/cloudflare-tunnel-support "${SB_REPO}" "$SB_PATH"

download_binary

# Set chmod +x on script files
run_cmd chmod +x $SB_PATH/*.sh

$VERBOSE && echo "Script Path: $SCRIPT_PATH"
$VERBOSE && echo "SB Install Path: "$SB_INSTALL_SCRIPT

# Check if /usr/local/bin exists, create it if not
if [ ! -d "/usr/local/bin" ]; then
    run_cmd mkdir -m 0755 -p /usr/local/bin
fi

## Create script symlinks in /usr/local/bin
shopt -s nullglob
for i in "$SB_PATH"/*.sh; do
    if [ ! -f "/usr/local/bin/$(basename "${i%.*}")" ]; then
        run_cmd ln -s "${i}" "/usr/local/bin/$(basename "${i%.*}")"
    fi
done
shopt -u nullglob

# Install Saltbox Dependencies
run_cmd bash -H $SB_PATH/sb_dep.sh $VERBOSE_OPT

# Clone Saltbox Repo
run_cmd bash -H $SB_PATH/sb_repo.sh -b "${BRANCH}" $VERBOSE_OPT

echo "Saltbox Dependencies were successfully installed."
