#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

usage() {
  echo "Usage: $0 <command> [args]"
  echo ""
  echo "Commands:"
  echo "  setup               First time setup of the project"
  echo "  build               Build the project"
  echo "  run <spec> <style>  Generate workload and run db_runner"
  echo "  clear               Delete build artifacts and output files"
  exit 1
}

if [ $# -lt 1 ]; then
  usage
fi

COMMAND="$1"
shift

case "$COMMAND" in
  setup)
    ./scripts/setup.sh
    ;;

  build)
    git submodule update --recursive

    mkdir -p build

    cmake -S . -B build

    CORES=$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu)
    cmake --build build --parallel "$CORES"

    clear
    echo "build complete!"
    ;;

  run)
    if [ $# -lt 2 ]; then
      echo "Usage: $0 run <spec_name> <compaction_style>"
      echo "  Example: $0 run w_01 1"
      exit 1
    fi
    
    SPEC="$1"
    C_FLAG="$2"

    ./bin/tectonic-cli generate -w "workloads/${SPEC}.spec.json" -o workload.txt
    ./bin/db_runner -C "$C_FLAG" -T 4 -E 64 --peroptime 1
    ;;

  clear)
    rm -rf bin build db
    rm -f workload.txt *.log
    ;;

  *)
    echo "Unknown command: $COMMAND"
    usage
    ;;
esac
