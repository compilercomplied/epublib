from typing import List
from epublib.reader import Epub, read
from tests.data.minimal_constants import (
    MINIMAL_CHAPTER1,
    MINIMAL_CHAPTER2,
    MINIMAL_COPYRIGHT,
    MINIMAL_TOC,
)
from tests.filesystem import open_test_file


def test_ok():
    filestream = open_test_file("minimal.epub")
    epubs: List[Epub] = read(filestream)

    assert len(epubs) == 1

    texts = epubs[0].texts

    assert texts[0] == MINIMAL_TOC
    assert texts[1] == MINIMAL_CHAPTER1
    assert texts[2] == MINIMAL_CHAPTER2
    assert texts[3] == MINIMAL_COPYRIGHT

    contents = epubs[0].dump_contents()

    expected_contents = (
        MINIMAL_TOC + MINIMAL_CHAPTER1 + MINIMAL_CHAPTER2 + MINIMAL_COPYRIGHT
    )

    assert contents == expected_contents
