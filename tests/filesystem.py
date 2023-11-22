
import io
import pathlib
import os
from typing import Any, IO
from functools import cache, lru_cache

DATA_DIR: str = "data"

@cache
def get_data_dir() -> pathlib.Path:
    testdir: pathlib.Path = pathlib.Path(__file__).parent.resolve()
    datadir: str = os.path.join(testdir, DATA_DIR)
    return pathlib.Path(datadir).resolve()

@lru_cache
def open_test_file(filename: str) -> IO[Any]:
    datadir: pathlib.Path = get_data_dir()
    filepath: str = os.path.join(datadir, filename)

    return io.open(file=filepath, mode='br')

