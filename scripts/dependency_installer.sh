#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  cat <<'USAGE'
Usage:
  scripts/install_dependencies.sh [options]

Installs the system, Rust, and Python dependencies needed to build and run
the RocksDB db_runner, Tectonic workload generator, RL agent, and result plots.

Supported systems:
  Ubuntu/Debian Linux
  macOS with Homebrew

Options:
  --no-system          Skip OS package installation.
  --no-rust            Skip rustup/Rust nightly installation.
  --no-python          Skip Python virtualenv/package installation.
  --no-submodules      Skip git submodule initialization.
  --venv PATH          Python virtualenv path. Default: .venv
  -h, --help           Show this help.

Environment overrides:
  VENV_DIR             Same as --venv.
  SUDO                 Privilege command for system packages. Default: sudo.

Examples:
  scripts/install_dependencies.sh

  scripts/install_dependencies.sh --venv .venv

  scripts/install_dependencies.sh --no-system
USAGE
}

require_option_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "Missing value for $option" >&2
    exit 1
  fi
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command missing after installation step: $command_name" >&2
    exit 1
  fi
}

SKIP_SYSTEM=0
SKIP_RUST=0
SKIP_PYTHON=0
SKIP_SUBMODULES=0
VENV_DIR="${VENV_DIR:-.venv}"
SUDO="${SUDO:-sudo}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --no-system)
      SKIP_SYSTEM=1
      shift
      ;;
    --no-rust)
      SKIP_RUST=1
      shift
      ;;
    --no-python)
      SKIP_PYTHON=1
      shift
      ;;
    --no-submodules)
      SKIP_SUBMODULES=1
      shift
      ;;
    --venv)
      require_option_value "$1" "${2:-}"
      VENV_DIR="$2"
      shift 2
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      echo "Unexpected positional argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

install_linux_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "[system] installing Ubuntu/Debian packages"
    $SUDO apt-get update
    $SUDO apt-get install -y \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      git \
      libbz2-dev \
      libgflags-dev \
      libjemalloc-dev \
      liblz4-dev \
      libnuma-dev \
      libsnappy-dev \
      libzstd-dev \
      ninja-build \
      pkg-config \
      python3 \
      python3-pip \
      python3-venv \
      zlib1g-dev
  else
    echo "Unsupported Linux package manager. Install dependencies manually or use --no-system." >&2
    exit 1
  fi
}

install_macos_packages() {
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "[system] installing Xcode Command Line Tools"
    xcode-select --install
  fi

  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required on macOS. Install it first: https://brew.sh" >&2
    exit 1
  fi

  echo "[system] installing Homebrew packages"
  brew update
  brew install \
    bzip2 \
    cmake \
    gflags \
    git \
    jemalloc \
    lz4 \
    ninja \
    pkg-config \
    python \
    snappy \
    zlib \
    zstd
}

install_system_packages() {
  case "$(uname -s)" in
    Linux)
      install_linux_packages
      ;;
    Darwin)
      install_macos_packages
      ;;
    *)
      echo "Unsupported OS: $(uname -s)" >&2
      exit 1
      ;;
  esac
}

install_rust() {
  if ! command -v rustup >/dev/null 2>&1; then
    echo "[rust] installing rustup"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  fi

  # shellcheck source=/dev/null
  if [[ -f "$HOME/.cargo/env" ]]; then
    source "$HOME/.cargo/env"
  fi

  require_command rustup
  echo "[rust] installing/updating nightly toolchain"
  rustup toolchain install nightly
  rustup default nightly
}

install_python_packages() {
  require_command python3
  echo "[python] creating virtualenv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"

  # shellcheck source=/dev/null
  source "${VENV_DIR}/bin/activate"

  python -m pip install --upgrade pip setuptools wheel
  python -m pip install -r rl_agent/requirements.txt
  python -m pip install matplotlib
}

init_submodules() {
  require_command git
  echo "[submodules] updating recursive submodules"
  git submodule update --init --recursive
}

if [[ "$SKIP_SYSTEM" == "1" ]]; then
  echo "[system] skipped"
else
  install_system_packages
fi

if [[ "$SKIP_RUST" == "1" ]]; then
  echo "[rust] skipped"
else
  install_rust
fi

if [[ "$SKIP_PYTHON" == "1" ]]; then
  echo "[python] skipped"
else
  install_python_packages
fi

if [[ "$SKIP_SUBMODULES" == "1" ]]; then
  echo "[submodules] skipped"
else
  init_submodules
fi

echo
echo "Dependency installation complete."
echo "Next build command:"
echo "  scripts/artifact_builder.sh --jobs \$(nproc)"
