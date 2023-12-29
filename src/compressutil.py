import os
import pathlib
import tarfile
from typing import Union

def compress_directory(path: Union[str, pathlib.Path], filename: Union[str, pathlib.Path]):
    with tarfile.open(filename, "w:gz") as tar:
        for fn in os.listdir(path):
            p = os.path.join(path, fn)
            tar.add(p, arcname=fn)
