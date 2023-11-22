import os
import pathlib
import html2text
from lxml import etree
from typing import Any, IO, Dict, List, Optional
from stream_unzip import stream_unzip
from dataclasses import dataclass

xml_parser_singleton: etree.XMLParser = etree.XMLParser(
    recover=True, resolve_entities=True
)

NAMESPACES = {
    "XML": "http://www.w3.org/XML/1998/namespace",
    "EPUB": "http://www.idpf.org/2007/ops",
    "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
    "OPF": "http://www.idpf.org/2007/opf",
    "CONTAINERS": "urn:oasis:names:tc:opendocument:xmlns:container",
    "DC": "http://purl.org/dc/elements/1.1/",
    "XHTML": "http://www.w3.org/1999/xhtml",
}
CONTENT_FILETYPES = {".xhtml", ".xml", ".opf"}


class Epub:
    def __init__(self, texts: List[str]):
        self.texts = texts

    def dump_contents(self) -> str:
        epub_text = ""
        for text in self.texts:
            epub_text += text

        return epub_text


class MalformedEpubException(Exception):
    pass


@dataclass
class UncompressedEpub:
    rootfiles: List[str]
    files: Dict[str, bytearray]


def decode(data: bytearray, encoding: str = "utf-8") -> str:
    return data.decode(encoding)


def normalize_path(
    rootfiles: List[str], files: Dict[str, bytearray]
) -> Dict[str, bytearray]:
    # We keep track of filepaths anchored to our uncompressed root directory,
    # which might be different than our epub root directory.
    # The rootpath obtained from container.xml is relative to the epub
    # directory, so we have to find whether both of our roots are the same or
    # not and normalize when they differ so we can easily follow links
    # embedded in our epub skeleton.
    #
    # The following file tree might serve as an example:
    # uncompressed_root
    # ├── actual_epub_root
    # │   ├── EPUB_FILES
    # │   │   └── epub_content.opf
    # │   ├── META-INF
    # │   │   └── container.xml
    # │   └── mimetype
    # └── random_directory
    #     └── unimportant_file
    #
    # Our extracted root path will point to EPUB_FILES/epub_content.opf, but
    # this path won't be valid from the uncompressed_root perspective.
    #
    # I couldn't find any useful information but it seems this structure was a
    # non-enforced recommendation in the past and was removed from the spec.
    # Nevertheless, this is a valid structure and so we have to support it.
    # https://github.com/w3c/epub-specs/issues/1177

    # Validating on the first one should be enough, we only want to normalize
    # the path to our OCF root, which should be the same in multipublications
    # (that is, OCFs with multiple rootfiles).
    if rootfiles[0] in files:
        return files

    normalized_dict: Dict[str, bytearray] = dict()

    for path in files:
        # Epubs follow the OpenContainerFormat spec. This spec defines the
        # compressed files to adhere to the Zip spec.
        # Zip spec explicitly demands forward slashes '/' for our path.
        path_arr = path.split("/")
        if len(path_arr) == 1:
            # We do not care about files sitting in our uncompressed root
            # directory.
            continue

        normalized_path = "/".join(path_arr[1:])
        normalized_dict[normalized_path] = files[path]

    return normalized_dict


def extract_root_path(container_file: bytearray) -> List[str]:
    tree = parse_as_etree(container_file)

    root_files = tree.findall(
        path=".//xmlns:rootfile[@media-type]",
        namespaces={"xmlns": NAMESPACES["CONTAINERS"]},
    )

    rootfiles: List[str] = []
    for root_file in root_files:
        mediatype = root_file.get("media-type")
        fullpath = root_file.get("full-path")
        if mediatype == "application/oebps-package+xml" and fullpath is not None:
            rootfiles.append(fullpath)

    return rootfiles


def read_unzipped_chunks(file_chunks, is_textfile: bool) -> Optional[bytearray]:
    file_bytes: Optional[bytearray] = None
    for chunk in file_chunks:
        # Whether we keep the bytes or not we still have to iterate over
        # all the content to avoid corrupting the file. When this happens
        # stream_unzip raises `UnfinishedIterationError`.
        if not is_textfile:
            continue

        if file_bytes is None:
            file_bytes = bytearray(chunk)
        else:
            file_bytes += bytearray(chunk)

    return file_bytes


def uncompress_epub(stream: IO[Any]) -> UncompressedEpub:
    # 'filepath' to 'binary contents' map.
    files: Dict[str, bytearray] = dict()
    rootfiles: List[str] = []

    for file_path, file_size, unzipped_chunks in stream_unzip(stream):
        filepath: str = decode(file_path)
        filename: str = os.path.basename(filepath)
        file_ext: str = pathlib.Path(filepath).suffix

        # Skip files that don't have any text in them like css stylesheets
        # or images.
        is_textfile: bool = file_ext in CONTENT_FILETYPES

        current_bytes: Optional[bytearray] = read_unzipped_chunks(
            unzipped_chunks, is_textfile
        )

        if current_bytes is None:
            continue

        if filename == "container.xml":
            rootfiles = extract_root_path(current_bytes)
        else:
            files[filepath] = current_bytes

    if len(rootfiles) == 0:
        msg = "No root file found in META-INF/container.xml definition."
        msg += " (Epub is not correctly packaged, unable to find content)"
        raise MalformedEpubException(msg)

    normalized_files = normalize_path(rootfiles=rootfiles, files=files)
    return UncompressedEpub(files=normalized_files, rootfiles=rootfiles)


def parse_as_etree(xml_data: bytearray, encoding: str = "utf-8") -> etree._Element:
    """
    Pre-processes XML so lxml won't raise an exception if the XML has an
    encoding tag in it.
    """
    xml_as_string = decode(xml_data, encoding)
    lxml_friendly_encoded_data = xml_as_string.encode(encoding)
    return etree.fromstring(
        text=lxml_friendly_encoded_data, parser=xml_parser_singleton
    )


def fix_mediatype(mediatype: Optional[str]) -> Optional[str]:
    """
    Override common mistakes.
    """
    if mediatype == "image/jpg":
        return "image/jpeg"
    else:
        return mediatype


def extract_textfiles(
    rootfile: bytearray, files: Dict[str, bytearray], root_dir: str
) -> Epub:
    tree = parse_as_etree(rootfile)
    manifest = tree.find("{%s}%s" % (NAMESPACES["OPF"], "manifest"))

    if manifest is None:
        raise MalformedEpubException("Rootfile has no manifest")

    texts: List[str] = []
    for item in manifest.iter():
        if item.tag != "{%s}item" % NAMESPACES["OPF"]:
            continue

        media_type = fix_mediatype(item.get("media-type", None))

        if media_type == "application/xhtml+xml":
            filepath: str | None = item.get("href", None)
            if filepath is None:
                continue

            # Our path here is relative to our root directory so we have to
            # prepend it.
            normalized_path: str = f"{root_dir}/{filepath}"
            contents: bytearray = files[normalized_path]

            str_data = decode(contents)
            texts.append(html2text.html2text(str_data))

    return Epub(texts=texts)


def read_uncompressed_epubs(uncompressed_epub: UncompressedEpub) -> List[Epub]:
    publications: List[Epub] = []
    for rootfile_path in uncompressed_epub.rootfiles:
        rootfile_dir: str = rootfile_path.split("/")[0]
        rootfile: bytearray = uncompressed_epub.files[rootfile_path]
        epub = extract_textfiles(rootfile, uncompressed_epub.files, rootfile_dir)

        publications.append(epub)

    return publications


def read(stream: IO[Any]) -> List[Epub]:
    uncompressed_epub = uncompress_epub(stream)

    epubs = read_uncompressed_epubs(uncompressed_epub)

    return epubs
