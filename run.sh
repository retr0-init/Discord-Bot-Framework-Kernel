#!/bin/bash

# The script to run the bot in a sandboxed environment
#
# Copyright (C) 2024  __retr0.init__
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# 

CURRENT_DIR=$(dirname $0)

pushd ${CURRENT_DIR}

python3 -m venv venv
venv/bin/pip install -r requirements.txt

firejail --profile=$(pwd)/firejail.profile --read-write=$(pwd) --read-only=$(pwd)/firejail.profile --noexec=$(pwd)/extensions venv/bin/python main.py

popd