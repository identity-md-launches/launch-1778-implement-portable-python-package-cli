"""Small dependency-free PPM/PNG image descriptors; hashes are fallback cues only."""

import struct
import zlib


def _ppm(data):
    # Read four tokens without consuming pixel bytes that happen to be whitespace.
    tokens, pos = [], 0
    while len(tokens) < 4:
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if data[pos:pos+1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while end < len(data) and data[end] not in b" \t\r\n":
            end += 1
        tokens.append(data[pos:end])
        pos = end
    if tokens[0] != b"P6" or tokens[3] != b"255":
        raise ValueError("only 8-bit binary PPM supported")
    pos += 2 if data[pos:pos+2] == b"\r\n" else 1
    width, height = int(tokens[1]), int(tokens[2])
    pixels = data[pos:]
    if len(pixels) != width * height * 3:
        raise ValueError("invalid PPM pixels")
    return width, height, 3, pixels


def _png(data):
    pos, compressed, header = 8, bytearray(), None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        kind, chunk = data[pos+4:pos+8], data[pos+8:pos+8+length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            compressed.extend(chunk)
        pos += length + 12
    if header is None:
        raise ValueError("missing PNG header")
    w, h, depth, color, compression, filtering, interlace = header
    if depth != 8 or color not in (0, 2, 4, 6) or interlace or compression or filtering:
        raise ValueError("unsupported PNG encoding")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    stride = w * channels
    if w * h > 32_000_000:
        raise ValueError("image too large")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, (stride+1)*h+1)
    if not decoder.eof:
        raise ValueError("invalid or oversized PNG data")
    if len(raw) != (stride+1)*h:
        raise ValueError("invalid PNG size")
    pixels, previous = bytearray(), bytearray(stride)
    for y in range(h):
        offset = y * (stride+1)
        mode = raw[offset]
        line = bytearray(raw[offset+1:offset+1+stride])
        for x in range(stride):
            a = line[x-channels] if x >= channels else 0
            b = previous[x]
            c = previous[x-channels] if x >= channels else 0
            if mode == 0:
                predictor = 0
            elif mode == 1:
                predictor = a
            elif mode == 2:
                predictor = b
            elif mode == 3:
                predictor = (a+b)//2
            elif mode == 4:
                p = a+b-c
                distances = (abs(p-a), abs(p-b), abs(p-c))
                predictor = (a, b, c)[distances.index(min(distances))]
            else:
                raise ValueError("invalid PNG filter")
            line[x] = (line[x] + predictor) & 255
        pixels.extend(line)
        previous = line
    return w, h, channels, bytes(pixels)


def descriptor(path):
    data = path.read_bytes()
    try:
        w, h, channels, pixels = _png(data) if data.startswith(b"\x89PNG\r\n\x1a\n") else _ppm(data)
    except (IndexError, struct.error, zlib.error, OverflowError) as exc:
        raise ValueError("unsupported or malformed image descriptor") from exc
    if w <= 0 or h <= 0:
        raise ValueError("empty image")
    result = []
    for y in range(8):
        for x in range(8):
            offset = (min(h-1, y*h//8)*w + min(w-1, x*w//8))*channels
            values = pixels[offset:offset+(1 if channels in (1, 2) else 3)]
            result.extend(values*3 if len(values) == 1 else values)
    return result


def difference(first, second):
    return sum(abs(a-b) for a, b in zip(first, second)) / (255 * len(first))
