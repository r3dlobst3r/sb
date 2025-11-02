import argparse
import asyncio
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from subprocess import CompletedProcess, CalledProcessError
from typing import Any, Callable, Dict, List, Optional, Union

import magic
import requests
import yaml
from yaml.loader import SafeLoader

__version__ = "0.0.0"

# Constants
ANSIBLE_PLAYBOOK_BINARY_PATH = "/usr/local/bin/ansible-playbook"
SALTBOX_REPO_PATH = "/srv/git/saltbox"
SALTBOX_PLAYBOOK_PATH = f"{SALTBOX_REPO_PATH}/saltbox.yml"
SALTBOX_ACCOUNTS_PATH = '/srv/git/saltbox/accounts.yml'
SANDBOX_REPO_PATH = "/opt/sandbox"
SANDBOX_PLAYBOOK_PATH = f"{SANDBOX_REPO_PATH}/sandbox.yml"
SALTBOXMOD_REPO_PATH = "/opt/saltbox_mod"
SALTBOXMOD_PLAYBOOK_PATH = f"{SALTBOXMOD_REPO_PATH}/saltbox_mod.yml"
SB_REPO_PATH = "/srv/git/sb"
SB_CACHE_FILE = "/srv/git/sb/cache.json"

# Global variable
saltbox_user = None


# Functions
def get_saltbox_user():
    try:
        with open(SALTBOX_ACCOUNTS_PATH, 'r') as file:
            data = yaml.load(file, Loader=SafeLoader)

        if data and isinstance(data, dict) and 'user' in data and 'name' in data['user']:
            return data['user']['name']
        else:
            print(f"Error: 'user.name' not found in {SALTBOX_ACCOUNTS_PATH}.")
            sys.exit(1)
    except FileNotFoundError:
        print(f"Error: {SALTBOX_ACCOUNTS_PATH} not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing {SALTBOX_ACCOUNTS_PATH}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error reading {SALTBOX_ACCOUNTS_PATH}: {e}")
        sys.exit(1)


def is_root():
    """
    Check if the current user has root privileges.

    Returns:
        bool: True if the user has root privileges, False otherwise.
    """
    return os.geteuid() == 0


def relaunch_as_root():
    """
    Relaunch the script with root privileges if not already root.

    Raises:
        SystemExit: Always exits after attempting to relaunch.
    """
    if not is_root():
        print("Relaunching with root privileges.")
        executable_path = os.path.abspath(sys.argv[0])
        try:
            subprocess.check_call(['sudo', executable_path] + sys.argv[1:])
        except subprocess.CalledProcessError as e:
            print(f"Failed to relaunch with root privileges: {e}")
        sys.exit(0)


def get_cached_tags(repo_path):
    """
    Retrieve cached tags and commit hash for the given repo_path.

    Args:
        repo_path (str): Path to the repository.

    Returns:
        dict: A dictionary containing cached tags and commit hash,
              or an empty dict if no cache exists.
    """
    try:
        with open(SB_CACHE_FILE, "r") as cache_file:
            cache = json.load(cache_file)
        return cache.get(repo_path, {})
    except FileNotFoundError:
        return {}


def update_cache(repo_path, commit_hash, tags):
    """
    Update the cache with the new commit hash and tags.

    Args:
        repo_path (str): Path to the repository.
        commit_hash (str): The current commit hash.
        tags (list): List of tags to cache.
    """
    try:
        with open(SB_CACHE_FILE, "r") as cache_file:
            cache = json.load(cache_file)
    except FileNotFoundError:
        cache = {}

    cache[repo_path] = {"commit": commit_hash, "tags": tags}

    with open(SB_CACHE_FILE, "w") as cache_file:
        json.dump(cache, cache_file)


def check_cache(repo_path, tags):
    """
    Check if all requested tags are present in the cache.

    Args:
        repo_path (str): The path to the repository.
        tags (list): The list of tags to check.

    Returns:
        tuple: A boolean indicating if all tags are cached and a list of missing tags.
    """
    cache = get_cached_tags(repo_path)
    if not cache:
        # If cache doesn't exist, proceed with playbook execution
        return True, []

    cached_tags = set(cache.get("tags", []))
    requested_tags = set(tags)

    missing_tags = requested_tags - cached_tags
    return not missing_tags, list(missing_tags)


def supports_color():
    """
    Returns True if the running system's terminal supports color,
    and False otherwise.
    """
    plat = sys.platform
    supported_platform = plat != 'Pocket PC' and (plat != 'win32' or 'ANSICON' in os.environ)

    # isatty is not always implemented, #6223.
    is_a_tty = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

    if not supported_platform or not is_a_tty:
        return False
    return True


class ColorPrinter:
    """A class for printing colored text to the console."""

    def __init__(self):
        """Initialize the ColorPrinter with color support and ANSI color codes."""
        self.use_color = supports_color()
        self.colors = {
            'red': '\033[91m',
            'green': '\033[92m',
            'yellow': '\033[93m',
            'blue': '\033[94m',
            'reset': '\033[0m'
        }

    def print_color(self, color, text):
        """
        Print text in the specified color if color is supported.

        Args:
            color (str): The color to print the text in.
            text (str): The text to print.
        """
        if self.use_color:
            color_code = self.colors.get(color, '')
            print(f"{color_code}{text}{self.colors['reset']}")
        else:
            print(text)


def get_console_width(default=80):
    """
    Get the width of the console in columns.

    Args:
        default (int): Default width to return if unable to determine.

    Returns:
        int: The width of the console in columns.
    """
    try:
        columns, _ = shutil.get_terminal_size()
    except AttributeError:
        columns = default
    return columns


def print_in_columns(tags, padding=2):
    """
    Print the given tags in columns that fit the console width.

    Args:
        tags (list): List of tags to print.
        padding (int): Number of spaces to add between columns.
    """
    if not tags:
        return

    console_width = shutil.get_terminal_size().columns
    max_tag_length = max(len(tag) for tag in tags) + padding
    # Ensure at least one column
    num_columns = max(1, console_width // max_tag_length)
    # Ceiling division to ensure all tags are included
    num_rows = (len(tags) + num_columns - 1) // num_columns

    for row in range(num_rows):
        for col in range(num_columns):
            idx = row + col * num_rows
            if idx < len(tags):
                print(f"{tags[idx]:{max_tag_length}}", end='')
        print()  # Newline after each row


def get_git_commit_hash(repo_path):
    """
    Get the current Git commit hash of the repository.

    Args:
        repo_path (str): The path to the Git repository.

    Returns:
        str: The current Git commit hash.

    Raises:
        SystemExit: If the repository doesn't exist or if there's an error
                    getting the commit hash.
    """
    try:
        completed_process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
    except FileNotFoundError:
        print(f"\nThe folder '{repo_path}' does not exist. "
              f"This indicates an incomplete install.\n")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error occurred while trying to get the git commit hash: "
              f"{e.stderr}")
        sys.exit(e.returncode)

    return completed_process.stdout.strip()


async def run_and_cache_ansible_tags(repo_path, playbook_path, extra_skip_tags):
    """
    Run ansible-playbook to list tags and cache the results.

    Args:
        repo_path (str): Path to the repository.
        playbook_path (str): Path to the Ansible playbook.
        extra_skip_tags (str): Additional tags to skip.
    """
    command, tag_parser = prepare_ansible_list_tags(
        repo_path, playbook_path, extra_skip_tags
    )
    if command:  # Need to fetch tags
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await process.communicate()
        output = stdout.decode()
        tag_parser(output)


def prepare_ansible_list_tags(repo_path, playbook_path, extra_skip_tags):
    """
    Prepare the command to list Ansible tags and the parser for the output.

    Args:
        repo_path (str): Path to the repository.
        playbook_path (str): Path to the Ansible playbook.
        extra_skip_tags (str): Additional tags to skip.

    Returns:
        tuple: The command to execute and the parser function for the output.
    """

    def parse_output(output):
        try:
            task_tags_line = next(
                line for line in output.split('\n') if "TASK TAGS:" in line
            )
            task_tags = (task_tags_line.split("TASK TAGS:")[1]
                         .replace('[', '').replace(']', '').strip())
            tags = [tag.strip() for tag in task_tags.split(',') if tag.strip()]
        except StopIteration:
            return (f"Error: 'TASK TAGS:' not found in the ansible-playbook "
                    f"output. Please make sure '{playbook_path}' "
                    f"is formatted correctly.")
        except Exception as e:
            return f"Error processing command output: {str(e)}"

        if repo_path != SALTBOXMOD_REPO_PATH:
            commit_hash = get_git_commit_hash(repo_path)
            update_cache(repo_path, commit_hash, tags)
        return tags

    if repo_path == SALTBOXMOD_REPO_PATH:
        command = [
            ANSIBLE_PLAYBOOK_BINARY_PATH,
            playbook_path,
            '--become',
            '--list-tags',
            f'--skip-tags=always,{extra_skip_tags}'
        ]
    else:
        cache = get_cached_tags(repo_path)
        current_commit = get_git_commit_hash(repo_path)
        if cache.get("commit") == current_commit:
            return None, lambda _: cache["tags"]  # Use cached tags
        command = [
            ANSIBLE_PLAYBOOK_BINARY_PATH,
            playbook_path,
            '--become',
            '--list-tags',
            f'--skip-tags=always,{extra_skip_tags}'
        ]

    return command, parse_output


async def handle_list_async():
    """
    Asynchronously handle listing of tags for different repositories.
    """
    repo_info = [
        (SALTBOX_REPO_PATH, SALTBOX_PLAYBOOK_PATH, "", "Saltbox tags:"),
        (SANDBOX_REPO_PATH, SANDBOX_PLAYBOOK_PATH, "sanity_check",
         "\nSandbox tags (prepend sandbox-):"),
    ]

    if os.path.isdir(SALTBOXMOD_REPO_PATH):
        repo_info.append(
            (SALTBOXMOD_REPO_PATH, SALTBOXMOD_PLAYBOOK_PATH, "sanity_check",
             "\nSaltbox_mod tags (prepend mod-):")
        )

    for repo_path, playbook_path, extra_skip_tags, base_title in repo_info:
        command, tag_parser = prepare_ansible_list_tags(
            repo_path, playbook_path, extra_skip_tags
        )

        cache_status = " (cached)" if command is None else ""

        if command:  # Fetch and parse tags if not using cache
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await process.communicate()
            tags = tag_parser(stdout.decode())
        else:  # Cached tags are available
            tags = tag_parser(None)  # Get cached tags directly

        title_with_status = f"{base_title}{cache_status}\n"
        print(title_with_status)
        if isinstance(tags, str) and tags.startswith("Error"):
            print(tags)  # Print the error message directly
        else:
            print_in_columns(tags)


def handle_list(_arguments):
    """
    Handle the list command by running the asynchronous list function.

    Args:
        _arguments: Unused arguments.
    """
    asyncio.run(handle_list_async())


def handle_recreate_venv(_arguments):
    """
    Handle the command to recreate the Ansible virtual environment.

    This function calls manage_ansible_venv with the force_recreate flag set to True.

    Args:
        _arguments: Unused arguments from the command line parser.
    """
    manage_ansible_venv(force_recreate=True)


def run_ansible_playbook(repo_path, playbook_path, ansible_binary_path,
                         tags=None, skip_tags=None, verbosity=0,
                         extra_vars=None):
    """
    Run an Ansible playbook with the given parameters.

    Args:
        repo_path (str): Path to the repository.
        playbook_path (str): Path to the Ansible playbook.
        ansible_binary_path (str): Path to the Ansible binary.
        tags (list): List of tags to run.
        skip_tags (list): List of tags to skip.
        verbosity (int): Verbosity level for Ansible output.
        extra_vars (list): List of extra variables to pass to Ansible.

    Raises:
        SystemExit: If the playbook execution fails or is interrupted.
    """
    command = [ansible_binary_path, playbook_path, "--become"]
    if tags:
        command += ["--tags", ','.join(tags)]
    if skip_tags:
        command += ["--skip-tags", ','.join(skip_tags)]
    if verbosity > 0:
        command.append('-' + 'v' * verbosity)

    if extra_vars:
        combined_extra_vars = {}
        file_extra_vars = []
        for var in extra_vars:
            if var.startswith("@"):
                file_extra_vars.append(var)
            else:
                try:
                    parsed_var = json.loads(var)
                    if isinstance(parsed_var, dict):
                        combined_extra_vars.update(parsed_var)
                    else:
                        raise ValueError("The provided JSON is not a dictionary.")
                except json.JSONDecodeError:
                    if "=" in var:
                        key, value = var.split("=", 1)
                        try:
                            parsed_value = json.loads(value, parse_float=str)
                        except json.JSONDecodeError:
                            parsed_value = value
                        combined_extra_vars[key] = parsed_value
                    else:
                        print(f"Error: Failed to parse '{var}' as valid JSON "
                              f"or a key=value pair.")
                        sys.exit(1)

        if combined_extra_vars:
            command += ["--extra-vars", json.dumps(combined_extra_vars)]

        for file_var in file_extra_vars:
            command += ["--extra-vars", file_var]

    print("Executing Ansible playbook with command: "
          f"{' '.join(shlex.quote(arg) for arg in command)}")

    try:
        _result = subprocess.run(command, cwd=repo_path, check=True)
    except KeyboardInterrupt:
        print(f"\nError: Playbook {playbook_path} run was aborted "
              f"by the user.\n")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"\nError: Playbook {playbook_path} run failed, scroll up "
              f"to the failed task to review.\n")
        sys.exit(e.returncode)

    print(f"\nPlaybook {playbook_path} executed successfully.\n")


def git_fetch_and_reset(repo_path, default_branch='master',
                        post_fetch_script=None, custom_commands=None):
    """
    Fetch and reset a Git repository to a specified branch.

    Args:
        repo_path (str): Path to the Git repository.
        default_branch (str): The default branch to reset to.
        post_fetch_script (str): Optional script to run after fetching.
        custom_commands (list): Optional list of custom commands to run.

    """
    global saltbox_user

    # Get current branch name
    result = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        cwd=repo_path,
        stdout=subprocess.PIPE,
        text=True,
        check=True
    )
    current_branch = result.stdout.strip()

    # Determine if a reset to default_branch is needed
    if current_branch != default_branch:
        print(f"Currently on branch '{current_branch}'.")
        reset_to_default = input(
            f"Do you want to reset to the '{default_branch}' branch? (y/n): "
        ).strip().lower()
        if reset_to_default != 'y':
            print(f"Updating the current branch '{current_branch}'...")
            branch = current_branch
        else:
            branch = default_branch
    else:
        branch = default_branch

    # Commands to fetch and reset
    commands = [
        ['git', 'fetch', '--quiet'],
        ['git', 'clean', '--quiet', '-df'],
        ['git', 'reset', '--quiet', '--hard', '@{u}'],
        ['git', 'checkout', '--quiet', branch],
        ['git', 'clean', '--quiet', '-df'],
        ['git', 'reset', '--quiet', '--hard', '@{u}'],
        ['git', 'submodule', 'update', '--init', '--recursive'],
        ['chown', '-R', f'{saltbox_user}:{saltbox_user}', repo_path]
    ]

    for command in commands:
        subprocess.run(command, cwd=repo_path,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)

    if post_fetch_script:
        subprocess.run(post_fetch_script, shell=True, cwd=repo_path,
                       stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       check=True)

    if custom_commands:
        for command in custom_commands:
            subprocess.run(command, shell=True, cwd=repo_path,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           check=True)

    print(f"Repository at {repo_path} has been updated. "
          f"Current branch: '{branch}'.")


def version_compare(v1: str, v2: str) -> int:
    """
    Compare two version strings.

    Args:
        v1 (str): First version string to compare.
        v2 (str): Second version string to compare.

    Returns:
        int: -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2

    Examples:
        >>> version_compare('1.0.0', '2.0.0')
        -1
        >>> version_compare('2.0.0', '2.0.0')
        0
        >>> version_compare('2.1.0', '2.0.0')
        1
    """
    v1_parts = v1.lstrip('v').split('.')
    v2_parts = v2.lstrip('v').split('.')

    for i in range(max(len(v1_parts), len(v2_parts))):
        v1_part = int(v1_parts[i]) if i < len(v1_parts) else 0
        v2_part = int(v2_parts[i]) if i < len(v2_parts) else 0

        if v1_part < v2_part:
            return -1
        elif v1_part > v2_part:
            return 1

    return 0


def download_and_install_saltbox_fact(always_update=False):
    """
    Download and install the latest saltbox.fact file.

    Args:
        always_update (bool): If True, force update regardless of current version.

    Raises:
        requests.RequestException: If there's an error downloading the file.
        IOError: If there's an error writing the file.
        Exception: For any other unexpected errors.
    """
    download_url = "https://github.com/r3dlobst3r/ansible-facts/releases/latest/download/saltbox-facts"
    target_path = "/srv/git/saltbox/ansible_facts.d/saltbox.fact"
    api_url = "https://api.github.com/repos/r3dlobst3r/ansible-facts/releases/latest"

    try:
        # Fetch the latest release info from GitHub
        response = requests.get(api_url)
        response.raise_for_status()
        latest_release = response.json()
        latest_version = latest_release['tag_name']

        if os.path.exists(target_path) and not always_update:
            # Run the existing saltbox.fact and parse its output
            result = subprocess.run([target_path], capture_output=True, text=True)
            if result.returncode == 0:
                try:
                    current_data = json.loads(result.stdout)
                    current_version = current_data.get("saltbox_facts_version")

                    if current_version is None:
                        print("Current saltbox.fact doesn't have version info. Updating...")
                    elif version_compare(current_version, latest_version) >= 0:
                        print(f"saltbox.fact is up to date (version {current_version})")
                        return
                    else:
                        print(f"New version available. Updating from {current_version} "
                              f"to {latest_version}")
                except json.JSONDecodeError:
                    print("Failed to parse current saltbox.fact output. "
                          "Proceeding with update.")
            else:
                print("Failed to run current saltbox.fact. Proceeding with update.")
        else:
            if always_update:
                print("Update forced. Proceeding with update.")
            else:
                print("saltbox.fact not found. Proceeding with update.")

        print(f"Updating saltbox.fact to version {latest_version}")

        response = requests.get(download_url)
        response.raise_for_status()

        # Ensure the directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # Write the content to the file
        with open(target_path, 'wb') as f:
            f.write(response.content)

        # Make the file executable
        os.chmod(target_path, 0o755)

        print(f"Successfully updated saltbox.fact to version {latest_version} "
              f"at {target_path}")
    except requests.RequestException as e:
        print(f"Error downloading saltbox.fact: {e}")
    except IOError as e:
        print(f"Error writing saltbox.fact: {e}")
    except Exception as e:
        print(f"Unexpected error updating saltbox.fact: {e}")


def update_saltbox(saltbox_repo_path, saltbox_playbook_file, verbosity=0):
    """
    Update Saltbox repository and run necessary tasks.

    Args:
        saltbox_repo_path (str): Path to the Saltbox repository.
        saltbox_playbook_file (str): Path to the Saltbox playbook file.
        verbosity (int): Verbosity level for Ansible playbook execution.

    Raises:
        SystemExit: If the Saltbox repository path does not exist.
    """
    print("Updating Saltbox...")

    if not os.path.isdir(saltbox_repo_path):
        print("Error: SB_REPO_PATH does not exist or is not a directory.")
        sys.exit(1)

    manage_ansible_venv(force_recreate=False)

    # Check commit hash before update
    old_commit_hash = get_git_commit_hash(saltbox_repo_path)

    git_fetch_and_reset(saltbox_repo_path, "master")

    # Always update saltbox.fact during update
    download_and_install_saltbox_fact(always_update=False)

    # Run Settings role with specified tags and skip-tags
    tags = ['settings']
    skip_tags = ['sanity-check', 'pre-tasks']
    run_ansible_playbook(
        saltbox_repo_path,
        saltbox_playbook_file,
        ANSIBLE_PLAYBOOK_BINARY_PATH,
        tags,
        skip_tags,
        verbosity
    )

    # Check commit hash after update
    new_commit_hash = get_git_commit_hash(saltbox_repo_path)

    if old_commit_hash != new_commit_hash:
        print("Saltbox Commit Hash changed, updating tags cache.")
        asyncio.run(run_and_cache_ansible_tags(
            saltbox_repo_path,
            saltbox_playbook_file,
            ""
        ))

    print("Saltbox Update Completed.")


def update_sandbox(sandbox_repo_path, sandbox_playbook_file, verbosity=0):
    """
    Update Sandbox repository and run necessary tasks.

    Args:
        sandbox_repo_path (str): Path to the Sandbox repository.
        sandbox_playbook_file (str): Path to the Sandbox playbook file.
        verbosity (int): Verbosity level for Ansible playbook execution.

    Raises:
        SystemExit: If the Sandbox repository path does not exist.
    """
    print("Updating Sandbox...")

    if not os.path.isdir(sandbox_repo_path):
        print(f"Error: {sandbox_repo_path} does not exist or is not a directory.")
        sys.exit(1)

    # Check commit hash before update
    old_commit_hash = get_git_commit_hash(sandbox_repo_path)

    git_fetch_and_reset(sandbox_repo_path, "master")

    # Run Settings role with specified tags and skip-tags
    tags = ['settings']
    skip_tags = ['sanity-check', 'pre-tasks']
    run_ansible_playbook(
        sandbox_repo_path,
        sandbox_playbook_file,
        ANSIBLE_PLAYBOOK_BINARY_PATH,
        tags,
        skip_tags,
        verbosity
    )

    # Check commit hash after update
    new_commit_hash = get_git_commit_hash(sandbox_repo_path)

    if old_commit_hash != new_commit_hash:
        print("Sandbox Commit Hash changed, updating tags cache.")
        asyncio.run(run_and_cache_ansible_tags(
            sandbox_repo_path,
            sandbox_playbook_file,
            ""
        ))

    print("Sandbox Update Completed.")


def update_sb(sb_repo_path):
    """
    Update the sb repository and binary.

    Args:
        sb_repo_path (str): Path to the sb repository.

    Raises:
        SystemExit: If any critical error occurs during the update process.
    """
    print("Updating sb.")

    if not os.path.isdir(sb_repo_path):
        print(f"Error: {sb_repo_path} does not exist or is not a directory.")
        sys.exit(1)

    # Perform git operations
    git_fetch_and_reset(sb_repo_path, "master")

    # Change permissions of sb.sh to 775
    sb_sh_path = os.path.join(sb_repo_path, 'sb.sh')
    if os.path.isfile(sb_sh_path):
        os.chmod(sb_sh_path, 0o775)
        print(f"Permissions changed for {sb_sh_path}.")
    else:
        print(f"Error: {sb_sh_path} does not exist or is not a file.")
        sys.exit(1)

    # Hardcoded paths
    release_file_path = os.path.join(sb_repo_path, 'release.txt')
    target_binary_path = os.path.join(sb_repo_path, 'sb')

    # Read the release.txt file to get the GitHub tag
    if not os.path.isfile(release_file_path):
        print(f"Error: {release_file_path} does not exist.")
        sys.exit(1)

    with open(release_file_path, 'r') as release_file:
        github_tag = release_file.readline().strip()

    # Extract the version number from the tag
    if not github_tag.startswith('refs/tags/'):
        print(f"Error: Invalid tag format in {release_file_path}.")
        sys.exit(1)

    version = github_tag[len('refs/tags/'):]
    if not version:
        print(f"Error: No version found in tag {github_tag}.")
        sys.exit(1)

    # Form the URL for the binary download
    download_url = f"https://github.com/r3dlobst3r/sb/releases/download/{version}/sb"

    # Download the binary file
    response = requests.get(download_url)
    if response.status_code != 200:
        print(f"Error: Failed to download the binary from {download_url}.")
        sys.exit(1)

    # Save the downloaded binary to a temporary file
    temp_binary_path = target_binary_path + '.tmp'
    with open(temp_binary_path, 'wb') as temp_binary_file:
        temp_binary_file.write(response.content)

    # Check if the downloaded file is a binary
    mime = magic.Magic(mime=True)
    file_type = mime.from_file(temp_binary_path)
    if not file_type.startswith('application/'):
        print(f"Error: Downloaded file is not a binary. "
              f"Detected type: {file_type}")
        os.remove(temp_binary_path)
        sys.exit(1)

    # Replace the old binary with the new one
    if os.path.isfile(temp_binary_path):
        os.replace(temp_binary_path, target_binary_path)
        print(f"Updated binary at {target_binary_path}.")

        # Ensure the new binary is executable
        os.chmod(target_binary_path, 0o755)
        print(f"Permissions changed for {target_binary_path} to be executable.")
    else:
        print(f"Error: Failed to write the new binary to {temp_binary_path}.")
        sys.exit(1)


def add_git_safe_directory_if_needed(directory):
    """
    Add a directory to git's safe.directory if it's not already there.

    Args:
        directory (str): The directory path to add.
    """
    result = subprocess.run(
        ['git', 'config', '--global', '--get-all', 'safe.directory'],
        stdout=subprocess.PIPE,
        text=True
    )
    safe_directories = result.stdout.strip().split('\n')

    if directory not in safe_directories:
        subprocess.run(
            ['git', 'config', '--global', '--add', 'safe.directory', directory]
        )
        print(f"Added {directory} to git safe.directory.")


def check_and_update_repo(sb_repo_path):
    """
    Check if the sb repository is up-to-date and update if necessary.

    Args:
        sb_repo_path (str): Path to the sb repository.

    Raises:
        SystemExit: If the directory doesn't exist or other errors occur.
    """
    has_updated = False
    try:
        if not os.path.isdir(sb_repo_path):
            raise OSError(f"Directory does not exist: {sb_repo_path}")

        for repo_path in [SALTBOXMOD_REPO_PATH, SALTBOX_REPO_PATH, SANDBOX_REPO_PATH]:
            if os.path.isdir(repo_path):
                add_git_safe_directory_if_needed(repo_path)

        # Fetch latest changes from the remote
        subprocess.run(
            ['git', 'fetch'],
            cwd=sb_repo_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        # Get the current HEAD hash and the upstream master hash
        head_hash = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=sb_repo_path
        ).strip()
        upstream_hash = subprocess.check_output(
            ['git', 'rev-parse', 'master@{upstream}'],
            cwd=sb_repo_path
        ).strip()

        if head_hash != upstream_hash:
            print("sb is not up to date with origin. Updating.")
            update_sb(sb_repo_path)
            has_updated = True

            print("Relaunching with previous arguments.")
            executable_path = os.path.abspath(sys.argv[0])
            result = subprocess.run(['sudo', executable_path] + sys.argv[1:], check=True)
            sys.exit(result.returncode)

    except OSError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        if not has_updated:
            print(f"Error executing git command: {e}")
        sys.exit(1)


def handle_update(arguments):
    """
    Handle the update command for Saltbox and Sandbox.

    This function updates both Saltbox and Sandbox repositories
    using their respective update functions.

    Args:
        arguments (argparse.Namespace): Command line arguments,
                                        expected to have a 'verbose' attribute.
    """
    update_saltbox(
        SALTBOX_REPO_PATH,
        SALTBOX_PLAYBOOK_PATH,
        arguments.verbose
    )
    update_sandbox(
        SANDBOX_REPO_PATH,
        SANDBOX_PLAYBOOK_PATH,
        arguments.verbose
    )


def handle_install(arguments):
    """
    Handle the installation process for Saltbox, Sandbox, and mod tags.

    Args:
        arguments: Parsed command-line arguments.

    Raises:
        SystemExit: If no valid tags are provided or if there are issues with the tags.
    """
    saltbox_tags: List[str] = []
    mod_tags: List[str] = []
    sandbox_tags: List[str] = []

    tags = [tag.strip() for arg in arguments.tags
            for tag in arg.split(',') if tag.strip()]
    skip_tags = [skip_tag.strip() for arg in arguments.skip_tags
                 for skip_tag in arg.split(',') if skip_tag.strip()]

    ignore_cache = any(var.startswith("sanity_check_use_cache=")
                       for var in arguments.extra_vars)

    def check_tag_existence(repo_path: str, tag: str) -> bool:
        """Check if a tag exists in the specified repository's cache."""
        cache = get_cached_tags(repo_path)
        return tag in cache.get("tags", [])

    for tag in tags:
        if tag.startswith("mod-"):
            mod_tags.append(tag[len("mod-"):])
        elif tag.startswith("sandbox-"):
            sandbox_tags.append(tag[len("sandbox-"):])
        else:
            saltbox_tags.append(tag)

    def validate_and_suggest(repo_path: str, provided_tags: List[str],
                             prefix: str = "") -> List[str]:
        """Validate tags and provide suggestions if they're missing."""
        if ignore_cache:
            return []
        cache_valid, missing_tags = check_cache(repo_path, provided_tags)
        suggestions = []
        for tag in missing_tags:
            if check_tag_existence(SANDBOX_REPO_PATH, tag):
                suggestions.append(
                    f"'{prefix}{tag}' doesn't exist in Saltbox, but "
                    f"'sandbox-{tag}' exists in Sandbox. "
                    f"Use 'sandbox-{tag}' instead."
                )
            elif check_tag_existence(SALTBOX_REPO_PATH, tag):
                if prefix:
                    suggestions.append(
                        f"'{prefix}{tag}' doesn't exist in Sandbox, but '{tag}' "
                        f"exists in Saltbox. Remove the '{prefix}' prefix."
                    )
            else:
                suggestions.append(
                    f"'{prefix}{tag}' doesn't exist in Saltbox nor Sandbox. Use "
                    f"'-e sanity_check_use_cache=false' if developing your own role."
                )
        return suggestions

    all_suggestions = []

    if saltbox_tags:
        all_suggestions.extend(validate_and_suggest(SALTBOX_REPO_PATH, saltbox_tags))

    if sandbox_tags:
        all_suggestions.extend(validate_and_suggest(SANDBOX_REPO_PATH, sandbox_tags, "sandbox-"))

    cp = ColorPrinter()

    if all_suggestions:
        print("----------------------------------------")
        cp.print_color('yellow', "The following issues were found with the provided tags:")
        for i, suggestion in enumerate(all_suggestions, 1):
            cp.print_color('red', f"{i}. {suggestion.split('.')[0]}.")
            cp.print_color('green', f"   Suggestion: {'.'.join(suggestion.split('.')[1:]).strip()}")
        print("----------------------------------------")
        sys.exit(1)

    def run_playbook(repo_path: str, playbook_path: str, playbook_tags: List[str]):
        """Run Ansible playbook with specified parameters."""
        run_ansible_playbook(
            repo_path, playbook_path, ANSIBLE_PLAYBOOK_BINARY_PATH,
            playbook_tags, skip_tags, arguments.verbose, arguments.extra_vars
        )

    if saltbox_tags:
        run_playbook(SALTBOX_REPO_PATH, SALTBOX_PLAYBOOK_PATH, saltbox_tags)

    if mod_tags:
        run_playbook(SALTBOXMOD_REPO_PATH, SALTBOXMOD_PLAYBOOK_PATH, mod_tags)

    if sandbox_tags:
        run_playbook(SANDBOX_REPO_PATH, SANDBOX_PLAYBOOK_PATH, sandbox_tags)

    if not (saltbox_tags or mod_tags or sandbox_tags):
        print("No valid tags were provided for installation.")
        sys.exit(1)


def handle_bench(_arguments):
    """
    Download and execute the bench.sh script for benchmarking.

    Args:
        _arguments: Unused argument for consistency with other handlers.
    """
    try:
        subprocess.run(
            "wget -qO- bench.sh | bash",
            shell=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while executing the benchmark: {e}")


def handle_diag(_arguments):
    """
    Run the diagnostic role using Ansible.

    Args:
        _arguments: Unused argument for consistency with other handlers.
    """
    tags = ['diag']
    run_ansible_playbook(
        SALTBOX_REPO_PATH,
        SALTBOX_PLAYBOOK_PATH,
        ANSIBLE_PLAYBOOK_BINARY_PATH,
        tags
    )


def handle_inventory(_arguments):
    """
    Handle editing of the Saltbox inventory file.

    Args:
        _arguments: Unused argument for consistency with other handlers.

    Returns:
        int: 1 if the inventory file doesn't exist, 0 otherwise.
    """
    file_path = "/srv/git/saltbox/inventories/host_vars/localhost.yml"
    default_editor = "nano"
    approved_editors = ["nano", "vim", "vi", "emacs", "gedit", "code", "micro"]

    if not os.path.isfile(file_path):
        print("Error: The inventory file 'localhost.yml' does not yet exist.")
        return 1

    editor = os.getenv("EDITOR", default_editor)
    is_approved = editor in approved_editors

    if not is_approved:
        if editor == default_editor:
            subprocess.call([default_editor, file_path])
        else:
            print(f"The EDITOR variable is set to an unrecognized value: {editor}")
            confirm = input("Are you sure you want to use it to edit the file? (y/N) ").strip().lower()
            if confirm == "y":
                subprocess.call([editor, file_path])
            else:
                print(f"Using default editor: {default_editor}")
                subprocess.call([default_editor, file_path])
    else:
        subprocess.call([editor, file_path])

    return 0


def handle_branch(arguments):
    """
    Handle switching the Saltbox repository branch.

    Args:
        arguments: Command line arguments containing branch_name and verbose.
    """
    print("Switching Saltbox repository branch...")

    git_fetch_and_reset(
        SALTBOX_REPO_PATH,
        arguments.branch_name
    )

    # Always update saltbox.fact during branch change
    download_and_install_saltbox_fact(always_update=False)

    # Run Settings role with specified tags and skip-tags
    tags = ['settings']
    skip_tags = ['sanity-check', 'pre-tasks']
    run_ansible_playbook(
        SALTBOX_REPO_PATH,
        SALTBOX_PLAYBOOK_PATH,
        ANSIBLE_PLAYBOOK_BINARY_PATH,
        tags,
        skip_tags,
        arguments.verbose
    )

    print("Updating Saltbox tags cache.")
    asyncio.run(run_and_cache_ansible_tags(
        SALTBOX_REPO_PATH,
        SALTBOX_PLAYBOOK_PATH,
        ""
    ))

    print(f"Saltbox repository branch switched to {arguments.branch_name} "
          f"and settings updated.")


def handle_sandbox_branch(arguments):
    """
    Handle switching the Sandbox repository branch.

    Args:
        arguments: Command line arguments containing branch_name and verbose.
    """
    print("Switching Sandbox repository branch...")

    git_fetch_and_reset(
        SANDBOX_REPO_PATH,
        arguments.branch_name
    )

    # Run Settings role with specified tags and skip-tags
    tags = ['settings']
    skip_tags = ['sanity-check', 'pre-tasks']
    run_ansible_playbook(
        SANDBOX_REPO_PATH,
        SANDBOX_PLAYBOOK_PATH,
        ANSIBLE_PLAYBOOK_BINARY_PATH,
        tags,
        skip_tags,
        arguments.verbose
    )

    print("Updating Sandbox tags cache.")
    asyncio.run(run_and_cache_ansible_tags(
        SANDBOX_REPO_PATH,
        SANDBOX_PLAYBOOK_PATH,
        ""
    ))

    print(f"Sandbox repository branch switched to {arguments.branch_name} "
          f"and settings updated.")


def handle_reinstall_fact(_arguments):
    """
    Handle reinstallation of saltbox.fact.

    Args:
        _arguments: Unused argument for consistency with other handlers.
    """
    print("Reinstalling saltbox.fact...")
    download_and_install_saltbox_fact(always_update=True)
    print("Reinstallation of saltbox.fact completed.")


def log_subprocess_result(
        result: Union[CompletedProcess, CalledProcessError],
        cmd: List[str],
        log_file_path: str
) -> None:
    """
    Log the command, output, and errors of a subprocess result to a file.

    This function appends the log information to the existing contents of the file.

    Args:
        result: The result object from subprocess.run or the exception.
        cmd: The command that was executed.
        log_file_path: Path to the log file for appending output and errors.
    """
    with open(log_file_path, "a") as log_file:
        log_file.write(f"Command Executed: {' '.join(cmd)}\n")
        log_file.write(f"Return Code: {result.returncode}\n\n")

        stdout = _get_output(result.stdout)
        stderr = _get_output(result.stderr)

        if stdout:
            log_file.write("Standard Output:\n")
            log_file.write(f"{stdout}\n\n")

        if stderr:
            log_file.write("Standard Error:\n")
            log_file.write(f"{stderr}\n\n")

        log_file.write("-" * 40 + "\n\n")


def _get_output(output: Union[str, bytes, None]) -> str:
    """Convert subprocess output to string."""
    if isinstance(output, str):
        return output
    elif isinstance(output, bytes):
        return output.decode('utf-8')
    return ""


def run_command(cmd: List[str], env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> None:
    """
    Execute a command using subprocess and log the results.

    This function runs the given command, logs the output to a file,
    and raises an exception if the command fails.

    Args:
        cmd (List[str]): The command to execute as a list of arguments.
        env (Optional[Dict[str, str]]): Dictionary of environment variables
                                        to set for the subprocess.
        cwd (Optional[str]): Directory to change to before executing the command.

    Raises:
        subprocess.CalledProcessError: If the command execution fails.
    """
    log_file_path = "/srv/git/saltbox/ansible-venv.log"

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            text=True,
            check=True
        )
        log_subprocess_result(result, cmd, log_file_path)
    except CalledProcessError as e:
        log_subprocess_result(e, cmd, log_file_path)
        raise CalledProcessError(
            e.returncode,
            e.cmd,
            f"Failed running {' '.join(cmd)} with error: {e.stderr}"
        ) from e


def copy_files(paths: List[str], dest_dir: str) -> None:
    """
    Copy files from given paths to the destination directory.

    This function handles both direct file paths and glob patterns.

    Args:
        paths (List[str]): A list of source file paths or glob patterns to match.
        dest_dir (str): The destination directory where files should be copied.

    Note:
        If a path in the list is not a file, a warning message is printed
        and the path is skipped.
    """
    for path in paths:
        if any(char in path for char in "*?[]"):
            files = glob.glob(path)
        else:
            files = [path]

        for file_path in files:
            if os.path.isfile(file_path):
                shutil.copy(file_path, dest_dir)
            else:
                print(f"Warning: {file_path} is not a file and will not be copied.")


def create_colored_circle(color_code: str) -> str:
    """Create a colored circle using ANSI escape codes."""
    return f"\033[{color_code}m●\033[0m"


class AnimatedTask:
    """A class for creating animated tasks with status indicators."""

    def __init__(self, task_description: str) -> None:
        """Initialize the AnimatedTask with the given description."""
        self.task_description: str = task_description
        self.running: bool = False
        self.completed: bool = False
        self.status: str = 'running'
        self.color_support: bool = supports_color()
        self.icons: Dict[str, List[str]] = {}
        self.setup_icons()

    def setup_icons(self) -> None:
        """Set up the icons based on color support."""
        if self.color_support:
            self.icons = {
                'spinner': ['◐', '◓', '◑', '◒'],
                'success': create_colored_circle('32'),  # Green
                'warning': create_colored_circle('33'),  # Yellow
                'error': create_colored_circle('31'),    # Red
                'info': create_colored_circle('34'),     # Blue
            }
        else:
            self.icons = {
                'spinner': ['|', '/', '-', '\\'],
                'success': '+',
                'warning': '!',
                'error': 'x',
                'info': 'i',
            }

    def animate(self) -> None:
        """Animate the task status."""
        idx = 0
        while self.running:
            if self.status == 'running':
                icon = self.icons['spinner'][idx % len(self.icons['spinner'])]
            else:
                icon = self.icons.get(self.status, ' ')

            sys.stdout.write(f"\r {icon}  {self.task_description}")
            sys.stdout.flush()
            time.sleep(0.1)
            idx += 1

    def start(self) -> None:
        """Start the task animation."""
        self.running = True
        threading.Thread(target=self.animate, daemon=True).start()

    def stop(self, status: str = 'success') -> None:
        """Stop the task animation and display final status."""
        self.running = False
        icon = self.icons.get(status, ' ')
        sys.stdout.write(f"\r {icon}  {self.task_description}\n")
        sys.stdout.flush()
        self.completed = True

    def set_warning(self) -> None:
        """Set the task status to warning."""
        self.status = 'warning'

    def set_error(self) -> None:
        """Set the task status to error."""
        self.status = 'error'


def print_info(message: str) -> None:
    """Print an informational message with a blue circle prefix."""
    icon = create_colored_circle('34') if supports_color() else 'i'
    print(f" {icon}  {message}")


def run_task_with_animation(
        task_description: str,
        task_function: Callable[..., Any],
        *args: Any,
        **kwargs: Any
) -> Any:
    """
    Run a task with an animated status indicator.

    Args:
        task_description: Description of the task.
        task_function: Function to run as the task.
        *args: Positional arguments to pass to the task function.
        **kwargs: Keyword arguments to pass to the task function.

    Returns:
        The result of the task function.

    Raises:
        Exception: If the task function raises an exception.
    """
    animated_task = AnimatedTask(task_description)
    animated_task.start()
    try:
        result = task_function(animated_task, *args, **kwargs)
        if animated_task.status == 'warning':
            animated_task.stop('warning')
        elif animated_task.status == 'error':
            animated_task.stop('error')
        else:
            animated_task.stop('success')
        return result
    except Exception as e:
        animated_task.stop('error')
        print(f"Error in task '{task_description}':")
        print(f"  Type: {type(e).__name__}")
        print(f"  Message: {str(e)}")
        sys.exit(1)


def manage_ansible_venv(force_recreate: bool = False) -> None:
    """
    Manage the Ansible virtual environment.

    This function creates or updates the Ansible virtual environment
    based on the current system state and the 'recreate' flag.

    Args:
        force_recreate (bool): If True, force recreation of the virtual environment.
    """
    global saltbox_user

    ansible_venv_path = "/srv/ansible"
    venv_python_path = f"{ansible_venv_path}/venv/bin/python3.12"
    python_missing = False

    def check_python_version(animated_task: AnimatedTask) -> bool:
        nonlocal python_missing
        if os.path.isdir(f"{ansible_venv_path}/venv/bin") and not os.path.isfile(venv_python_path):
            animated_task.set_warning()
            python_missing = True
        return python_missing

    python_missing = run_task_with_animation("Checking Python version in venv", check_python_version)

    recreate = force_recreate or python_missing

    if force_recreate:
        print_info("Recreate flag set, forcing recreation of Ansible venv.")
    elif python_missing:
        print_info("Python 3.12 not detected in venv, recreation required.")

    print_info("Recreating Ansible venv." if recreate else "Updating Ansible venv.")

    def detect_os_release(_animated_task: AnimatedTask) -> str:
        return subprocess.check_output(["lsb_release", "-cs"], text=True).strip()

    release = run_task_with_animation("Detecting OS release", detect_os_release)

    if recreate:
        def remove_existing_venv(_animated_task: AnimatedTask) -> None:
            run_command(["rm", "-rf", ansible_venv_path])

        run_task_with_animation("Removing existing Ansible venv", remove_existing_venv)

    if not os.path.isdir(ansible_venv_path):
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        python_cmd = "python3.12"

        if release in ("focal", "jammy"):
            def add_python_ppa(_animated_task: AnimatedTask) -> None:
                run_command(["add-apt-repository", "ppa:deadsnakes/ppa", "--yes"], env=env)

            run_task_with_animation("Adding PPA for Python 3.12", add_python_ppa)

            def install_python(_animated_task: AnimatedTask) -> None:
                run_command([
                    "apt-get", "install", "python3.12", "python3.12-dev",
                    "python3.12-venv", "-y"
                ], env=env)

            run_task_with_animation("Installing Python 3.12 and dependencies", install_python)

            def ensure_pip(_animated_task: AnimatedTask) -> None:
                run_command([python_cmd, "-m", "ensurepip"])

            run_task_with_animation("Ensuring pip is installed", ensure_pip)

            def create_venv_dir(_animated_task: AnimatedTask) -> None:
                os.makedirs(ansible_venv_path, exist_ok=True)

            run_task_with_animation("Creating Ansible venv directory", create_venv_dir)

            def create_python_venv(_animated_task: AnimatedTask) -> None:
                run_command([python_cmd, "-m", "venv", "venv"], cwd=ansible_venv_path)

            run_task_with_animation("Creating Python virtual environment", create_python_venv)

        elif release == "noble":
            def create_venv_dir(_animated_task: AnimatedTask) -> None:
                os.makedirs(ansible_venv_path, exist_ok=True)

            run_task_with_animation("Creating Ansible venv directory", create_venv_dir)

            def create_python_venv(_animated_task: AnimatedTask) -> None:
                run_command([python_cmd, "-m", "venv", "venv"], cwd=ansible_venv_path)

            run_task_with_animation("Creating Python virtual environment", create_python_venv)

        else:
            print_info("Unsupported OS.")
            sys.exit(1)

    def upgrade_pip(_animated_task: AnimatedTask) -> None:
        run_command([
            "/srv/ansible/venv/bin/python3", "-m", "pip", "install",
            "--no-cache-dir", "--disable-pip-version-check",
            "--upgrade", "pip", "setuptools", "wheel"
        ])

    run_task_with_animation("Upgrading pip, setuptools, and wheel", upgrade_pip)

    def install_requirements(_animated_task: AnimatedTask) -> None:
        run_command([
            "/srv/ansible/venv/bin/python3", "-m", "pip", "install",
            "--no-cache-dir", "--disable-pip-version-check",
            "--upgrade", "--requirement", "/srv/git/sb/requirements-saltbox.txt"
        ])

    run_task_with_animation("Installing Saltbox pip requirements", install_requirements)

    def copy_binaries(_animated_task: AnimatedTask) -> None:
        copy_files([
            "/srv/ansible/venv/bin/ansible*",
            "/srv/ansible/venv/bin/certbot",
            "/srv/ansible/venv/bin/apprise"
        ], "/usr/local/bin/")

    run_task_with_animation("Copying Ansible, certbot and apprise binaries to /usr/local/bin", copy_binaries)

    def set_ownership(_animated_task: AnimatedTask) -> None:
        run_command(["chown", "-R", f"{saltbox_user}:{saltbox_user}", ansible_venv_path])

    run_task_with_animation("Setting correct ownership for Ansible venv", set_ownership)

    print_info(f"Done {'recreating' if recreate else 'updating'} Ansible venv.")


def handle_version(_args=None) -> None:
    """
    Print the application version.

    Args:
        _args: Unused argument for consistency with other handlers.
    """
    print(f"Application Version: {__version__}")


def remove_python(_animated_task: AnimatedTask) -> None:
    """
    Remove Python 3.12 packages.

    Args:
        _animated_task: The animated task instance tracking progress.
    """
    packages = [
        "libpython3.12-minimal",
        "python3.12-minimal",
        "libpython3.12",
        "libpython3.12-dev",
        "python3.12",
        "python3.12-dev",
        "python3.12-venv",
        "libpython3.12-stdlib"
    ]

    run_command(["apt", "remove", "-y"] + packages)

def handle_reinstall_python(_arguments):
    """
    Handle the reinstall-python command by removing Python 3.12 and recreating the venv.

    Args:
        _arguments: Unused arguments from the command line parser.
    """
    def detect_os_release(_animated_task: AnimatedTask) -> str:
        return subprocess.check_output(["lsb_release", "-cs"], text=True).strip()

    release = run_task_with_animation("Detecting OS release", detect_os_release)

    if release in ("focal", "jammy"):
        print_info("Removing Python 3.12 packages and recreating Ansible venv.")
        run_task_with_animation("Removing Python 3.12 packages", remove_python)
        manage_ansible_venv(force_recreate=True)
    else:
        print_info("This command is only for Ubuntu 20.04 and 22.04")


def create_parser():
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description='Saltbox command line interface.')

    # Add global --no-update flag
    parser.add_argument('--no-update', action='store_true', help=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(help='Available sub-commands')

    # Update command
    parser_update = subparsers.add_parser('update', help='Update Saltbox and Sandbox')
    add_verbosity_argument(parser_update)
    parser_update.set_defaults(func=handle_update)

    # List command
    parser_list = subparsers.add_parser('list', help='List Saltbox and Sandbox tags')
    parser_list.set_defaults(func=handle_list)

    # Install command
    parser_install = subparsers.add_parser('install', help='Install <tag>')
    parser_install.add_argument('tags', nargs='+', help='Tags to install')
    parser_install.add_argument('--skip-tags', nargs='+', help='Tags to skip, separated by commas', default='')
    add_extra_vars_argument(parser_install)
    add_verbosity_argument(parser_install)
    parser_install.set_defaults(func=handle_install)

    # Bench command
    parser_bench = subparsers.add_parser('bench', help='Run bench.sh')
    parser_bench.set_defaults(func=handle_bench)

    # Diag command
    parser_diag = subparsers.add_parser('diag', help='Run Saltbox diagnose for support')
    parser_diag.set_defaults(func=handle_diag)

    # Recreate-venv command
    parser_recreate_venv = subparsers.add_parser('recreate-venv',
                                                 help='Re-create the Ansible Python Virtual Environment')
    parser_recreate_venv.set_defaults(func=handle_recreate_venv)

    # Reinstall-python command
    parser_reinstall_python = subparsers.add_parser('reinstall-python',
                                                 help='Reinstall Python 3.12 and recreate the Ansible venv')
    parser_reinstall_python.set_defaults(func=handle_reinstall_python)

    # Reinstall-facts command
    parser_reinstall_facts = subparsers.add_parser('reinstall-facts', help='Reinstall the saltbox.fact file')
    parser_reinstall_facts.set_defaults(func=handle_reinstall_fact)

    # Inventory command
    parser_inventory = subparsers.add_parser('inventory', help="Manage inventory 'localhost.yml' file")
    parser_inventory.set_defaults(func=handle_inventory)

    # Branch command
    parser_branch = subparsers.add_parser('branch', help='Change the branch of the Saltbox repository')
    parser_branch.add_argument('branch_name', type=str,
                               help='The name of the branch to switch to in the Saltbox repository')
    add_verbosity_argument(parser_branch)
    parser_branch.set_defaults(func=handle_branch)

    # Sandbox-branch command
    parser_sandbox_branch = subparsers.add_parser('sandbox-branch', help='Change the branch of the Sandbox repository')
    parser_sandbox_branch.add_argument('branch_name', type=str,
                                       help='The name of the branch to switch to in the Sandbox repository')
    add_verbosity_argument(parser_sandbox_branch)
    parser_sandbox_branch.set_defaults(func=handle_sandbox_branch)

    # Version command
    parser_handle_version = subparsers.add_parser('version', help='Report the version of the binary')
    parser_handle_version.set_defaults(func=handle_version)

    # Add the --version argument to the main parser
    parser.add_argument('--version', action='store_true', help='Report the version of the binary')

    return parser


def add_extra_vars_argument(arg_parser):
    """Add extra variables argument to a parser."""
    arg_parser.add_argument('-e', '--extra-vars', action='append', help='Extra variables', default=[])


def add_verbosity_argument(arg_parser):
    """Add verbosity argument to a parser."""
    arg_parser.add_argument('-v', '--verbose', action='count', help='Ansible Verbosity', default=0)


def main():
    """Main function to parse arguments and execute commands."""
    global saltbox_user

    saltbox_user = get_saltbox_user()
    relaunch_as_root()

    parser = create_parser()
    args = parser.parse_args()

    if not args.no_update:
        check_and_update_repo(SB_REPO_PATH)

    if args.version:
        handle_version()
    elif hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
