"""
Tarball Compress Util functions

Copyright (C) 2024  __retr0.init__

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""

import os
import pathlib
import tarfile
from typing import Union

def compress_directory(path: Union[str, pathlib.Path], filename: Union[str, pathlib.Path]) -> None:
    def compress_filter(tarinfo: tarfile.TarInfo) -> Union[tarfile.TarInfo, None]:
        '''
        Exclude the dot files that contains secrets and git information, virtual environment and runtime files
        '''
        name_determine = lambda x: x[0] == '.' or x == 'venv' or x == '__pycache__'
        name: str = tarinfo.name
        name_list: list[str] = name.split('/')
        if not any(map(name_determine, name_list)):
            return tarinfo
    with tarfile.open(filename, "w:gz") as tar:
        for fn in os.listdir(path):
            p = os.path.join(path, fn)
            tar.add(p, arcname=fn, filter=compress_filter)
