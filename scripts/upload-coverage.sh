#!/usr/bin/env bash

set -euo pipefail
set -x

# Install coverage.
if [[ -z ${TOXENV+x} || -z $TOXENV ]]; then
  python -m pip install coverage
else
  # Add last TOXENV to $PATH.
  PATH="$PWD/.tox/${TOXENV##*,}/bin:$PATH"
fi

# Run coverage.
python -m coverage xml

# Download and verify latest Codecov bash uploader.
# Set --connect-timeout to work around https://github.com/curl/curl/issues/4461
curl --silent --show-error --location --connect-timeout 5 --retry 6 -o codecov https://codecov.io/bash
VERSION=$(grep --only-matching 'VERSION=\"[0-9\.]*\"' codecov | cut -d'"' -f2)
if command -v sha256sum; then
    sha256sum --check --strict --ignore-missing --quiet <(curl --silent "https://raw.githubusercontent.com/codecov/codecov-bash/${VERSION}/SHA256SUM")
else
    shasum --algorithm 256 --check --strict --ignore-missing --quiet <(curl --silent "https://raw.githubusercontent.com/codecov/codecov-bash/${VERSION}/SHA256SUM")
fi

# Upload coverage.
bash codecov -Z -X fix -f coverage.xml "$@"
