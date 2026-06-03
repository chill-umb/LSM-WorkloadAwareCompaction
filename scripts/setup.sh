#!/usr/bin/env bash
set -e

OS="$(uname)"

if [[ "$OS" == "Linux" ]]; then
    sudo apt-get update -y
    sudo apt-get install -y build-essential cmake libgflags-dev curl git

elif [[ "$OS" == "Darwin" ]]; then
    if ! xcode-select -p &>/dev/null; then
        xcode-select --install
    fi

    if ! command -v brew &>/dev/null; then
        exit 1
    fi

    brew update
    brew install cmake gflags git curl

else
    exit 1
fi

if ! command -v rustup &>/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi

source "$HOME/.cargo/env"
rustup default nightly

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

git submodule update --init --recursive