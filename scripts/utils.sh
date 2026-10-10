#!/usr/bin/env bash
################################################################################
# Utility Functions for Service Management
# Provides logging, formatting, and process management helpers
################################################################################

# Color definitions for formatted output
BLACK='\033[0;30m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
BOLD='\033[1m'
RESET='\033[0m'

# Logging functions with timestamps and formatting
log_header() {
    local message="$1"
    echo -e "\n${CYAN}${BOLD}═══════════════════════════════════════════════════════════${RESET}"
    echo -e "${CYAN}${BOLD}  $message${RESET}"
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════════════${RESET}\n"
}

log_info() {
    local message="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${BLUE}[INFO]${RESET} ${timestamp} - ${message}"
}

log_success() {
    local message="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[✓]${RESET} ${timestamp} - ${message}"
}

log_error() {
    local message="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[✗]${RESET} ${timestamp} - ${message}" >&2
}

log_warning() {
    local message="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[!]${RESET} ${timestamp} - ${message}"
}

log_debug() {
    local message="$1"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    if [ "${DEBUG_MODE:-false}" = "true" ]; then
        echo -e "${MAGENTA}[DEBUG]${RESET} ${timestamp} - ${message}"
    fi
}

