#!/bin/bash

CURRENT_DIR=$(dirname $0)

pushd ${CURRENT_DIR}

python3 -m venv venv
venv/bin/pip install -r requirements.txt

firejail --profile=$(pwd)/firejail.profile --read-write=$(pwd) --read-only=$(pwd)/firejail.profile --noexec=$(pwd)/extensions venv/bin/python main.py

popd