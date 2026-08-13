#!/usr/bin/env bash
# Install build dependencies and create the Python environment used by the RL
# server and graph generator. Ubuntu/Debian only.
set -Eeuo pipefail

PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$PIPELINE_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck source=config.sh
source "$PIPELINE_DIR/config.sh"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script supports Ubuntu/Debian (apt-get) only." >&2
  exit 1
fi

sudo_prefix=()
if [[ "$(id -u)" -ne 0 ]]; then
  sudo_prefix=("${SUDO_COMMAND:-sudo}")
fi

echo "[system] installing compiler, CMake, gflags, and RocksDB dependencies"
"${sudo_prefix[@]}" apt-get update
"${sudo_prefix[@]}" apt-get install -y \
  build-essential \
  ca-certificates \
  cmake \
  git \
  libbz2-dev \
  libgflags-dev \
  liblz4-dev \
  libsnappy-dev \
  libzstd-dev \
  ninja-build \
  pkg-config \
  python3 \
  python3-pip \
  python3-venv \
  zlib1g-dev

echo "[python] creating $PYTHON_VENV"
python3 -m venv "$PYTHON_VENV"
"$PYTHON_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$PYTHON_VENV/bin/python" -m pip install -r rl_agent/requirements.txt matplotlib

echo
echo "Dependencies installed. Next: scripts/dbbench_pipeline/01_build_rocksdb.sh"
