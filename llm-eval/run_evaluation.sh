#!/bin/bash
# Theon RAG Evaluation Runner
#
# Usage:
#   ./run_evaluation.sh                    # Run full evaluation (both modes)
#   ./run_evaluation.sh --single           # Run single question test
#   ./run_evaluation.sh --with-collection  # Run only with-collection mode
#   ./run_evaluation.sh --no-collection    # Run only no-collection mode

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default config
export THEON_API_URL="${THEON_API_URL:-http://localhost:8080}"
export DATASET_FILE="${DATASET_FILE:-./evaluation_dataset.json}"
export OUTPUT_DIR="${OUTPUT_DIR:-./evaluation_results}"
export GREENPT_API_KEY="${GREENPT_API_KEY:?GREENPT_API_KEY environment variable is required}"
export GREENPT_API_URL="${GREENPT_API_URL:-https://api.greenpt.ai/v1}"

# Get Theon token if not set
get_token() {
    if [[ -z "${THEON_API_TOKEN}" ]]; then
        echo "Getting Theon API token..."
        
        if [[ -z "${THEON_EMAIL}" || -z "${THEON_PASSWORD}" ]]; then
            echo "Error: Set THEON_API_TOKEN or both THEON_EMAIL and THEON_PASSWORD"
            exit 1
        fi
        
        export THEON_API_TOKEN=$(curl -s -X POST "${THEON_API_URL}/api/auths/signin" \
            -H "Content-Type: application/json" \
            -d "{\"email\": \"${THEON_EMAIL}\", \"password\": \"${THEON_PASSWORD}\"}" \
            | jq -r '.token')
        
        if [[ -z "${THEON_API_TOKEN}" || "${THEON_API_TOKEN}" == "null" ]]; then
            echo "Error: Failed to get token. Check credentials."
            exit 1
        fi
        echo "Token acquired."
    fi
}

check_dependencies() {
    if ! command -v python3 &> /dev/null; then
        echo "Error: python3 not found"
        exit 1
    fi
    
    local missing=false
    
    # Check core dependencies
    if ! python3 -c "import numpy, requests" 2>/dev/null; then
        missing=true
    fi
    
    # Check TruLens
    if ! python3 -c "import trulens_eval" 2>/dev/null; then
        missing=true
    fi
    
    # Check RAGAS
    if ! python3 -c "import ragas" 2>/dev/null; then
        missing=true
    fi
    
    if [[ "$missing" == "true" ]]; then
        echo "Installing Python dependencies..."
        pip install -r "${SCRIPT_DIR}/requirements.txt"
    else
        echo "All dependencies already installed."
    fi
}

show_help() {
    cat << EOF
Theon RAG Evaluation Runner

Usage:
  $0                       Run full evaluation (both modes, all questions)
  $0 --single              Run single question test (both modes)
  $0 --with-collection     Run only with-collection mode
  $0 --no-collection       Run only no-collection mode
  $0 --help                Show this help

Environment variables:
  THEON_API_URL      Theon API URL (default: http://localhost:8080)
  THEON_API_TOKEN    Bearer token (or set THEON_EMAIL + THEON_PASSWORD)
  THEON_EMAIL        Email for auto-login
  THEON_PASSWORD     Password for auto-login
  GREENPT_API_KEY    GreenPT API key
  DATASET_FILE       Path to evaluation_dataset.json
  OUTPUT_DIR         Output directory for results

Examples:
  # Quick test with one question
  export THEON_EMAIL="your@email.com"
  export THEON_PASSWORD="yourpassword"
  $0 --single

  # Full evaluation
  $0
EOF
}

run_evaluation() {
    local mode="$1"
    local single="$2"
    
    local args=""
    if [[ "$mode" == "with" ]]; then
        args="--with-collection"
    else
        args="--no-collection"
    fi
    
    if [[ "$single" == "true" ]]; then
        args="$args --single"
    fi
    
    echo ""
    echo "=========================================="
    echo "Running: $mode collection mode"
    echo "=========================================="
    python3 evaluate_pipeline.py $args
}

# Main
main() {
    local run_with=true
    local run_no=true
    local single=false
    
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --with-collection)
                run_no=false
                shift
                ;;
            --no-collection)
                run_with=false
                shift
                ;;
            --single)
                single=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                echo "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    check_dependencies
    get_token
    
    echo "Theon RAG Evaluation"
    echo "===================="
    echo "API URL: ${THEON_API_URL}"
    echo "Dataset: ${DATASET_FILE}"
    echo "Output:  ${OUTPUT_DIR}"
    
    if [[ "$run_with" == "true" ]]; then
        run_evaluation "with" "$single"
    fi
    
    if [[ "$run_no" == "true" ]]; then
        run_evaluation "no" "$single"
    fi
    
    echo ""
    echo "=========================================="
    echo "All evaluations complete!"
    echo "Results in: ${OUTPUT_DIR}"
    echo "=========================================="
}

main "$@"
