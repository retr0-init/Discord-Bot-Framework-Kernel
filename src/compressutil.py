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
