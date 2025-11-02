#!/bin/bash
#################################################################################
# Title:         Saltbox: Dependencies Installer                                #
# Author(s):     L3uddz, Desimaniac, EnorMOZ, salty                             #
# URL:           https://github.com/r3dlobst3r/sb                                 #
# Description:   Installs dependencies needed for Saltbox.                      #
# --                                                                            #
#################################################################################
#                     GNU General Public License v3.0                           #
#################################################################################

set -e
set -o pipefail

################################
# Error Handling
################################

error() {
    echo "Error: $1" >&2
    exit 1
}

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

VERBOSE=false

readonly SYSCTL_PATH="/etc/sysctl.conf"
readonly PYTHON3_CMD=("/srv/ansible/venv/bin/python3" "-m" "pip" "install" "--timeout=360" "--no-cache-dir" "--disable-pip-version-check" "--upgrade")

################################
# Argument Parser
################################

while getopts 'v' f; do
    case $f in
    v) VERBOSE=true;;
    *) error "Invalid option";;
    esac
done

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

install_pip() {
  cd /tmp || error "Failed to change directory to /tmp"
  run_cmd curl -sLO https://bootstrap.pypa.io/get-pip.py
  run_cmd python3 get-pip.py
}

add_repo() {
    local repo="$1"
    local file="$2"

    if ! grep -q "^${repo}$" "$file"; then
        echo "$repo" >> "$file"
    else
        echo "$repo already present in $file."
    fi
}

################################
# Main
################################

## IPv6
if [ -f "$SYSCTL_PATH" ]; then
    ## Remove 'Disable IPv6' entries from sysctl
    run_cmd sed -i -e '/^net.ipv6.conf.all.disable_ipv6/d' "$SYSCTL_PATH"
    run_cmd sed -i -e '/^net.ipv6.conf.default.disable_ipv6/d' "$SYSCTL_PATH"
    run_cmd sed -i -e '/^net.ipv6.conf.lo.disable_ipv6/d' "$SYSCTL_PATH"
    run_cmd sysctl -p
fi

## Environmental Variables
export DEBIAN_FRONTEND=noninteractive

## Install Pre-Dependencies
run_cmd run_cmd apt-get install -o DPkg::Lock::Timeout=-1 -y software-properties-common apt-transport-https
run_cmd run_cmd apt-get update

# Check for supported Ubuntu Releases
release=$(lsb_release -cs) || error "Failed to determine Ubuntu release"

## Add apt repos
if [[ $release =~ (jammy)$ ]]; then
    sources_file="/etc/apt/sources.list"

    run_cmd rm -rf /etc/apt/sources.list.d/*
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc) main" "$sources_file"
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc) universe" "$sources_file"
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc) restricted" "$sources_file"
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc) multiverse" "$sources_file"

    run_cmd apt-get update

elif [[ $release =~ (noble)$ ]]; then
    sources_file="/etc/apt/sources.list"

    run_cmd find /etc/apt/sources.list.d/ -type f ! -name "ubuntu.sources" -delete
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc) main restricted universe multiverse" "$sources_file"
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc)-updates main restricted universe multiverse" "$sources_file"
    add_repo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -sc)-backports main restricted universe multiverse" "$sources_file"
    add_repo "deb http://security.ubuntu.com/ubuntu $(lsb_release -sc)-security main restricted universe multiverse" "$sources_file"

    run_cmd apt-get update

else
    error "Unsupported Distro, exiting."
fi

## Install apt Dependencies
run_cmd apt-get install -y \
    locales \
    nano \
    git \
    curl \
    wget \
    jq \
    file \
    gpg-agent \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    python3-testresources \
    python3-apt \
    python3-venv

# Generate en_US.UTF-8 locale if it doesn't already exist
if ! locale -a | grep -q "^en_US.UTF-8"; then
    run_cmd locale-gen en_US.UTF-8
fi

# Update locale settings simultaneously
run_cmd update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

# Export the locale for the current script
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8

# Check if both locale settings are correct
current_lc_all=$(locale | grep 'LC_ALL' | cut -d= -f2 | tr -d '"')
current_lang=$(locale | grep 'LANG' | cut -d= -f2 | tr -d '"')

if [ "$current_lc_all" != "en_US.UTF-8" ] || [ "$current_lang" != "en_US.UTF-8" ]; then
    echo "One or both locale settings are incorrect, trying to reconfigure locales..."
    run_cmd dpkg-reconfigure locales

    # Re-check after reconfiguration
    current_lc_all=$(locale | grep 'LC_ALL' | cut -d= -f2 | tr -d '"')
    current_lang=$(locale | grep 'LANG' | cut -d= -f2 | tr -d '"')
    
    if [ "$current_lc_all" != "en_US.UTF-8" ] || [ "$current_lang" != "en_US.UTF-8" ]; then
        error "Locale settings still incorrect: LC_ALL=$current_lc_all, LANG=$current_lang"
    fi
fi

echo "Locale set to en_US.UTF-8"

cd /srv/ansible || error "Failed to change directory to /srv/ansible"

if [[ $release =~ (jammy)$ ]]; then
    echo "${release^}, deploying venv with Python 3.12."
    run_cmd add-apt-repository ppa:deadsnakes/ppa --yes
    run_cmd apt-get install python3.12 python3.12-dev python3.12-venv -y
    run_cmd python3.12 -m ensurepip
    run_cmd python3.12 -m venv venv

elif [[ $release =~ (noble)$ ]]; then
    echo "Noble, deploying venv with Python 3.12."
    # Cannot use pypa install method with Noble due to PEP 668
    run_cmd apt-get install -y python3-pip
    run_cmd python3.12 -m venv venv

else
    error "Unsupported Distro, exiting."
fi

if [[ $release =~ (jammy)$ ]]; then
    install_pip
fi

## Check if venv Python exists
if [ ! -f "/srv/ansible/venv/bin/python3" ]; then
    echo "Virtual environment Python not found. Waiting 10 seconds..."
    sleep 10
    if [ ! -f "/srv/ansible/venv/bin/python3" ]; then
        error "Virtual environment Python still not found after waiting. Exiting."
    fi
fi

## Install pip3 Dependencies
run_cmd "${PYTHON3_CMD[@]}" pip setuptools wheel
run_cmd "${PYTHON3_CMD[@]}" --requirement /srv/git/sb/requirements-saltbox.txt
run_cmd cp /srv/ansible/venv/bin/ansible* /usr/local/bin/
run_cmd cp /srv/ansible/venv/bin/certbot /usr/local/bin/
run_cmd cp /srv/ansible/venv/bin/apprise /usr/local/bin/

$VERBOSE && echo "sb_dep.sh ran successfully."
exit 0
