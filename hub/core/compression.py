from logging import warning
import hub
from hub.util.exceptions import (
    SampleCompressionError,
    SampleDecompressionError,
    UnsupportedCompressionError,
    CorruptedSampleError,
)
from hub.compression import (
    get_compression_type,
    BYTE_COMPRESSION,
    IMAGE_COMPRESSION,
    VIDEO_COMPRESSION,
    AUDIO_COMPRESSION,
)
from typing import Union, Tuple, Sequence, List, Optional, BinaryIO
import numpy as np
from pathlib import Path
from PIL import Image, UnidentifiedImageError  # type: ignore
from io import BytesIO
import mmap
import struct
import sys
import re
import numcodecs.lz4  # type: ignore

try:
    from miniaudio import (  # type: ignore
        mp3_read_file_f32,
        mp3_read_f32,
        mp3_get_file_info,
        mp3_get_info,
        flac_read_file_f32,
        flac_read_f32,
        flac_get_file_info,
        flac_get_info,
        wav_read_file_f32,
        wav_read_f32,
        wav_get_file_info,
        wav_get_info,
    )

    _MINIAUDIO_INSTALLED = True
except ImportError:
    _MINIAUDIO_INSTALLED = False
from numpy.core.fromnumeric import compress  # type: ignore
import math

try:
    import av  # type: ignore

    _PYAV_INSTALLED = True
except ImportError:
    _PYAV_INSTALLED = False

try:
    import lz4.frame  # type: ignore

    _LZ4_INSTALLED = True
except ImportError:
    _LZ4_INSTALLED = False


if sys.byteorder == "little":
    _NATIVE_INT32 = "<i4"
    _NATIVE_FLOAT32 = "<f4"
else:
    _NATIVE_INT32 = ">i4"
    _NATIVE_FLOAT32 = ">f4"

DIMS_RE = re.compile(rb" ([0-9]+)x([0-9]+)")
FPS_RE = re.compile(rb" ([0-9]+) fps,")
DURATION_RE = re.compile(rb"Duration: ([0-9:.]+),")
INFO_RE = re.compile(rb"([a-z]+)=([0-9./]+)")

_JPEG_SOFS = [
    b"\xff\xc0",
    b"\xff\xc2",
    b"\xff\xc1",
    b"\xff\xc3",
    b"\xff\xc5",
    b"\xff\xc6",
    b"\xff\xc7",
    b"\xff\xc9",
    b"\xff\xca",
    b"\xff\xcb",
    b"\xff\xcd",
    b"\xff\xce",
    b"\xff\xcf",
    b"\xff\xde",
    # Skip:
    b"\xff\xcc",
    b"\xff\xdc",
    b"\xff\xdd",
    b"\xff\xdf",
    # App: (0xFFE0 - 0xFFEF):
    *map(lambda x: x.to_bytes(2, "big"), range(0xFFE0, 0xFFF0)),
    # DQT:
    b"\xff\xdb",
    # COM:
    b"\xff\xfe",
    # Start of scan
    b"\xff\xda",
]

_JPEG_SKIP_MARKERS = set(_JPEG_SOFS[14:])
_JPEG_SOFS_RE = re.compile(b"|".join(_JPEG_SOFS))
_STRUCT_HHB = struct.Struct(">HHB")
_STRUCT_II = struct.Struct(">ii")


def to_image(array: np.ndarray) -> Image:
    shape = array.shape
    if len(shape) == 3 and shape[0] != 1 and shape[2] == 1:
        # convert (X,Y,1) grayscale to (X,Y) for pillow compatibility
        return Image.fromarray(array.squeeze(axis=2))

    return Image.fromarray(array)


def _compress_apng(array: np.ndarray) -> bytes:
    if array.ndim == 3:
        # Binary APNG
        frames = list(
            map(Image.fromarray, (array[:, :, i] for i in range(array.shape[2])))
        )
    elif array.ndim == 4 and array.shape[3] <= 4:
        # RGB(A) APNG
        frames = list(map(Image.fromarray, array))
    else:
        raise SampleCompressionError(array.shape, "apng", "Unexpected shape.")
    out = BytesIO()
    frames[0].save(out, "png", save_all=True, append_images=frames[1:])
    out.seek(0)
    ret = out.read()
    out.close()
    return ret


def _decompress_apng(buffer: Union[bytes, memoryview]) -> np.ndarray:
    img = Image.open(BytesIO(buffer))
    frame0 = np.array(img)
    if frame0.ndim == 2:
        ret = np.zeros(frame0.shape + (img.n_frames,), dtype=frame0.dtype)
        ret[:, :, 0] = frame0
        for i in range(1, img.n_frames):
            img.seek(i)
            ret[:, :, i] = np.array(img)
    else:
        ret = np.zeros((img.n_frames,) + frame0.shape, dtype=frame0.dtype)
        ret[0] = frame0
        for i in range(1, img.n_frames):
            img.seek(i)
            ret[i] = np.array(img)
    return ret


def compress_bytes(
    buffer: Union[bytes, memoryview], compression: Optional[str]
) -> bytes:
    if not buffer:
        return b""
    if compression == "lz4":
        if not buffer:
            return b""
        return numcodecs.lz4.compress(buffer)
    else:
        raise SampleCompressionError(
            (len(buffer),), compression, f"Not a byte compression: {compression}"
        )


def decompress_bytes(
    buffer: Union[bytes, memoryview], compression: Optional[str]
) -> bytes:
    if not buffer:
        return b""
    if compression == "lz4":
        # weird edge case of lz4 + empty string
        if buffer == b"\x00\x00\x00\x00\x00":
            return b""
        if (
            buffer[:4] == b'\x04"M\x18'
        ):  # python-lz4 magic number (backward compatiblity)
            if not _LZ4_INSTALLED:
                raise ModuleNotFoundError(
                    "Module lz4 not found. Install using `pip install lz4`."
                )
            return lz4.frame.decompress(buffer)
        return numcodecs.lz4.decompress(buffer)
    else:
        raise SampleDecompressionError()


def compress_array(array: np.ndarray, compression: Optional[str]) -> bytes:
    """Compress some numpy array using `compression`. All meta information will be contained in the returned buffer.

    Note:
        `decompress_array` may be used to decompress from the returned bytes back into the `array`.

    Args:
        array (np.ndarray): Array to be compressed.
        compression (str, optional): `array` will be compressed with this compression into bytes. Right now only arrays compatible with `PIL` will be compressed.

    Raises:
        UnsupportedCompressionError: If `compression` is unsupported. See `hub.compressions`.
        SampleCompressionError: If there was a problem compressing `array`.
        NotImplementedError: If compression is not supported.

    Returns:
        bytes: Compressed `array` represented as bytes.
    """

    # empty sample shouldn't be compressed

    if 0 in array.shape:
        return bytes()

    if compression not in hub.compressions:
        raise UnsupportedCompressionError(compression)

    if compression is None:
        return array.tobytes()

    compr_type = get_compression_type(compression)

    if compr_type == BYTE_COMPRESSION:
        return compress_bytes(array.tobytes(), compression)
    elif compr_type == AUDIO_COMPRESSION:
        raise NotImplementedError(
            "In order to store audio data, you should use `hub.read(path_to_file)`. Compressing raw data is not yet supported."
        )
    elif compr_type == VIDEO_COMPRESSION:
        raise NotImplementedError(
            "In order to store video data, you should use `hub.read(path_to_file)`. Compressing raw data is not yet supported."
        )

    if compression == "apng":
        return _compress_apng(array)
    try:
        img = to_image(array)
        out = BytesIO()
        out._close = out.close  # type: ignore
        out.close = (  # type: ignore
            lambda: None
        )  # sgi save handler will try to close the stream (see https://github.com/python-pillow/Pillow/pull/5645)
        kwargs = {"sizes": [img.size]} if compression == "ico" else {}
        img.save(out, compression, **kwargs)
        out.seek(0)
        compressed_bytes = out.read()
        out._close()  # type: ignore
        return compressed_bytes
    except (TypeError, OSError) as e:
        raise SampleCompressionError(array.shape, compression, str(e))


def decompress_array(
    buffer: Union[bytes, memoryview, str],
    shape: Optional[Tuple[int, ...]] = None,
    dtype: Optional[str] = None,
    compression: Optional[str] = None,
    start_idx: Optional[int] = None,
    end_idx: Optional[int] = None,
    step: Optional[int] = None,
    reverse: bool = False,
) -> np.ndarray:
    """Decompress some buffer into a numpy array. It is expected that all meta information is
    stored inside `buffer`.

    Note:
        `compress_array` may be used to get the `buffer` input.

    Args:
        buffer (bytes, memoryview, str): Buffer or file to be decompressed. It is assumed all meta information required to
            decompress is contained within `buffer`, except for byte compressions
        shape (Tuple[int], Optional): Desired shape of decompressed object. Reshape will attempt to match this shape before returning.
        dtype (str, Optional): Applicable only for byte compressions. Expected dtype of decompressed array.
        compression (str, Optional): Applicable only for byte compressions. Compression used to compression the given buffer.
        start_idx: (int, Optional): Applicable only for video compressions. Index of first frame.
        end_idx: (int, Optional): Applicable only for video compressions. Index of last frame (exclusive).
        step: (int, Optional): Applicable only for video compressions. Step size for seeking.
        reverse (bool): Applicable only for video compressions. Reverses output numpy array if set to True.

    Raises:
        SampleDecompressionError: If decompression fails.
        ValueError: If dtype and shape are not specified for byte compression.

    Returns:
        np.ndarray: Array from the decompressed buffer.
    """
    compr_type = get_compression_type(compression)
    if compr_type == BYTE_COMPRESSION:
        if dtype is None or shape is None:
            raise ValueError("dtype and shape must be specified for byte compressions.")
        try:
            decompressed_bytes = decompress_bytes(buffer, compression)  # type: ignore
            return np.frombuffer(decompressed_bytes, dtype=dtype).reshape(shape)
        except Exception:
            raise SampleDecompressionError()
    elif compr_type == AUDIO_COMPRESSION:
        return _decompress_audio(buffer, compression)
    elif compr_type == VIDEO_COMPRESSION:
        return _decompress_video(buffer, start_idx, end_idx, step, reverse)  # type: ignore

    if compression == "apng":
        return _decompress_apng(buffer)  # type: ignore
    try:
        if shape is not None and 0 in shape:
            return np.zeros(shape, dtype=dtype)
        if not isinstance(buffer, str):
            buffer = BytesIO(buffer)  # type: ignore
        img = Image.open(buffer)  # type: ignore
        arr = np.array(img)
        if shape is not None:
            arr = arr.reshape(shape)
        return arr
    except Exception:
        raise SampleDecompressionError()


def _get_bounding_shape(shapes: Sequence[Tuple[int, ...]]) -> Tuple[int, int, int]:
    """Gets the shape of a bounding box that can contain the given the shapes tiled horizontally."""
    if len(shapes) == 0:
        return (0, 0, 0)
    channels_shape = shapes[0][2:]
    for shape in shapes:
        if shape[2:] != channels_shape:
            raise ValueError(
                "The data can't be compressed as the number of channels doesn't match."
            )
    return (max(s[0] for s in shapes), sum(s[1] for s in shapes)) + channels_shape  # type: ignore


def compress_multiple(
    arrays: Sequence[np.ndarray], compression: Optional[str]
) -> bytes:
    """Compress multiple arrays of different shapes into a single buffer. Used for chunk wise compression.
    The arrays are tiled horizontally and padded with zeros to fit in a bounding box, which is then compressed."""
    if len(arrays) == 0:
        return b""
    dtype = arrays[0].dtype
    for arr in arrays:
        if arr.dtype != dtype:
            raise SampleCompressionError(
                arr.shape,
                compression,
                message="All arrays expected to have same dtype.",
            )
    compr_type = get_compression_type(compression)
    if compr_type == BYTE_COMPRESSION:
        return compress_bytes(
            b"".join(arr.tobytes() for arr in arrays), compression
        )  # Note: shape and dtype info not included
    elif compr_type == AUDIO_COMPRESSION:
        raise NotImplementedError("compress_multiple does not support audio samples.")
    elif compr_type == VIDEO_COMPRESSION:
        raise NotImplementedError("compress_multiple does not support video samples.")
    elif compression == "apng":
        raise NotImplementedError("compress_multiple does not support apng samples.")
    canvas = np.zeros(_get_bounding_shape([arr.shape for arr in arrays]), dtype=dtype)
    next_x = 0
    for arr in arrays:
        canvas[: arr.shape[0], next_x : next_x + arr.shape[1]] = arr
        next_x += arr.shape[1]
    return compress_array(canvas, compression=compression)


def decompress_multiple(
    buffer: Union[bytes, memoryview],
    shapes: Sequence[Tuple[int, ...]],
    dtype: Optional[Union[np.dtype, str]] = None,
    compression: Optional[str] = None,
) -> List[np.ndarray]:
    """Unpack a compressed buffer into multiple arrays."""
    if not buffer:
        return []
    if compression and get_compression_type(compression) == "byte":
        decompressed_buffer = memoryview(decompress_bytes(buffer, compression))
        arrays = []
        itemsize = np.dtype(dtype).itemsize
        for shape in shapes:
            nbytes = int(np.prod(shape) * itemsize)
            arrays.append(
                np.frombuffer(decompressed_buffer[:nbytes], dtype=dtype).reshape(shape)
            )
            decompressed_buffer = decompressed_buffer[nbytes:]
        return arrays
    canvas = decompress_array(buffer)
    arrays = []
    next_x = 0
    for shape in shapes:
        arrays.append(canvas[: shape[0], next_x : next_x + shape[1]])
        next_x += shape[1]
    return arrays


def verify_compressed_file(
    file: Union[str, BinaryIO, bytes, memoryview], compression: str
) -> Union[Tuple[Tuple[int, ...], str], None]:
    """Verify the contents of an image file
    Args:
        file (Union[str, BinaryIO, bytes, memoryview]): Path to the file or file like object or contents of the file
        compression (str): Expected compression of the image file
    """
    if isinstance(file, str):
        file = open(file, "rb")
        close = True
    elif hasattr(file, "read"):
        close = False
        file.seek(0)  # type: ignore
    else:
        close = False
    try:
        if compression == "png":
            return _verify_png(file)
        elif compression == "jpeg":
            return _verify_jpeg(file), "|u1"
        elif get_compression_type(compression) == AUDIO_COMPRESSION:
            return _read_audio_shape(file, compression), "<f4"  # type: ignore
        elif compression in ("mp4", "mkv", "avi"):
            if isinstance(file, (bytes, memoryview, str)):
                return _read_video_shape(file), "|u1"  # type: ignore
        else:
            return _fast_decompress(file)
    except Exception as e:
        raise CorruptedSampleError(compression)
    finally:
        if close:
            file.close()  # type: ignore

    return None


def get_compression(header=None, path=None):
    if path:
        # These formats are recognized by file extension for now
        file_formats = ["mp3", "flac", "wav", "mp4", "mkv", "avi"]
        for fmt in file_formats:
            if str(path).lower().endswith("." + fmt):
                return fmt
    if header:
        if not Image.OPEN:
            Image.init()
        for fmt in Image.OPEN:
            accept = Image.OPEN[fmt][1]
            if accept and accept(header):
                return fmt.lower()
        raise SampleDecompressionError()


def _verify_png(buf):
    if not hasattr(buf, "read"):
        buf = BytesIO(buf)
    img = Image.open(buf)
    img.verify()
    return Image._conv_type_shape(img)


def _verify_jpeg(f):
    if hasattr(f, "read"):
        return _verify_jpeg_file(f)
    return _verify_jpeg_buffer(f)


def _verify_jpeg_buffer(buf: bytes):
    # Start of Image
    mview = memoryview(buf)
    assert buf.startswith(b"\xff\xd8")
    # Look for Start of Frame
    sof_idx = -1
    offset = 0
    while True:
        match = _re_find_first(_JPEG_SOFS_RE, mview[offset:])
        if match is None:
            break
        idx = match.start(0) + offset
        marker = buf[idx : idx + 2]
        if marker == _JPEG_SOFS[-1]:
            break
        offset = idx + int.from_bytes(buf[idx + 2 : idx + 4], "big") + 2
        if marker not in _JPEG_SKIP_MARKERS:
            sof_idx = idx
    if sof_idx == -1:
        raise Exception()

    length = int.from_bytes(mview[sof_idx + 2 : sof_idx + 4], "big")
    assert mview[sof_idx + length + 2 : sof_idx + length + 4] in [
        b"\xff\xc4",
        b"\xff\xdb",
        b"\xff\xdd",
        b"\xff\xda",
    ]  # DHT, DQT, DRI, SOS
    shape = _STRUCT_HHB.unpack(mview[sof_idx + 5 : sof_idx + 10])
    assert buf.find(b"\xff\xd9") != -1
    if shape[-1] in (1, None):
        shape = shape[:-1]
    return shape


def _verify_jpeg_file(f):
    # See: https://dev.exiv2.org/projects/exiv2/wiki/The_Metadata_in_JPEG_files#2-The-metadata-structure-in-JPEG
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    mv = memoryview(mm)
    try:
        soi = f.read(2)
        # Start of Image
        assert soi == b"\xff\xd8"

        # Look for Start of Frame
        sof_idx = -1
        offset = 0
        while True:
            view = mv[offset:]
            match = _re_find_first(_JPEG_SOFS_RE, view)
            view.release()
            if match is None:
                break
            idx = match.start(0) + offset
            marker = mm[idx : idx + 2]
            if marker == _JPEG_SOFS[-1]:
                break
            f.seek(idx + 2)
            offset = idx + int.from_bytes(f.read(2), "big") + 2
            if marker not in _JPEG_SKIP_MARKERS:
                sof_idx = idx
        if sof_idx == -1:
            raise Exception()  # Caught by verify_compressed_file()

        f.seek(sof_idx + 2)
        length = int.from_bytes(f.read(2), "big")
        f.seek(length - 2, 1)
        definition_start = f.read(2)
        assert definition_start in [
            b"\xff\xc4",
            b"\xff\xdb",
            b"\xff\xdd",
            b"\xff\xda",
        ]  # DHT, DQT, DRI, SOS
        f.seek(sof_idx + 5)
        shape = _STRUCT_HHB.unpack(f.read(5))
        # TODO this check is too slow
        assert mm.find(b"\xff\xd9") != -1  # End of Image
        if shape[-1] in (1, None):
            shape = shape[:-1]
        return shape
    finally:
        mv.release()
        mm.close()


def _fast_decompress(buf):
    if not hasattr(buf, "read"):
        buf = BytesIO(buf)
    img = Image.open(buf)
    img.load()
    if img.mode == 1:
        args = ("L",)
    else:
        args = (img.mode,)
    enc = Image._getencoder(img.mode, "raw", args)
    enc.setimage(img.im)
    bufsize = max(65536, img.size[0] * 4)
    while True:
        status, err_code, buf = enc.encode(
            bufsize
        )  # See https://github.com/python-pillow/Pillow/blob/master/src/encode.c#L144
        if err_code:
            break
    if err_code < 0:
        raise Exception()  # caught by verify_compressed_file()
    return Image._conv_type_shape(img)


def read_meta_from_compressed_file(
    file, compression: Optional[str] = None
) -> Tuple[str, Tuple[int], str]:
    """Reads shape, dtype and format without decompressing or verifying the sample."""
    if isinstance(file, (str, Path)):
        f = open(file, "rb")
        isfile = True
        close = True
    elif hasattr(file, "read"):
        f = file
        close = False
        isfile = True
        f.seek(0)
    else:
        isfile = False
        f = file
        close = False
    try:
        if compression is None:
            path = file if isinstance(file, str) else None
            if hasattr(f, "read"):
                compression = get_compression(f.read(32), path)
                f.seek(0)
            else:
                compression = get_compression(f[:32], path)  # type: ignore
        if compression == "jpeg":
            try:
                shape, typestr = _read_jpeg_shape(f), "|u1"
            except Exception:
                raise CorruptedSampleError("jpeg")
        elif compression == "png":
            try:
                shape, typestr = _read_png_shape_and_dtype(f)
            except Exception:
                raise CorruptedSampleError("png")
        elif get_compression_type(compression) == AUDIO_COMPRESSION:
            try:
                shape, typestr = _read_audio_shape(file, compression), "<f4"
            except Exception as e:
                raise CorruptedSampleError(compression)
        elif compression in ("mp4", "mkv", "avi"):
            try:
                shape, typestr = _read_video_shape(file), "|u1"  # type: ignore
            except Exception as e:
                raise CorruptedSampleError(compression)
        else:
            img = Image.open(f) if isfile else Image.open(BytesIO(f))  # type: ignore
            shape, typestr = Image._conv_type_shape(img)
            compression = img.format.lower()
        return compression, shape, typestr  # type: ignore
    finally:
        if close:
            f.close()


def _read_jpeg_shape(f: Union[bytes, BinaryIO]) -> Tuple[int, ...]:
    if hasattr(f, "read"):
        return _read_jpeg_shape_from_file(f)
    return _read_jpeg_shape_from_buffer(f)  # type: ignore


def _re_find_first(pattern, string):
    for match in re.finditer(pattern, string):
        return match


def _read_jpeg_shape_from_file(f) -> Tuple[int, ...]:
    """Reads shape of a jpeg image from file without loading the whole image in memory"""
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY)
    mv = memoryview(mm)
    try:
        # Look for Start of Frame
        sof_idx = -1
        offset = 0
        while True:
            view = mv[offset:]
            match = _re_find_first(_JPEG_SOFS_RE, view)
            view.release()
            if match is None:
                break
            idx = match.start(0) + offset
            marker = mm[idx : idx + 2]
            if marker == _JPEG_SOFS[-1]:
                break
            f.seek(idx + 2)
            offset = idx + int.from_bytes(f.read(2), "big") + 2
            if marker not in _JPEG_SKIP_MARKERS:
                sof_idx = idx
        if sof_idx == -1:
            raise Exception()
        f.seek(sof_idx + 5)
        shape = _STRUCT_HHB.unpack(f.read(5))  # type: ignore
        if shape[-1] in (1, None):
            shape = shape[:-1]
        return shape
    finally:
        mv.release()
        mm.close()


def _read_jpeg_shape_from_buffer(buf: bytes) -> Tuple[int, ...]:
    """Gets shape of a jpeg file from its contents"""
    # Look for Start of Frame
    mv = memoryview(buf)
    sof_idx = -1
    offset = 0
    while True:
        match = _re_find_first(_JPEG_SOFS_RE, mv[offset:])
        if match is None:
            break
        idx = match.start(0) + offset
        marker = buf[idx : idx + 2]
        if marker == _JPEG_SOFS[-1]:
            break
        offset = idx + int.from_bytes(buf[idx + 2 : idx + 4], "big") + 2
        if marker not in _JPEG_SKIP_MARKERS:
            sof_idx = idx
    if sof_idx == -1:
        raise Exception()
    shape = _STRUCT_HHB.unpack(memoryview(buf)[sof_idx + 5 : sof_idx + 10])  # type: ignore
    if shape[-1] in (1, None):
        shape = shape[:-1]
    return shape


def _read_png_shape_and_dtype(f: Union[bytes, BinaryIO]) -> Tuple[Tuple[int, ...], str]:
    """Reads shape and dtype of a png file from a file like object or file contents.
    If a file like object is provided, all of its contents are NOT loaded into memory."""
    if not hasattr(f, "read"):
        f = BytesIO(f)  # type: ignore
    f.seek(16)  # type: ignore
    size = _STRUCT_II.unpack(f.read(8))[::-1]  # type: ignore
    bits, colors = f.read(2)  # type: ignore

    # Get the number of channels and dtype based on bits and colors:
    if colors == 0:
        if bits == 1:
            typstr = "|b1"
        elif bits == 16:
            typstr = _NATIVE_INT32
        else:
            typstr = "|u1"
        nlayers = None
    else:
        typstr = "|u1"
        if colors == 2:
            nlayers = 3
        elif colors == 3:
            nlayers = None
        elif colors == 4:
            if bits == 8:
                nlayers = 2
            else:
                nlayers = 4
        else:
            nlayers = 4
    shape = size if nlayers is None else size + (nlayers,)
    return shape, typstr  # type: ignore


def _decompress_audio(
    file: Union[bytes, memoryview, str], compression: Optional[str]
) -> np.ndarray:
    if not _MINIAUDIO_INSTALLED:
        raise ModuleNotFoundError(
            "Miniaudio is not installed. Run `pip install hub[audio]`."
        )
    decompressor = globals()[
        f"{compression}_read{'_file' if isinstance(file, str) else ''}_f32"
    ]
    if isinstance(file, memoryview):
        if (
            isinstance(file.obj, bytes)
            and file.strides == (1,)
            and file.shape == (len(file.obj),)
        ):
            file = file.obj
        else:
            file = bytes(file)
    raw_audio = decompressor(file)
    return np.frombuffer(raw_audio.samples, dtype="<f4").reshape(
        raw_audio.num_frames, raw_audio.nchannels
    )


def _read_audio_meta(file: Union[bytes, memoryview, str], compression: str) -> dict:
    if not _MINIAUDIO_INSTALLED:
        raise ModuleNotFoundError(
            "Miniaudio is not installed. Run `pip install hub[audio]`."
        )
    f_info = globals()[
        f"{compression}_get{'_file' if isinstance(file, str) else ''}_info"
    ]
    info = f_info(file)
    return info.__dict__


def _read_audio_shape(
    file: Union[bytes, memoryview, str], compression: str
) -> Tuple[int, ...]:
    if not _MINIAUDIO_INSTALLED:
        raise ModuleNotFoundError(
            "Miniaudio is not installed. Run `pip install hub[audio]`."
        )
    f_info = globals()[
        f"{compression}_get{'_file' if isinstance(file, str) else ''}_info"
    ]
    info = f_info(file)
    return (info.num_frames, info.nchannels)


def _frame_to_stamp(nframe, stream):
    """Convert frame number to timestamp based on fps of video stream."""
    fps = stream.guessed_rate.numerator / stream.guessed_rate.denominator
    seek_target = nframe / fps
    stamp = math.floor(
        seek_target * (stream.time_base.denominator / stream.time_base.numerator)
    )
    return stamp


def _open_video(file: Union[str, bytes, memoryview]):
    if not _PYAV_INSTALLED:
        raise ModuleNotFoundError(
            "PyAV is not installed. Run `pip install hub[video]`."
        )
    if isinstance(file, str):
        container = av.open(
            file, options={"protocol_whitelist": "file,http,https,tcp,tls,subfile"}
        )
    else:
        container = av.open(BytesIO(file))

    vstreams = container.streams.video

    if len(vstreams) == 0:
        raise IndexError("No video streams available!")

    vstream = vstreams[0]

    return container, vstream


def _read_metadata_from_vstream(container, vstream):
    duration = vstream.duration
    if duration is None:
        duration = container.duration
        time_base = 1 / av.time_base
    else:
        time_base = vstream.time_base.numerator / vstream.time_base.denominator
    fps = vstream.guessed_rate.numerator / vstream.guessed_rate.denominator

    nframes = vstream.frames
    if nframes == 0:
        nframes = math.floor(fps * duration * time_base)

    height = vstream.codec_context.height
    width = vstream.codec_context.width
    shape = (nframes, height, width, 3)

    return shape, duration, fps, time_base


def _read_video_shape(
    file: Union[str, bytes, memoryview],
):
    container, vstream = _open_video(file)
    shape = _read_metadata_from_vstream(container, vstream)[0]
    return shape


def _decompress_video(
    file: Union[str, bytes],
    start: Optional[int],
    stop: Optional[int],
    step: Optional[int],
    reverse: bool,
):
    container, vstream = _open_video(file)
    nframes, height, width, _ = _read_metadata_from_vstream(container, vstream)[0]

    if start is None:
        start = 0

    if stop is None:
        stop = nframes

    if step is None:
        step = 1

    nframes = math.ceil((stop - start) / step)

    video = np.zeros((nframes, height, width, 3), dtype=np.uint8)

    seek_target = _frame_to_stamp(start, vstream)
    step_time = _frame_to_stamp(step, vstream)

    gop_size = (
        vstream.codec_context.gop_size
    )  # gop size is distance (in frames) between 2 I-frames
    if step > gop_size:
        step_seeking = True
    else:
        step_seeking = False

    seekable = True
    try:
        container.seek(seek_target, stream=vstream)
    except av.error.PermissionError:
        seekable = False
        container, vstream = _open_video(file)  # try again but this time don't seek
        warning(
            "Cannot seek. Possibly a corrupted video file. Retrying with seeking disabled..."
        )

    i = 0
    for packet in container.demux(video=0):
        for frame in packet.decode():
            if packet.pts and packet.pts >= seek_target:
                arr = frame.to_ndarray(format="rgb24")
                video[i] = arr
                i += 1
                seek_target += step_time
                if step_seeking and seekable:
                    container.seek(seek_target, stream=vstream)

        if i == nframes:
            break

    if reverse:
        return video[::-1]
    return video
